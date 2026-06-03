"""FastAPI app: API + static frontend on one port.

Endpoints
  POST /api/simulate/{call_id}   start streaming a demo call (real-time chunks)
  POST /api/simulate-all         launch the full demo scenario (staggered)
  GET  /api/state                full snapshot (calls + incidents) for polling
  GET  /api/demo-calls           list available demo calls
  POST /api/reset                clear all calls & incidents
  POST /api/ingest               ingest a raw transcript chunk (real intake hook)

The frontend polls /api/state; the simulation task appends transcript chunks
over time, which is what makes the transcript appear "live".
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from typing import Dict

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from models import Call, Incident
from store import store
from stt import get_stt_engine
from llm import get_analyzer
import matching
from demo_data import DEMO_CALLS

app = FastAPI(title="Pillar of Fire")

stt = get_stt_engine()
analyzer = get_analyzer()

CHUNK_DELAY_SEC = float(os.environ.get("CHUNK_DELAY_SEC", "1.1"))
_running: Dict[str, bool] = {}  # guard against double-launching the same call


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _link_call_into_incidents(call: Call) -> None:
    """Match the freshly analyzed call and link or open an incident."""
    inc, score = matching.best_incident_for(call)
    if inc and score and score.total >= matching.LINK_THRESHOLD:
        inc.call_ids.append(call.call_id)
        inc.match_scores.append(score)
        call.incident_id = inc.incident_id
    else:
        inc = Incident(
            incident_id=f"inc-{len(store.active_incidents()) + 1}",
            created_at=_now(),
            call_ids=[call.call_id],
            match_scores=[score] if score else [],
        )
        call.incident_id = inc.incident_id
    store.upsert_incident(inc)
    matching.assemble_incident(inc)
    store.upsert_call(call)


async def _simulate_call(call_id: str) -> None:
    spec = DEMO_CALLS.get(call_id)
    if not spec or _running.get(call_id):
        return
    _running[call_id] = True
    try:
        call = Call(call_id=call_id, timestamp=_now(),
                    color=store.next_color(), status="transcribing")
        store.upsert_call(call)

        # Stream chunks with a delay to simulate real-time transcription.
        for chunk in stt.stream_chunks(call_id):
            call.transcript = (call.transcript + " " + chunk).strip()
            call.analysis = analyzer.analyze(call.transcript)  # progressive extraction
            store.upsert_call(call)
            await asyncio.sleep(CHUNK_DELAY_SEC)

        call.status = "analyzed"
        store.upsert_call(call)
        _link_call_into_incidents(call)
    finally:
        _running[call_id] = False


@app.post("/api/simulate/{call_id}")
async def simulate(call_id: str):
    if call_id not in DEMO_CALLS:
        raise HTTPException(404, "unknown demo call")
    asyncio.create_task(_simulate_call(call_id))
    return {"ok": True, "call_id": call_id}


@app.post("/api/simulate-all")
async def simulate_all():
    async def runner():
        # Stagger launches so calls overlap, as in a real call surge.
        for i, cid in enumerate(DEMO_CALLS.keys()):
            asyncio.create_task(_simulate_call(cid))
            await asyncio.sleep(2.5)
    asyncio.create_task(runner())
    return {"ok": True, "calls": list(DEMO_CALLS.keys())}


class IngestChunk(BaseModel):
    call_id: str
    chunk: str
    final: bool = False


@app.post("/api/ingest")
async def ingest(body: IngestChunk):
    """Real intake hook: append a transcript chunk for an arbitrary call.

    This is the endpoint a live STT pipeline would call per partial result.
    """
    call = store.get_call(body.call_id)
    if call is None:
        call = Call(call_id=body.call_id, timestamp=_now(),
                    color=store.next_color(), status="transcribing")
    call.transcript = (call.transcript + " " + body.chunk).strip()
    call.analysis = analyzer.analyze(call.transcript)
    store.upsert_call(call)
    if body.final:
        call.status = "analyzed"
        store.upsert_call(call)
        _link_call_into_incidents(call)
    return {"ok": True}


@app.get("/api/demo-calls")
async def demo_calls():
    return [{"call_id": cid, "title": spec["title"]} for cid, spec in DEMO_CALLS.items()]


@app.get("/api/state")
async def state():
    return JSONResponse({
        "calls": [c.model_dump() for c in store.active_calls()],
        "incidents": [i.model_dump() for i in store.active_incidents()],
        "server_time": _now(),
    })


@app.post("/api/reset")
async def reset():
    store.reset()
    _running.clear()
    return {"ok": True}


# Serve the frontend (mounted last so it doesn't shadow /api routes).
_frontend = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend")
if os.path.isdir(_frontend):
    app.mount("/", StaticFiles(directory=_frontend, html=True), name="frontend")
