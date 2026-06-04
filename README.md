# 🔥 Pillar of Fire · עמוד האש


A first-responder dashboard that listens to incoming **Hebrew** emergency calls,
transcribes them in real time, extracts structured incident details with an LLM,
detects when multiple calls describe the **same event**, **clusters/merges**
related calls into one incident, and presents the combined picture to responders.

> **Decision-support only.** The system assists human responders — it does not
> replace human judgement or official emergency protocols.

Hackathon 2026.

---

## Quick start

```bash
./run.sh
# then open http://127.0.0.1:8000
```

First run creates a virtualenv and installs FastAPI/uvicorn/pydantic. No API keys
or GPU needed — the demo runs **fully offline**.

### Workspace model

The UI is a **per-dispatcher workspace**, not a control-room dashboard:

- A **dispatcher switcher** (top-right) chooses whose workspace you're in. You
  see only **your** open incidents as calm cards.
- Every finalized call opens **its own incident card** — nothing is merged
  automatically.
- Relatedness surfaces as a **merge suggestion** (⚠) — which can point to an
  incident owned by **another dispatcher**. You **approve** or **reject** it.
- Approving unifies the incidents into one **shared incident** visible in *all*
  involved dispatchers' workspaces, while preserving **per-call provenance**.
- Click a card → a **focused detail drawer** (transcript, structured summary,
  merge suggestions, next steps). Hover any extracted fact to see its **source
  call + dispatcher**.
- The **map is shared/global**: one marker per incident, **color = severity**,
  **size = number of merged calls**. Your own incidents get a white ring.
- **⬆ Upload a recording** creates a new incident through the same pipeline.

In the UI click **▶ הדמיית שיחות נכנסות** ("Simulate incoming calls").

## Demo scenario

| Call | Dispatcher | Content | Result |
|------|------------|---------|--------|
| `call-1` | דריה | Explosion at a gas station on Herzl St, Tel Aviv — 2 injured | **inc-1** |
| `call-2` | נועה | Fire / gas smell near the same gas station (different caller) | **inc-2** → suggests merge with inc-1 (**cross-dispatcher**) |
| `call-3` | נועה | Traffic accident on Route 6 near Hadera | separate **inc-3** |
| `call-4` | דריה | Noisy/partial call mentioning Herzl + smoke | **inc-4** → weak suggestion to inc-1 |

The headline interaction: `call-1` (Daria) and `call-2` (Noa) describe the same
event from two operators, producing a **cross-dispatcher merge suggestion**.

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

## How matching works

A newly analyzed incident is scored against every other open incident across
five signals (weighted). If the best score clears `0.55` a **merge suggestion**
is raised (never an automatic merge):

| Signal | Weight | How |
|--------|--------|-----|
| Location similarity | 0.30 | haversine distance (or normalized-text overlap) |
| Event-type match | 0.20 | exact event type, partial credit for `unknown` |
| Time proximity | 0.15 | within a 30-minute window |
| Semantic similarity | 0.20 | Hebrew token Jaccard over transcripts |
| Shared entities | 0.15 | overlap of hazards + location tokens |

The full per-signal breakdown is shown in the incident detail drawer, so
responders see **why** a merge was suggested — nothing is merged silently.

## API

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/dispatchers` | list operators (workspaces) |
| POST | `/api/simulate/{call_id}` | stream one demo call |
| POST | `/api/simulate-all` | launch the full scenario (staggered) |
| POST | `/api/upload` | create an incident from a "recorded" call `{dispatcher_id, filename}` |
| POST | `/api/merge` | approve a merge `{suggestion_id}` (or `{incident_a, incident_b}`) |
| POST | `/api/suggestion/{id}/reject` | dismiss a merge suggestion |
| POST | `/api/ingest` | ingest a real transcript chunk `{call_id, chunk, final, dispatcher_id}` |
| GET | `/api/state` | full snapshot (calls + incidents + dispatchers + suggestions) — polled |
| GET | `/api/demo-calls` | list available demo calls |
| POST | `/api/reset` | clear all calls, incidents & suggestions |

## Data model

| Entity | Key fields |
|--------|-----------|
| **Dispatcher** | `dispatcher_id`, `name`, `color` (identity tint) |
| **Call** | `call_id`, `transcript`, `analysis`, `color` (provenance), `dispatcher_id`, `incident_id` |
| **Incident** | `incident_id`, `title`, `severity`, `call_ids`, `dispatcher_ids` (owners), `status` (open/merged), `merged` (field → per-source contributions), `locations` |
| **MergeSuggestion** | `incident_a`, `incident_b`, `score` (explainable breakdown), `status` |

Merging is **never automatic** — a suggestion is raised when incident similarity
clears `0.55`, and only a dispatcher's approval unifies them.
=======

