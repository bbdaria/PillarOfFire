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
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
import shutil
from typing import Dict, Optional
import tempfile
from fastapi import UploadFile, File, Form


from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from models import Call, Incident
from store import store
from stt import get_stt_engine
from llm import get_analyzer
import matching
import known_events
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


def _finalize_call_into_incident(call: Call) -> None:
    """Finish a call's incident and propose any merges.

    Unlike the old auto-link behaviour, every call keeps its own incident card.
    Relatedness only ever surfaces as a pending, reviewable suggestion.
    """
    inc = _ensure_incident(call)
    matching.assemble_incident(inc)
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
            call.transcript = (call.transcript + " " + chunk).strip()
            call.analysis = analyzer.analyze(call.transcript)  # progressive extraction
            store.upsert_call(call)
            matching.assemble_incident(inc)  # keep the card in sync
            store.upsert_incident(inc)
            if CHUNK_DELAY_SEC:
                await asyncio.sleep(CHUNK_DELAY_SEC)
            else:
                await asyncio.sleep(0)  # yield so polls render the new text

        call.status = "analyzed"
        store.upsert_call(call)
        _finalize_call_into_incident(call)
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

   
    tmp_path = os.path.join(tempfile.gettempdir(), file.filename)
    with open(tmp_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    asyncio.create_task(_simulate_call(call_id, disp_id, script_key=tmp_path))
    
    return {"ok": True, "call_id": call_id, "filename": file.filename}
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
    matching.merge_incidents(survivor, absorbed)

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
