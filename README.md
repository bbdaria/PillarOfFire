# 🔥 Pillar of Fire · עמוד האש

A first-responder dashboard that listens to incoming **Hebrew** emergency calls,
transcribes them in real time, extracts structured incident details with an LLM,
detects when multiple calls describe the **same event**, **clusters/merges**
related calls into one incident, and presents the combined picture to responders.

> **Decision-support only.** The system assists human responders — it does not
> replace human judgement or official emergency protocols.

Hackathon 2026 MVP.

---

## Quick start

```bash
./run.sh
# then open http://127.0.0.1:8000
```

First run creates a virtualenv and installs FastAPI/uvicorn/pydantic. No API keys
or GPU needed — the demo runs **fully offline**.

In the UI click **▶ הדמה את כל השיחות** ("Simulate all calls"). You'll see:
1. Live Hebrew transcripts streaming in per call.
2. Structured details (event type, location, hazards, casualties, severity…) filling in.
3. Two calls being detected as the **same incident** and merged.
4. Color-coded evidence showing which call contributed which detail.
5. Severity and suggested next-steps updating after the merge.
6. Map pins for detected locations.

## Demo scenario

| Call | Content | Result |
|------|---------|--------|
| `call-1` | Explosion at a gas station on Herzl St, Tel Aviv — 2 injured | **inc-1** |
| `call-2` | Fire / gas smell near the same gas station (different caller) | linked → **inc-1** |
| `call-3` | Traffic accident on Route 6 near Hadera | separate **inc-2** |
| `call-4` | Noisy/partial call mentioning Herzl + smoke | weakly linked → **inc-1** |

## Architecture

```
backend/
  app.py            FastAPI: API + serves the frontend; real-time simulation
  models.py         Pydantic schemas (the structured JSON contract)
  store.py          In-memory store for active calls & incidents
  matching.py       Similarity scoring, clustering/merging, incident severity
  demo_data.py      4 prerecorded Hebrew calls + location gazetteer
  stt/              Speech-to-text abstraction
    base.py           STTEngine interface
    mock_stt.py       replays demo transcripts as timed chunks (default)
    ivrit_stt.py      ivrit-ai Hebrew model placeholder (faster-whisper)
  llm/              Analysis abstraction
    base.py           Analyzer interface
    mock_analyzer.py  rule-based Hebrew extractor (default, deterministic)
    claude_analyzer.py real Claude API analyzer (optional)
frontend/
  index.html, style.css, app.js   vanilla dashboard + Leaflet map
```

Each layer (STT, LLM analysis, clustering, UI) is swappable in isolation.

### Pluggable real models

```bash
# Real Hebrew STT via ivrit-ai (https://huggingface.co/ivrit-ai):
pip install faster-whisper
STT_ENGINE=ivrit ./run.sh          # then POST audio paths to /api/ingest

# Real LLM analysis via Claude:
pip install anthropic
export ANTHROPIC_API_KEY=sk-...
LLM_ENGINE=claude ./run.sh
```

Both fall back to the offline mock if the dependency/key is missing, so the demo
never breaks.

## How clustering works

A newly analyzed call is scored against existing incidents across five signals
(weighted), and linked if the best score clears `0.55`:

| Signal | Weight | How |
|--------|--------|-----|
| Location similarity | 0.30 | haversine distance (or normalized-text overlap) |
| Event-type match | 0.20 | exact event type, partial credit for `unknown` |
| Time proximity | 0.15 | within a 30-minute window |
| Semantic similarity | 0.20 | Hebrew token Jaccard over transcripts |
| Shared entities | 0.15 | overlap of hazards + location tokens |

The full per-signal breakdown is shown in the incident view, so responders see
**why** calls were linked — nothing is merged silently.

## API

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/simulate/{call_id}` | stream one demo call |
| POST | `/api/simulate-all` | launch the full scenario (staggered) |
| POST | `/api/ingest` | ingest a real transcript chunk `{call_id, chunk, final}` |
| GET | `/api/state` | full snapshot (calls + incidents) — the frontend polls this |
| GET | `/api/demo-calls` | list available demo calls |
| POST | `/api/reset` | clear all calls & incidents |

## Terminology

Related calls are **clustered / linked / merged** into a shared incident.
