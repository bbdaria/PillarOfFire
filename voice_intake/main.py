"""FastAPI + Twilio voice-intake webhooks for the 100 overflow triage system.

Flow
  1. PBX overflows a call -> Twilio hits  POST /voice/incoming
       We answer in Hebrew, greet, and <Gather> the caller's speech.
  2. Twilio transcribes the speech and hits  POST /voice/process_speech
       We run triage (caller frequency + keyword priority), emit a clean JSON
       payload to the console (stand-in for the dispatch dashboard), then say a
       closing line and hang up.

This is a prototype: it uses Twilio's *native* speech gathering. The seam for
swapping in a custom Hebrew STT (via Twilio Media Streams) is the
/voice/process_speech handler — replace the `SpeechResult` source with your own
transcript and the rest of the pipeline is unchanged.

Run:
    pip install -r requirements.txt
    uvicorn main:app --reload --port 8001
    # expose to Twilio:  ngrok http 8001
    # point your Twilio number's Voice webhook at  https://<ngrok>/voice/incoming
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from fastapi import FastAPI, Form, Response
from twilio.twiml.voice_response import VoiceResponse, Gather

import config
from triage import CallerTracker, classify_priority, detect_keywords

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("voice_intake")

app = FastAPI(title="Pillar of Fire — Voice Intake")

# In-memory caller-frequency tracker. Swap for a Redis-backed CallerTracker in
# production (the interface — record_call() -> (count, is_repeat)) stays the same).
caller_tracker = CallerTracker()


def _twiml(response: VoiceResponse) -> Response:
    """Wrap a TwiML document in the XML response Twilio expects."""
    return Response(content=str(response), media_type="application/xml")


@app.post("/voice/incoming")
async def voice_incoming() -> Response:
    """Answer the overflow call: greet in Hebrew and gather the caller's reply.

    The <Gather> blocks until the caller finishes speaking (or times out), then
    Twilio POSTs the transcript to `action` (/voice/process_speech).
    """
    vr = VoiceResponse()

    # Gather speech; the greeting is nested INSIDE <Gather> so Twilio starts
    # listening while/just after it is spoken (caller can begin immediately).
    gather = Gather(
        input="speech",
        language=config.LANGUAGE,
        action="/voice/process_speech",
        method="POST",
        speech_timeout=config.SPEECH_TIMEOUT,
        timeout=config.GATHER_TIMEOUT,
    )
    gather.say(config.GREETING_TEXT, language=config.LANGUAGE, voice=config.VOICE)
    vr.append(gather)

    # Reached only if <Gather> heard nothing (caller silent / timed out).
    vr.say(config.NO_INPUT_TEXT, language=config.LANGUAGE, voice=config.VOICE)
    vr.hangup()

    return _twiml(vr)


@app.post("/voice/process_speech")
async def process_speech(
    CallSid: str = Form(""),
    From: str = Form(""),          # Twilio Caller ID
    SpeechResult: str = Form(""),  # Twilio's transcript of the caller's speech
    Confidence: str = Form(""),    # STT confidence (0..1), informational
) -> Response:
    """Triage the transcript and emit the structured dispatch payload.

    NOTE: to plug in your own STT later, replace `SpeechResult` here with the
    transcript produced by your Media Streams pipeline — nothing else changes.
    """
    caller_id = From or "unknown"
    transcript = (SpeechResult or "").strip()

    # 3. Caller-ID tracking — how many times has this number called recently?
    call_count, is_repeat_caller = caller_tracker.record_call(caller_id)

    # 4. Keyword triage — find danger words and set CRITICAL / STANDARD.
    matched_keywords = detect_keywords(transcript)
    priority = classify_priority(matched_keywords)

    # 5. Structured payload — this is what the dispatch dashboard consumes.
    payload = {
        "call_sid": CallSid,
        "caller_id": caller_id,
        "call_count": call_count,
        "is_repeat_caller": is_repeat_caller,
        "transcript": transcript,
        "priority": priority,
        "matched_keywords": matched_keywords,
        "confidence": Confidence,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    # Log cleanly to the console (stand-in for POSTing to Pillar of Fire's
    # /api/ingest). ensure_ascii=False keeps the Hebrew readable in the log.
    log.info(
        "DISPATCH PAYLOAD [%s]:\n%s",
        priority,
        json.dumps(payload, ensure_ascii=False, indent=2),
    )

    # 6. Close the call.
    vr = VoiceResponse()
    vr.say(config.CLOSING_TEXT, language=config.LANGUAGE, voice=config.VOICE)
    vr.hangup()
    return _twiml(vr)


@app.get("/health")
async def health() -> dict:
    """Liveness probe."""
    return {"ok": True}
