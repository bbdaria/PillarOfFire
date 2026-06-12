# 🔥 Pillar of Fire · עמוד האש

An emergency-response system for the Israeli **100** call center (police), in two parts:

1. **Dashboard** — a hierarchical situational picture. Hebrew calls are transcribed
   in real time (**ivrit-ai** STT), structured by an **LLM** (Llama), clustered when
   multiple calls describe the **same event**, and routed through a command hierarchy
   (call-taker → dispatcher → command center).
2. **Auto-Operator** — an automated overflow agent. When human dispatchers are full,
   Twilio routes calls to our number; the system holds a short **Hebrew conversation**,
   transcribes the caller **live** with ivrit via Twilio Media Streams, and drops the
   event straight into the dashboard.

> **Decision-support only.** The system assists human responders — it does not
> replace human judgement or official emergency protocols.

Hackathon 2026.

---

## Quick start

```bash
./run.sh
# then open http://127.0.0.1:8000
```

First run creates a virtualenv, installs dependencies, and (with the default
`STT_ENGINE=ivrit`) downloads the ivrit-ai Hebrew model (~1.6 GB, cached under
`~/.cache/huggingface`). Every real engine **falls back to an offline mock** if its
backend is unavailable, so the app always runs.

| Concern | Default | Needs |
|---------|---------|-------|
| **STT** | `ivrit` (ivrit-ai `whisper-large-v3-turbo-ct2`, faster-whisper, CPU) | one-time model download |
| **LLM** | `llama` (OpenAI-compatible endpoint, default Ollama) | `ollama serve` + `ollama pull llama3.1` |
| **Voice** | Twilio Auto-Operator | a Twilio number + a public tunnel (e.g. `ngrok http 8000`) |

Set `STT_ENGINE=mock` / `LLM_ENGINE=mock` to force the offline mocks.

---

## The dashboard — a command hierarchy

A **role switcher** (top bar) moves between the three tiers of the 100; each is its
own view. A contextual person picker chooses *who* you are within a role.

### 1. מוקדנית — call-taker
Your personal workspace of incident cards + the shared situational map.
- **⬆ Upload a recording** (or an Auto-Operator call) opens an incident; the transcript
  streams in live and the LLM fills in a summary, severity, tags, location and casualties.
- Relatedness surfaces as a **merge suggestion (⚠)** — possibly to an incident handled
  by someone else. You approve or reject; approving unifies them while preserving
  **per-call provenance** (hover any fact to see its source call).
- **Forward an event** to the **least-busy משגר** (automatic load-balancing), and
  override its **priority**.

### 2. משגר — dispatcher (takes action)
A queue of events forwarded to *you*, shown as decision-ready summaries. For each:
- **Dispatch resources** — 🚑 אמבולנס / 🚒 כבאית / 🚓 משטרה. Each button is a **toggle**
  (press again to cancel); pressing twice never double-sends.
- **Advance the status** — חדש → הועבר → בטיפול → טופל.
- **Override priority**.

### 3. מצודה — command & control
A **table-first operational overview** of *all* events across the system:
- KPI strip (total / active / handled / injured / dead estimates),
- **filter** (all / active / critical) + a **sortable** all-events table,
- side rail with severity & event-type **charts** and a **mini map**.

The **map** is shared/global: one marker per incident, **color = severity**,
**size = number of merged calls**.

---

## The Auto-Operator — automated overflow intake (Twilio voice)

When the 100 is overwhelmed, the PBX routes calls to our Twilio number. The system:

1. **Answers** and opens a live incident in the dashboard immediately.
2. Holds a short **Hebrew conversation** — *"מה שמך?"* → *"מאיפה אתה מתקשר?"* →
   *"מה קרה? … וציין אם יש נפגעים וכמה."* Twilio's `<Gather>` detects end-of-speech
   automatically (no key press); prompts are pre-synthesized Hebrew audio (`<Play>`),
   since Twilio has no Hebrew TTS.
3. **Transcribes live** — Twilio **Media Streams** forks the caller's audio to a
   WebSocket (`/voice/stream`); our **ivrit** STT transcribes each answer and the
   event's transcript fills in **during the call**.
4. **Triages** the transcript for critical Hebrew keywords (`ירי`, `פצוע`, `מחבל`, …)
   → flags severity, runs the LLM, and the event is ready for a human dispatcher.

**Set-up:** point your Twilio number's *Voice → "A call comes in"* webhook (POST) at
`https://<your-tunnel>/voice/incoming`, run `ngrok http 8000`, and call the number.
No Twilio API credentials are needed — the audio arrives directly over the WebSocket.

Configured by `voice.py` (prompts, triage, caller-repeat tracking, TwiML). Pre-rendered
prompt WAVs live in `backend/voice_audio/` and are regenerated (macOS `say -v Carmit`)
whenever the prompt text changes.

---

## Known Large Events — contextual intelligence layer

Police/EMS often know about large planned gatherings (concerts, demos, games,
religious/private mass-events). The system keeps these as a **subtle, second map
layer** and only makes them prominent when an emergency lands near/inside one —
the Nova/Re'im 7.10 lesson, operationalized.

Three concepts are kept distinct:

1. **Emergency incident** — an active emergency detected from calls (dominant).
2. **Known large event** — a planned, pre-entered gathering (calm background).
3. **Event-context alert** — "an emergency incident is near/inside a known event".

What you can do:

- **📅 Known Events Calendar** (topbar, מוקדנית role) — list view grouped by day, with
  filters (area, date range, type, status, participants) and free-text search.
- **＋ אירוע חדש** — manually create one event (enter `lat/lng`, or an address resolved
  by the geocoder).
- **⬆ ייבוא Excel/CSV** — upload `.csv`/`.xlsx`, see validated rows + per-row errors in a
  **preview**, then confirm. `.xlsx` is parsed with a stdlib-only reader (no
  `openpyxl`/`pandas`).
- On the shared map, known events render as **translucent dashed slate** circles
  (radius = event area), tinting amber only on an active alert.
- Inside an incident drawer, a calm **context card** appears when the incident is
  near/inside a time-relevant event (name, participants, distance, time window,
  police/risk notes, a cautious operational *consideration*).

### Matching logic (`match_incident_to_known_events`)

Each incident with coordinates is scored against every known event on **distance**
(haversine) and **time relevance**:

| Output | Meaning |
|--------|---------|
| `relation` | `inside` (≤ radius) or `nearby` (≤ radius + `KE_PROXIMITY_METERS`, default 800 m) |
| `time_relation` | `active` / `starting_soon` (≤12 h) / `recently_ended` (≤6 h) / `scheduled` |
| `alert_level` | `critical` (inside + active + ≥1000 participants), `important`, or `info` |

Only spatially-close **and** time-relevant events become alerts. Thresholds are
env-configurable (`KE_PROXIMITY_METERS`, `KE_STARTING_SOON_HOURS`,
`KE_RECENTLY_ENDED_HOURS`, `KE_MASS_PARTICIPANTS`).

---

## How call-clustering works

A newly analyzed incident is scored against every other open incident across five
weighted signals. If the best score clears `0.55` a **merge suggestion** is raised
(never an automatic merge):

| Signal | Weight | How |
|--------|--------|-----|
| Location similarity | 0.30 | haversine distance (or normalized-text overlap) |
| Event-type match | 0.20 | exact event type, partial credit for `unknown` |
| Time proximity | 0.15 | within a 30-minute window |
| Semantic similarity | 0.20 | Hebrew token Jaccard over transcripts |
| Shared entities | 0.15 | overlap of hazards + location tokens |

The full per-signal breakdown is shown in the incident drawer, so responders see
**why** a merge was suggested — nothing is merged silently.

---

## Architecture

```
backend/
  app.py            FastAPI: dashboard API + Auto-Operator voice + serves frontend
  voice.py          Twilio voice agent: TwiML, prompts, keyword triage, caller tracking
  voice_audio/      pre-synthesized Hebrew prompt WAVs (greeting, questions, closing)
  models.py         Pydantic schemas (the structured JSON contract)
  store.py          In-memory store; seeds the role hierarchy (moked/meshager/hamal)
  matching.py       Similarity scoring, clustering/merging, incident severity
  known_events.py   Known-event helpers, geocoding (Nominatim + city gazetteer),
                    CSV/XLSX import, match_incident_to_known_events
  demo_data.py      Hebrew location gazetteer (used by the mock analyzer)
  demo_known_events.py  seeded known large events (incl. the Nova/Re'im demo)
  stt/              Speech-to-text abstraction (chosen by STT_ENGINE)
    base.py           STTEngine interface
    ivrit_stt.py      ivrit-ai Hebrew model via faster-whisper (default)
    mock_stt.py       inert stub fallback
  llm/              Analysis abstraction (chosen by LLM_ENGINE)
    base.py           Analyzer interface
    llama_analyzer.py Llama via an OpenAI-compatible endpoint, e.g. Ollama (default)
    claude_analyzer.py Anthropic Claude analyzer (optional)
    mock_analyzer.py  rule-based Hebrew extractor (offline fallback)
frontend/
  index.html, style.css   role switcher, three views, Leaflet map
  app.js                  moked workspace, meshager queue, hamal overview, drawer
  known_events.js         known-events map layer, calendar, form, import, alert
```

Each layer (STT, LLM, clustering, voice, UI) is swappable in isolation.

### Pluggable engines

```bash
# STT — ivrit-ai Hebrew model (default). Streams real-time chunks from audio.
STT_ENGINE=ivrit  IVRIT_MODEL=ivrit-ai/whisper-large-v3-turbo-ct2 ./run.sh

# LLM — Llama via an OpenAI-compatible endpoint (default; Ollama).
ollama serve && ollama pull llama3.1
LLM_ENGINE=llama  LLAMA_BASE_URL=http://localhost:11434/v1  LLAMA_MODEL=llama3.1 ./run.sh

# LLM — Anthropic Claude (alternative):
export ANTHROPIC_API_KEY=sk-...
LLM_ENGINE=claude ./run.sh
```

The LLM is called **once per transcript** (and once more when calls merge) and returns
a compact JSON — summary, caller, tags, location, ambulance-needed, injured, severity —
kept small for speed. Addresses are geocoded street-level via OpenStreetMap **Nominatim**
(falling back to a city gazetteer offline).

---

## API

**Dashboard**

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/dispatchers` | list users (with `role`) |
| POST | `/api/upload` | open an incident from an uploaded audio file (multipart) |
| POST | `/api/incident/{id}/forward` | forward to the least-busy משגר `{by}` |
| POST | `/api/incident/{id}/status` | set workflow status `{status}` |
| POST | `/api/incident/{id}/dispatch` | toggle a resource `{resource, by}` |
| POST | `/api/incident/{id}/priority` | override priority `{label, by}` |
| POST | `/api/merge` | approve a merge `{suggestion_id}` or `{incident_a, incident_b}` |
| POST | `/api/suggestion/{id}/reject` | dismiss a merge suggestion |
| GET | `/api/state` | full snapshot (calls, incidents, users, suggestions, known events) — polled |
| POST | `/api/reset` | clear calls/incidents/suggestions (known events persist) |
| GET/POST | `/api/known-events` | list / create known large events |
| POST | `/api/known-events/import/preview` · `/confirm` | validate then insert a `.csv`/`.xlsx` |

**Auto-Operator (Twilio)**

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/voice/incoming` | open the incident, start the media stream, greet + ask Q1 |
| POST | `/voice/gather` | an answer finished → transcribe segment (ivrit) + next question |
| WS | `/voice/stream` | Twilio Media Streams: live caller audio → ivrit STT |
| GET | `/voice/audio/{clip}` | serve a pre-synthesized Hebrew prompt clip |

## Data model

| Entity | Key fields |
|--------|-----------|
| **Dispatcher** (user) | `dispatcher_id`, `name`, `color`, `role` (`moked`/`meshager`/`hamal`) |
| **Call** | `call_id`, `transcript`, `analysis`, `status`, `color`, `dispatcher_id`, `incident_id` |
| **CallAnalysis** | `summary`, `event_type`, `tags`, `caller`, `location`, `casualties`, `ambulance_needed`, `severity`, `date`/`time`, … |
| **Incident** | `title`, `severity`, `call_ids`, `dispatcher_ids`, `status` (open/merged), `workflow_status` (new→forwarded→in_progress→resolved), `assigned_meshager_id`, `dispatched[]`, `priority_override`, `narrative`, `locations` |
| **ResourceDispatch** | `resource` (ambulance/fire/police), `at`, `by` |
| **MergeSuggestion** | `incident_a`, `incident_b`, `score` (explainable), `status` |
| **KnownEvent** / **EventContextMatch** | planned gathering + its match to an incident (distance, relation, time-relation, alert level) |

Merging is **never automatic** — a suggestion is raised when similarity clears `0.55`,
and only a dispatcher's approval unifies the incidents.
