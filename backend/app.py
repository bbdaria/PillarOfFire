"""FastAPI app: API + static frontend on one port.

Model
  Each finalized call opens its OWN incident (one card per call). Relatedness is
  surfaced as a *merge suggestion* between incidents — never an automatic merge —
  and suggestions are cross-dispatcher: the candidate may belong to another
  operator. A dispatcher approves or rejects; on approval the incidents unify
  into one shared incident while preserving per-call provenance.

Endpoints
  GET  /api/dispatchers          list operators (workspaces)
  POST /api/upload               create an incident from a recorded audio file
  POST /api/merge                approve a merge (by suggestion or incident pair)
  POST /api/suggestion/{id}/reject   dismiss a merge suggestion
  GET  /api/state                full snapshot for polling
  POST /api/reset                clear all calls, incidents & suggestions
  POST /api/ingest               ingest a raw transcript chunk (real intake hook)
  POST /voice/incoming           Twilio: open incident + stream + full recording + Q1
  POST /voice/gather             Twilio: answered -> ivrit segment + next question
  WS   /voice/stream             Twilio Media Streams: live caller audio -> ivrit
  POST /voice/recording_status   Twilio: full call recorded -> ivrit whole-call STT
  GET  /voice/audio/{clip}       serve pre-synthesized Hebrew prompt audio
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
from datetime import datetime, timezone
import shutil
from typing import Dict, Optional
import tempfile
from fastapi import (UploadFile, File, Form, Request, Response, BackgroundTasks,
                     WebSocket, WebSocketDisconnect)


from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from models import Call, Incident, ResourceDispatch, Severity, Location
from store import store
from stt import get_stt_engine
from llm import get_analyzer
import matching
import known_events
import voice
from demo_known_events import seed_known_events

app = FastAPI(title="Pillar of Fire")

stt = get_stt_engine()
analyzer = get_analyzer()

# Seed the pre-known intelligence layer once at startup. These are reference
# data and persist across /api/reset (only the live call picture is cleared).
seed_known_events()

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("pillar")

# Real STT yields segments as fast as the model decodes them; no artificial
# pacing. (Default >0 only matters if a replaying engine is ever wired back in.)
CHUNK_DELAY_SEC = float(os.environ.get("CHUNK_DELAY_SEC", "0"))
DEFAULT_DISPATCHER = "d-daria"
_running: Dict[str, bool] = {}  # guard against double-launching the same call
_upload_seq = 0  # gives each uploaded recording a unique call id


@app.on_event("startup")
async def _warm_stt() -> None:
    """Load the STT model in the background so the first upload isn't stuck
    waiting on a multi-GB download + model load, and so config errors (bad
    model id, missing weights) surface in the server log immediately."""
    async def warm():
        try:
            log.info("warming STT engine (%s)…", type(stt).__name__)
            await asyncio.to_thread(stt.warmup)
            log.info("STT engine ready")
        except Exception:
            log.exception("STT warmup failed — uploads will report an error")
        # Probe the LLM analyzer too, so a missing Llama endpoint is obvious in
        # the log instead of silently degrading to the mock analyzer.
        try:
            log.info("checking LLM analyzer (%s)…", type(analyzer).__name__)
            await asyncio.to_thread(analyzer.warmup)
        except Exception:
            log.exception("LLM analyzer warmup failed")
        # Synthesize the Hebrew voice prompts (Twilio can't TTS Hebrew).
        try:
            await asyncio.to_thread(voice.ensure_audio)
            log.info("voice prompts ready (%s)", voice.AUDIO_DIR)
        except Exception:
            log.exception("could not generate Hebrew voice prompts")
    asyncio.create_task(warm())


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_incident(call: Call) -> Incident:
    """Get (or open) the call's own incident card. One card per call by default."""
    if call.incident_id:
        existing = store.get_incident(call.incident_id)
        if existing:
            return existing
    inc = Incident(
        incident_id=store.next_incident_id(),
        created_at=_now(),
        call_ids=[call.call_id],
        dispatcher_ids=[call.dispatcher_id] if call.dispatcher_id else [],
    )
    call.incident_id = inc.incident_id
    store.upsert_call(call)
    store.upsert_incident(inc)
    matching.assemble_incident(inc)
    return inc


def _set_time_date(analysis, iso_ts: str) -> None:
    """Stamp the call's date/time onto its analysis (from the call timestamp)."""
    try:
        dt = datetime.fromisoformat(iso_ts)
    except Exception:
        dt = datetime.now(timezone.utc)
    analysis.date = dt.strftime("%Y-%m-%d")
    analysis.time = dt.strftime("%H:%M")


def _reanalyze_incident(inc: Incident) -> None:
    """Set the incident's summary + severity from the LLM.

    One call: reuse that call's analysis (no extra LLM call). Multiple calls
    (i.e. after a merge): run the LLM ONCE on the combined transcript so the
    summary and severity reflect every linked call.
    """
    calls = [c for c in (store.get_call(cid) for cid in inc.call_ids) if c]
    if not calls:
        return
    if len(calls) == 1:
        a = calls[0].analysis
    else:
        combined = "\n".join(f"שיחה {i + 1}: {c.transcript}" for i, c in enumerate(calls))
        a = analyzer.analyze(combined)
    inc.severity = a.severity
    if a.summary:
        # Keep coarse provenance: the whole summary points back to its calls.
        sources = [{"call_id": c.call_id, "color": c.color,
                    "dispatcher_id": c.dispatcher_id, "detail": (c.analysis.summary or "")[:80]}
                   for c in calls]
        inc.narrative = [{"text": a.summary, "sources": sources}]
    if len(calls) > 1:
        _set_combined_location(inc, calls)


def _set_combined_location(inc: Incident, calls) -> None:
    """Several calls often give PARTIAL place names — a neighborhood in one, the
    city in another (each alone fails to geocode well). Combine the distinct place
    names (recognized cities last, so they anchor the query) and geocode the whole,
    making it the incident's primary location."""
    texts = []
    for c in calls:
        loc = c.analysis.location
        t = (loc.normalized or loc.raw_text or "").strip()
        if t and t not in texts:
            texts.append(t)
    if len(texts) < 2:
        return
    non_city = [t for t in texts if known_events.geocode(t) is None]
    city = [t for t in texts if known_events.geocode(t) is not None]
    query = ", ".join(non_city + city)
    coords = known_events.geocode_precise(query)
    if not coords:
        return
    loc = Location(raw_text=query, normalized=query, lat=coords[0], lng=coords[1], confidence=0.6)
    inc.locations = [loc] + [l for l in inc.locations
                             if l.lat is None or (round(l.lat, 4), round(l.lng, 4)) != (round(coords[0], 4), round(coords[1], 4))]
    inc.title = f"{matching.HEB_EVENT.get(inc.event_type, inc.event_type)} - {query}"


def _finalize_call_into_incident(call: Call) -> None:
    """Finish a call's incident and propose any merges.

    Unlike the old auto-link behaviour, every call keeps its own incident card.
    Relatedness only ever surfaces as a pending, reviewable suggestion.
    """
    inc = _ensure_incident(call)
    matching.assemble_incident(inc)
    _reanalyze_incident(inc)  # LLM-owned summary + severity (overrides rule-based)
    store.upsert_incident(inc)
    matching.suggest_merges_for(inc)


async def _simulate_call(call_id: str, dispatcher_id: str,
                         script_key: Optional[str] = None) -> None:
    """Transcribe a call's audio into its incident, streaming chunks live.

    The incident card is opened immediately so the live transcript streams into
    it; merge suggestions are computed once the call is fully analyzed.
    `script_key` is the audio file path for an uploaded recording.
    """
    script_key = script_key or call_id
    if _running.get(call_id):
        return
    _running[call_id] = True
    call = Call(call_id=call_id, timestamp=_now(),
                color=store.next_color(), status="transcribing",
                call_number=store.next_call_number(),
                dispatcher_id=dispatcher_id)
    store.upsert_call(call)
    inc = _ensure_incident(call)  # card appears right away
    try:
        # Pull chunks off the event loop: real STT (model load + decode) is
        # blocking, so iterate the generator in a worker thread. This keeps the
        # card visible and the transcript streaming in immediately instead of
        # freezing every poll until transcription finishes.
        chunks = stt.stream_chunks(script_key)
        _DONE = object()
        while True:
            chunk = await asyncio.to_thread(next, chunks, _DONE)
            if chunk is _DONE:
                break
            # Stream the raw transcript live; we do NOT analyze per chunk — the
            # LLM is called exactly once, on the full transcript, below.
            call.transcript = (call.transcript + " " + chunk).strip()
            store.upsert_call(call)
            if CHUNK_DELAY_SEC:
                await asyncio.sleep(CHUNK_DELAY_SEC)
            else:
                await asyncio.sleep(0)  # yield so polls render the new text

        # Transcription finished -> ONE LLM call on the complete transcript.
        call.status = "analyzing"
        store.upsert_call(call)
        call.analysis = await asyncio.to_thread(analyzer.analyze, call.transcript)
        _set_time_date(call.analysis, call.timestamp)
        call.status = "analyzed"
        store.upsert_call(call)
        await asyncio.to_thread(_finalize_call_into_incident, call)
    except Exception as exc:  # surface STT/model failures instead of hanging
        log.exception("transcription failed for %s", call_id)
        call.status = "error"
        note = f"[שגיאת תמלול: {exc}]"
        call.transcript = (call.transcript + " " + note).strip() if call.transcript else note
        store.upsert_call(call)
    finally:
        _running[call_id] = False


@app.get("/api/dispatchers")
async def dispatchers():
    return [d.model_dump() for d in store.active_dispatchers()]


@app.post("/api/upload")
async def upload(dispatcher_id: Optional[str] = Form(None), file: UploadFile = File(...)):
    """Create a new incident from a real recorded audio file."""
    global _upload_seq
    disp_id = dispatcher_id or DEFAULT_DISPATCHER

    _upload_seq += 1
    call_id = f"upload-{_upload_seq}"

   
    # Prefix with the unique call_id so parallel uploads of identically-named
    # files don't clobber each other on disk.
    tmp_path = os.path.join(tempfile.gettempdir(), f"{call_id}-{file.filename}")
    with open(tmp_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    asyncio.create_task(_simulate_call(call_id, disp_id, script_key=tmp_path))
    
    return {"ok": True, "call_id": call_id, "filename": file.filename}


# --- Auto-Operator: Twilio voice overflow intake (real-time ivrit STT) -----
# Twilio routes overflow 100 calls to our number. We open the incident the moment
# the call connects, hold a short Hebrew conversation (<Gather>, no key press),
# and — via Twilio Media Streams — fork the caller's live audio to a WebSocket
# where our ivrit STT transcribes it DURING the call, updating the dashboard live.

TWILIO_SID = os.environ.get("TWILIO_ACCOUNT_SID", "")
TWILIO_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "")

_voice_seq = 0
# call_sid -> {"ulaw": bytearray, "pos": int, "call_id": str}
_streams: Dict[str, Dict] = {}
# call_sid -> call_id, kept until the full-call recording is transcribed.
_voice_call_id_by_sid: Dict[str, str] = {}


def _open_voice_incident(caller_id: str) -> str:
    """Open an empty live Call + incident at the start of the call so it appears
    in the dashboard immediately and its transcript streams in. Returns call_id."""
    global _voice_seq
    _voice_seq += 1
    call = Call(call_id=f"voice-{_voice_seq}", timestamp=_now(),
                color=store.next_color(), status="transcribing",
                dispatcher_id=DEFAULT_DISPATCHER, transcript="")
    store.upsert_call(call)
    _ensure_incident(call)  # card appears right away
    return call.call_id


def _transcribe_ulaw(ulaw: bytes) -> str:
    """Transcribe raw 8 kHz μ-law telephony audio with our ivrit STT."""
    if not ulaw:
        return ""
    try:
        import audioop
        import wave
        pcm = audioop.ulaw2lin(ulaw, 2)  # -> 16-bit linear PCM, 8 kHz
        path = os.path.join(tempfile.gettempdir(), f"stream-{len(ulaw)}.wav")
        with wave.open(path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(8000)
            w.writeframes(pcm)
        return stt.transcribe(path).strip()
    except Exception:
        log.exception("μ-law transcription failed")
        return ""


def _transcribe_segment(call_sid: str) -> None:
    """Transcribe the caller audio captured since the last answer and append it to
    the live transcript (runs after each answered question)."""
    s = _streams.get(call_sid)
    if not s:
        return
    ulaw = bytes(s["ulaw"])
    new, s["pos"] = ulaw[s["pos"]:], len(ulaw)
    if len(new) < 4000:  # < ~0.5 s of audio — nothing meaningful
        return
    text = _transcribe_ulaw(new)
    if not text:
        return
    call = store.get_call(s["call_id"])
    if not call:
        return
    call.transcript = (call.transcript + " " + text).strip()
    store.upsert_call(call)
    inc = store.get_incident(call.incident_id)
    if inc:
        matching.assemble_incident(inc)
        store.upsert_incident(inc)
    log.info("live ivrit segment for %s -> %r", call_sid, text[:80])


def _finalize_stream_call(call_sid: str) -> None:
    """End of call: transcribe the final segment, then run the LLM + triage."""
    _transcribe_segment(call_sid)
    s = _streams.pop(call_sid, None)
    if not s:
        return
    call = store.get_call(s["call_id"])
    inc = store.get_incident(call.incident_id) if call else None
    if not (call and inc):
        return
    try:
        call.analysis = analyzer.analyze(call.transcript)
        _set_time_date(call.analysis, call.timestamp)
        call.status = "analyzed"
        store.upsert_call(call)
        matching.assemble_incident(inc)
        _reanalyze_incident(inc)
        if voice.triage(call.transcript)[0] == "CRITICAL":
            inc.priority_override = Severity(
                score=10, label="critical",
                reasoning="מילות מפתח קריטיות זוהו בשיחה אוטומטית")
        store.upsert_incident(inc)
        matching.suggest_merges_for(inc)
        log.info("voice call %s finalized: %r", call_sid, call.transcript[:100])
    except Exception:
        log.exception("voice finalize failed for %s", call_sid)
        call.status = "error"
        store.upsert_call(call)


def _start_full_recording(call_sid: str, public_base: str) -> None:
    """Record the WHOLE call — BOTH legs (operator prompts + caller). The full
    conversation is later transcribed by ivrit. Needs Twilio creds + a public URL."""
    if not (TWILIO_SID and TWILIO_TOKEN and call_sid and public_base):
        log.info("full-call recording skipped (missing creds/URL) for %s", call_sid)
        return
    try:
        import requests
        r = requests.post(
            f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_SID}/Calls/{call_sid}/Recordings.json",
            auth=(TWILIO_SID, TWILIO_TOKEN),
            data={"RecordingTrack": "both",
                  "RecordingStatusCallback": f"{public_base}/voice/recording_status",
                  "RecordingStatusCallbackEvent": "completed"},
            timeout=8,
        )
        if r.status_code >= 300:
            log.warning("start full recording failed (%s): %s", r.status_code, r.text[:200])
        else:
            log.info("started full-call recording for %s", call_sid)
    except Exception:
        log.exception("could not start full-call recording for %s", call_sid)


def _transcribe_recording(recording_url: str) -> str:
    """Download a Twilio recording (mixed both legs) and transcribe it with ivrit."""
    if not recording_url:
        return ""
    import requests
    import time
    auth = (TWILIO_SID, TWILIO_TOKEN) if (TWILIO_SID and TWILIO_TOKEN) else None
    wav_url = recording_url + ".wav"
    data, last = b"", None
    try:
        for _ in range(8):  # the recording may take a moment to finalize
            last = requests.get(wav_url, auth=auth, timeout=10)
            if last.status_code == 200 and last.content:
                data = last.content
                break
            time.sleep(1)
        if not data:
            log.warning("recording fetch failed for %s (status %s)",
                        wav_url, getattr(last, "status_code", "n/a"))
            return ""
        path = os.path.join(tempfile.gettempdir(), f"fullrec-{os.path.basename(recording_url)}.wav")
        with open(path, "wb") as f:
            f.write(data)
        text = stt.transcribe(path).strip()
        log.info("ivrit full-call transcript (%d bytes) -> %r", len(data), text[:100])
        return text
    except Exception:
        log.exception("full-call transcription failed")
        return ""


def _apply_full_transcript(call_sid: str, recording_url: str) -> None:
    """Full-call recording is ready: transcribe the ENTIRE call (operator + caller)
    with ivrit and make it the incident's transcript + re-run the analysis."""
    call_id = _voice_call_id_by_sid.pop(call_sid, None)
    if not call_id:
        return
    text = _transcribe_recording(recording_url)
    if not text:
        return
    call = store.get_call(call_id)
    inc = store.get_incident(call.incident_id) if call else None
    if not (call and inc):
        return
    try:
        call.transcript = text
        call.analysis = analyzer.analyze(text)
        _set_time_date(call.analysis, call.timestamp)
        call.status = "analyzed"
        store.upsert_call(call)
        matching.assemble_incident(inc)
        _reanalyze_incident(inc)
        store.upsert_incident(inc)
        matching.suggest_merges_for(inc)  # re-check relatedness on the full transcript
        log.info("incident for call %s set to full-call transcript", call_sid)
    except Exception:
        log.exception("full transcript apply failed for %s", call_sid)


@app.get("/voice/audio/{clip}")
async def voice_audio(clip: str):
    """Serve the pre-synthesized Hebrew prompt clips that Twilio <Play>s."""
    if not voice.is_clip(clip):
        raise HTTPException(404, "unknown clip")
    path = os.path.join(voice.AUDIO_DIR, clip)
    if not os.path.exists(path):
        raise HTTPException(404, "audio not generated")
    return FileResponse(path, media_type="audio/wav")


@app.post("/voice/incoming")
async def voice_incoming(request: Request, background_tasks: BackgroundTasks):
    """Twilio hits this when a call arrives: open the live incident, start the live
    media stream (caller audio -> ivrit, per-answer) AND a full-call recording (both
    legs -> ivrit at the end), then greet + ask Q1."""
    form = await request.form()
    caller_id = form.get("From", "")
    call_sid = form.get("CallSid", "")
    count, is_repeat = voice.record_call(caller_id)
    voice.start_session(call_sid, caller_id)
    call_id = _open_voice_incident(caller_id)
    _streams[call_sid] = {"ulaw": bytearray(), "pos": 0, "call_id": call_id}
    _voice_call_id_by_sid[call_sid] = call_id
    host = request.headers.get("host", "")
    stream_url = f"wss://{host}/voice/stream"
    log.info("voice incoming: From=%s sid=%s repeat=%s stream=%s",
             caller_id, call_sid, is_repeat, stream_url)
    # Record the entire call (both legs) for an authoritative ivrit transcript.
    background_tasks.add_task(_start_full_recording, call_sid, f"https://{host}" if host else "")
    return Response(content=voice.stream_then_question(stream_url, "/voice/gather"),
                    media_type="application/xml")


@app.post("/voice/recording_status")
async def voice_recording_status(request: Request, background_tasks: BackgroundTasks):
    """Twilio calls this when the full-call recording is ready -> ivrit transcribe
    the entire conversation (operator + caller) and set it as the transcript."""
    form = await request.form()
    call_sid = form.get("CallSid", "")
    recording_url = form.get("RecordingUrl", "")
    log.info("full recording ready: sid=%s url=%s", call_sid, recording_url)
    background_tasks.add_task(_apply_full_transcript, call_sid, recording_url)
    return Response(status_code=204)


@app.websocket("/voice/stream")
async def voice_stream(ws: WebSocket):
    """Twilio Media Stream: receive the caller's live μ-law audio and buffer it
    (per call). Transcription is triggered per-answer from /voice/gather."""
    await ws.accept()
    call_sid = None
    try:
        while True:
            data = json.loads(await ws.receive_text())
            event = data.get("event")
            if event == "start":
                call_sid = data["start"]["callSid"]
                log.info("media stream started for call %s", call_sid)
            elif event == "media" and call_sid:
                s = _streams.get(call_sid)
                if s is not None:
                    s["ulaw"].extend(base64.b64decode(data["media"]["payload"]))
            elif event == "stop":
                break
    except WebSocketDisconnect:
        pass
    except Exception:
        log.exception("media stream error (call %s)", call_sid)
    log.info("media stream closed for call %s", call_sid)


@app.post("/voice/gather")
async def voice_gather(request: Request, background_tasks: BackgroundTasks):
    """One turn: transcribe the answer just given (live ivrit) and ask the next
    question, or — when done — play the closing and finalize the analysis."""
    form = await request.form()
    call_sid = form.get("CallSid", "")
    nxt = voice.record_answer_and_next(call_sid, form.get("SpeechResult", "") or "")
    if nxt is not None:
        # Transcribe this answer's audio segment in the background (updates live).
        background_tasks.add_task(_transcribe_segment, call_sid)
        return Response(content=voice.question_twiml(nxt, "/voice/gather"),
                        media_type="application/xml")

    # Last answer -> transcribe remaining audio + run the LLM, then close the call.
    background_tasks.add_task(_finalize_stream_call, call_sid)
    voice.end_session(call_sid)
    return Response(content=voice.closing_twiml(), media_type="application/xml")


# --- Escalation workflow (moked -> meshager -> resolved) -------------------

def _get_incident_or_404(incident_id: str) -> Incident:
    inc = store.get_incident(incident_id)
    if not inc:
        raise HTTPException(404, "unknown incident")
    return inc


def _least_loaded_meshager() -> Optional[str]:
    """Pick the משגר with the fewest active (non-resolved) assigned incidents.

    This is the load-balancing target for forwarding: work goes to whoever is
    least busy. Ties break toward seed order (min keeps the first minimum).
    """
    meshagers = [d for d in store.active_dispatchers() if d.role == "meshager"]
    if not meshagers:
        return None
    load = {d.dispatcher_id: 0 for d in meshagers}
    for inc in store.active_incidents():
        mid = inc.assigned_meshager_id
        if mid in load and inc.workflow_status != "resolved":
            load[mid] += 1
    return min(meshagers, key=lambda d: load[d.dispatcher_id]).dispatcher_id


class ForwardBody(BaseModel):
    by: Optional[str] = None  # the moked who forwarded


@app.post("/api/incident/{incident_id}/forward")
async def forward_incident(incident_id: str, body: ForwardBody):
    """A מוקדנית forwards the event; it is auto-assigned to the least-busy משגר."""
    inc = _get_incident_or_404(incident_id)
    mid = _least_loaded_meshager()
    if not mid:
        raise HTTPException(400, "no meshager available")
    inc.assigned_meshager_id = mid
    inc.workflow_status = "forwarded"
    inc.forwarded_by = body.by
    inc.forwarded_at = _now()
    store.upsert_incident(inc)
    return {"ok": True, "assigned_meshager_id": mid}


_WORKFLOW_STATES = {"forwarded", "in_progress", "resolved", "escalated"}


class StatusBody(BaseModel):
    status: str


@app.post("/api/incident/{incident_id}/status")
async def set_incident_status(incident_id: str, body: StatusBody):
    """The משגר advances the event through its handling lifecycle."""
    if body.status not in _WORKFLOW_STATES:
        raise HTTPException(400, f"status must be one of {sorted(_WORKFLOW_STATES)}")
    inc = _get_incident_or_404(incident_id)
    inc.workflow_status = body.status
    store.upsert_incident(inc)
    return {"ok": True}


_RESOURCES = {"ambulance", "fire", "police"}


@app.post("/api/incident/{incident_id}/ack_review")
async def ack_review(incident_id: str):
    """The משגר acknowledges a post-merge re-review; clears the review flag."""
    inc = _get_incident_or_404(incident_id)
    inc.review_flag = False
    inc.review_reason = ""
    store.upsert_incident(inc)
    return {"ok": True}


@app.post("/api/incident/{incident_id}/escalate")
async def escalate_to_c2(incident_id: str):
    """The משגר escalates the event to חמ\"\u05dc (C2 command center)."""
    inc = _get_incident_or_404(incident_id)
    inc.escalated_to_c2 = True
    if inc.workflow_status not in ("resolved",):
        inc.workflow_status = "escalated"
    store.upsert_incident(inc)
    return {"ok": True}


class DispatchBody(BaseModel):
    resource: str
    by: Optional[str] = None  # the meshager dispatching


@app.post("/api/incident/{incident_id}/dispatch")
async def dispatch_resource(incident_id: str, body: DispatchBody):
    """Toggle a resource (ambulance/fire/police) on the event.

    Idempotent per resource type: if it's already sent, this removes it;
    otherwise it sends one. Pressing the same button twice never stacks.
    """
    if body.resource not in _RESOURCES:
        raise HTTPException(400, f"resource must be one of {sorted(_RESOURCES)}")
    inc = _get_incident_or_404(incident_id)
    already = any(d.resource == body.resource for d in inc.dispatched)
    if already:
        inc.dispatched = [d for d in inc.dispatched if d.resource != body.resource]
    else:
        inc.dispatched.append(ResourceDispatch(resource=body.resource, at=_now(), by=body.by))
        # Sending a resource means the event is actively being handled.
        if inc.workflow_status in ("new", "forwarded"):
            inc.workflow_status = "in_progress"
    store.upsert_incident(inc)
    return {"ok": True, "active": not already}


# Manual priority labels -> representative 1..10 score (matches Severity scale).
_PRIORITY_SCORE = {"low": 2, "medium": 5, "high": 8, "critical": 10}


class PriorityBody(BaseModel):
    label: str
    by: Optional[str] = None


@app.post("/api/incident/{incident_id}/priority")
async def override_priority(incident_id: str, body: PriorityBody):
    """A מוקדנית or משגר manually overrides the event's priority."""
    if body.label not in _PRIORITY_SCORE:
        raise HTTPException(400, f"label must be one of {sorted(_PRIORITY_SCORE)}")
    inc = _get_incident_or_404(incident_id)
    inc.priority_override = Severity(
        score=_PRIORITY_SCORE[body.label], label=body.label,
        reasoning=f"עדיפות נקבעה ידנית{f' ע״י {body.by}' if body.by else ''}")
    store.upsert_incident(inc)
    return {"ok": True}


class TranscriptBody(BaseModel):
    transcript: str


@app.post("/api/call/{call_id}/transcript")
async def edit_call_transcript(call_id: str, body: TranscriptBody):
    """Manually correct a call's transcript. Shared state -> every dashboard
    that shows this call updates on its next poll."""
    call = store.get_call(call_id)
    if not call:
        raise HTTPException(404, "unknown call")
    call.transcript = body.transcript
    store.upsert_call(call)
    return {"ok": True}


class SummaryBody(BaseModel):
    summary: str


@app.post("/api/incident/{incident_id}/summary")
async def edit_incident_summary(incident_id: str, body: SummaryBody):
    """Manually edit the auto-generated incident summary. Keeps existing source
    provenance but replaces the narrative text with one edited segment."""
    inc = _get_incident_or_404(incident_id)
    sources = []
    if inc.narrative and isinstance(inc.narrative, list):
        sources = inc.narrative[0].get("sources", []) if inc.narrative else []
    inc.narrative = [{"text": body.summary, "sources": sources}]
    store.upsert_incident(inc)
    return {"ok": True}


class MergeBody(BaseModel):
    suggestion_id: Optional[str] = None
    incident_a: Optional[str] = None
    incident_b: Optional[str] = None


@app.post("/api/merge")
async def merge(body: MergeBody):
    """Approve a merge — by suggestion id, or by an explicit incident pair."""
    if body.suggestion_id:
        sug = store.get_suggestion(body.suggestion_id)
        if not sug:
            raise HTTPException(404, "unknown suggestion")
        a = store.get_incident(sug.incident_a)
        b = store.get_incident(sug.incident_b)
        sug.status = "approved"
        store.upsert_suggestion(sug)
    else:
        a = store.get_incident(body.incident_a or "")
        b = store.get_incident(body.incident_b or "")
    if not a or not b or a.incident_id == b.incident_id:
        raise HTTPException(400, "two distinct open incidents required")

    # Survivor = the bigger incident; tie-break to the older one (stable id).
    survivor, absorbed = (a, b)
    if len(b.call_ids) > len(a.call_ids) or (
            len(b.call_ids) == len(a.call_ids) and b.created_at < a.created_at):
        survivor, absorbed = (b, a)
    # Carry forward escalation/assignment from either side so a merge never
    # "loses" an event that was already moving through the chain.
    if absorbed.assigned_meshager_id and not survivor.assigned_meshager_id:
        survivor.assigned_meshager_id = absorbed.assigned_meshager_id
        survivor.workflow_status = absorbed.workflow_status
    survivor.escalated_to_c2 = survivor.escalated_to_c2 or absorbed.escalated_to_c2

    matching.merge_incidents(survivor, absorbed)
    # Merge changed the call set -> refresh the LLM summary + severity once.
    await asyncio.to_thread(_reanalyze_incident, survivor)

    # If the combined event is already in a משגר's hands, flag it for re-review:
    # the picture changed and they may need to act differently.
    if survivor.assigned_meshager_id:
        survivor.review_flag = True
        survivor.review_reason = "האירוע אוחד עם אירוע נוסף — ייתכן שצריך לעדכן את הטיפול"
    store.upsert_incident(survivor)

    # Any pending suggestion touching the absorbed incident is now resolved.
    for s in store.pending_suggestions():
        if absorbed.incident_id in (s.incident_a, s.incident_b):
            s.status = "approved"
            store.upsert_suggestion(s)
    return {"ok": True, "incident_id": survivor.incident_id}


@app.post("/api/suggestion/{suggestion_id}/reject")
async def reject_suggestion(suggestion_id: str):
    sug = store.get_suggestion(suggestion_id)
    if not sug:
        raise HTTPException(404, "unknown suggestion")
    sug.status = "rejected"
    store.upsert_suggestion(sug)
    return {"ok": True}


class IngestChunk(BaseModel):
    call_id: str
    chunk: str
    final: bool = False
    dispatcher_id: Optional[str] = None


@app.post("/api/ingest")
async def ingest(body: IngestChunk):
    """Real intake hook: append a transcript chunk for an arbitrary call.

    This is the endpoint a live STT pipeline would call per partial result.
    """
    call = store.get_call(body.call_id)
    if call is None:
        call = Call(call_id=body.call_id, timestamp=_now(),
                    color=store.next_color(), status="transcribing",
                    call_number=store.next_call_number(),
                    dispatcher_id=body.dispatcher_id or DEFAULT_DISPATCHER)
    call.transcript = (call.transcript + " " + body.chunk).strip()
    call.analysis = analyzer.analyze(call.transcript)
    store.upsert_call(call)
    if body.final:
        call.status = "analyzed"
        store.upsert_call(call)
        _finalize_call_into_incident(call)
    return {"ok": True}


# --- Known Large Events (the pre-known intelligence layer) -----------------

@app.get("/api/known-events")
async def list_known_events():
    """All known large events, with live status recomputed from the clock."""
    return [known_events.refresh_status(e).model_dump()
            for e in store.all_known_events()]


class KnownEventBody(BaseModel):
    name: str
    type: str = "other"
    expected_participants: int = 0
    start_time: str = ""
    end_time: str = ""
    address: str = ""
    city: str = ""
    lat: Optional[float] = None
    lng: Optional[float] = None
    radius_meters: int = 0
    organizer: str = ""
    description: str = ""
    police_notes: str = ""
    risk_notes: str = ""


@app.post("/api/known-events")
async def create_known_event(body: KnownEventBody):
    """Manually create one known large event (the KnownEventForm submit)."""
    if not body.name.strip():
        raise HTTPException(400, "event name is required")
    evt = known_events.create_known_event(body.model_dump(), source="manual")
    store.upsert_known_event(evt)
    return {"ok": True, "event": evt.model_dump()}


class ImportPreviewBody(BaseModel):
    filename: str
    content_b64: str


@app.post("/api/known-events/import/preview")
async def import_preview(body: ImportPreviewBody):
    """Parse + validate an uploaded .csv/.xlsx WITHOUT inserting anything."""
    try:
        content = known_events.decode_upload(body.content_b64)
        return known_events.preview_import(body.filename, content)
    except Exception as e:  # malformed upload → clear, non-fatal error
        raise HTTPException(400, f"could not parse file: {e}")


class ImportConfirmBody(BaseModel):
    payloads: list


@app.post("/api/known-events/import/confirm")
async def import_confirm(body: ImportConfirmBody):
    """Insert the validated rows the user confirmed from the preview."""
    created = known_events.import_known_events(body.payloads)
    return {"ok": True, "imported": len(created),
            "events": [e.model_dump() for e in created]}


@app.get("/api/state")
async def state():
    # Attach per-incident known-event context matches (computed fresh so they
    # reflect the current time and the latest known events).
    incidents = []
    for inc in store.active_incidents():
        d = inc.model_dump()
        d["event_context"] = [m.model_dump()
                              for m in known_events.match_incident_to_known_events(inc)]
        incidents.append(d)
    return JSONResponse({
        "calls": [c.model_dump() for c in store.active_calls()],
        "incidents": incidents,
        "dispatchers": [d.model_dump() for d in store.active_dispatchers()],
        "suggestions": [s.model_dump() for s in store.pending_suggestions()],
        "known_events": [known_events.refresh_status(e).model_dump()
                         for e in store.all_known_events()],
        "server_time": _now(),
    })


@app.post("/api/reset")
async def reset():
    global _upload_seq
    store.reset()
    _running.clear()
    _upload_seq = 0
    return {"ok": True}


# Serve the frontend (mounted last so it doesn't shadow /api routes).
_frontend = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend")
if os.path.isdir(_frontend):
    app.mount("/", StaticFiles(directory=_frontend, html=True), name="frontend")
