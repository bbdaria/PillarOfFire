"""Llama analyzer via an OpenAI-compatible chat endpoint.

Active when LLM_ENGINE=llama. Talks to any OpenAI-compatible chat-completions
server — Ollama (default, http://localhost:11434/v1), llama.cpp, vLLM, LM Studio,
Groq, Together, ... — configured by env:

  LLAMA_BASE_URL  (default http://localhost:11434/v1)
  LLAMA_MODEL     (default llama3.2)
  LLAMA_API_KEY   (default "ollama"; ignored by local servers)

Built for speed: ONE call per transcript, a tiny JSON response, low max_tokens,
temperature 0. The LLM only produces the genuinely semantic fields; everything
derivable (geocoding, hazard keys, severity scoring, next steps) reuses the
rule-based helpers in mock_analyzer. Any failure falls back to the mock analyzer
so the demo never breaks.
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Optional

import requests

log = logging.getLogger("pillar.llama")

from models import CallAnalysis, Casualties, Location, Severity
from llm.base import Analyzer
from llm.mock_analyzer import (
    MockAnalyzer, _find_event_type, _find_location, _find_hazards, _distress,
)
from known_events import geocode_precise  # street-level geocoding (Nominatim + fallback)

BASE_URL = os.environ.get("LLAMA_BASE_URL", "http://localhost:11434/v1").rstrip("/")
MODEL = os.environ.get("LLAMA_MODEL", "llama3.1")
API_KEY = os.environ.get("LLAMA_API_KEY", "ollama")
# Generous default: an 8B model on CPU can take 30-60s per analysis.
TIMEOUT = float(os.environ.get("LLAMA_TIMEOUT", "120"))

SYSTEM_PROMPT = (
    "אתה מנתח שיחות חירום למוקד בישראל. קבל תמלול והחזר אך ורק אובייקט JSON אחד "
    "(ללא טקסט נוסף, כל הערכים מחרוזות/מספרים פשוטים) עם המפתחות:\n"
    "- summary: משפט עברי קצר אחד המתאר מה קרה, היכן וכמה נפגעים. חובה למלא — לעולם לא ריק.\n"
    "- caller: מי מתקשר (אם לא ידוע, \"\").\n"
    "- tags: מערך תגיות קצרות (מה קרה).\n"
    "- location: המיקום כמחרוזת טקסט אחת.\n"
    "- ambulance_needed: true/false.\n"
    "- injured: מספר פצועים (שלם) או null.\n"
    "- dead: מספר הרוגים (שלם) או null.\n"
    "- hazards: מערך סכנות (אש/עשן/גז/...).\n"
    "- severity: חומרה 0 עד 10 (שלם).\n"
    "דוגמה: "
    '{"summary":"שריפה בבניין ברחוב הרצל תל אביב, שני לכודים","caller":"עובר אורח",'
    '"tags":["שריפה"],"location":"רחוב הרצל, תל אביב","ambulance_needed":true,'
    '"injured":2,"dead":null,"hazards":["אש","עשן"],"severity":8}'
)


def _label_for(score: int) -> str:
    if score >= 9:
        return "critical"
    if score >= 6:
        return "high"
    if score >= 4:
        return "medium"
    return "low"


class LlamaAnalyzer(Analyzer):
    def __init__(self) -> None:
        self._fallback = MockAnalyzer()
        self._warned = False  # log the fallback reason only once, not per call

    def warmup(self) -> None:
        """Preload the model at startup so the FIRST upload isn't cold (loading
        an 8B model into memory can exceed a single request's timeout). Also
        surfaces an unreachable endpoint clearly in the log."""
        try:
            t = time.time()
            r = requests.post(
                f"{BASE_URL}/chat/completions",
                headers={"Authorization": f"Bearer {API_KEY}"},
                json={"model": MODEL, "messages": [{"role": "user", "content": "היי"}],
                      "max_tokens": 1, "temperature": 0, "stream": False},
                timeout=max(TIMEOUT, 180),
            )
            r.raise_for_status()
            log.info("LLM=llama ready (%s, model=%s) — preloaded in %.0fs",
                     BASE_URL, MODEL, time.time() - t)
        except Exception as exc:
            log.warning("LLM=llama endpoint unreachable at %s (%s) — falling back to "
                        "the rule-based mock analyzer. Start Ollama and `ollama pull %s`, "
                        "or set LLAMA_BASE_URL.", BASE_URL, exc, MODEL)

    def analyze(self, transcript: str) -> CallAnalysis:
        data = self._call_llm(transcript or "")
        if data is None:
            analysis = self._fallback.analyze(transcript)  # endpoint down / bad output
        else:
            try:
                analysis = self._to_analysis(transcript or "", data)
            except Exception:
                log.exception("Llama response could not be mapped — using mock fallback")
                analysis = self._fallback.analyze(transcript)
        # Never surface the generic "unidentified event" — always describe the call.
        analysis.summary = _ensure_description(transcript or "", analysis)
        return analysis

    # --- LLM call ----------------------------------------------------------
    def _call_llm(self, transcript: str) -> Optional[dict]:
        try:
            r = requests.post(
                f"{BASE_URL}/chat/completions",
                headers={"Authorization": f"Bearer {API_KEY}"},
                json={
                    "model": MODEL,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": f"תמלול:\n{transcript}"},
                    ],
                    "temperature": 0,
                    "max_tokens": 300,
                    "response_format": {"type": "json_object"},
                    "stream": False,
                },
                timeout=TIMEOUT,
            )
            r.raise_for_status()
            content = r.json()["choices"][0]["message"]["content"]
            return json.loads(_extract_json(content))
        except Exception as exc:
            if not self._warned:
                log.warning("Llama call failed (%s) — using mock fallback. Is the "
                            "endpoint at %s up with model '%s'?", exc, BASE_URL, MODEL)
                self._warned = True
            return None

    # --- map compact JSON -> CallAnalysis (reusing rule-based helpers) ------
    def _to_analysis(self, transcript: str, d: dict) -> CallAnalysis:
        summary = _as_text(d.get("summary"))
        caller = _as_text(d.get("caller"))
        raw_tags = d.get("tags") or []
        if isinstance(raw_tags, str):
            raw_tags = [raw_tags]
        tags = [_as_text(t) for t in raw_tags if t]
        tags = [t for t in tags if t]
        # Derive a known event_type (keeps UI labels/colors/matching working).
        event_type = _find_event_type(" ".join(tags) + " " + summary + " " + transcript)

        # Location: trust the model's stated place name for the DISPLAY (it
        # captures the city correctly, e.g. חיפה), and geocode city-aware so the
        # map pin lands in the right city instead of a hardcoded street match.
        loc_text = _as_text(d.get("location"))
        if loc_text:
            coords = geocode_precise(loc_text)
            location = Location(raw_text=loc_text, normalized=loc_text,
                                lat=coords[0] if coords else None,
                                lng=coords[1] if coords else None,
                                confidence=0.7 if coords else 0.3)
        else:
            location = _find_location(transcript)  # last resort: street gazetteer

        injured = _as_int(d.get("injured"))
        dead = _as_int(d.get("dead"))
        casualties = Casualties(injured=injured, dead=dead,
                                unknown=(injured is None and dead is None))

        # Hazard KEYS (not free Hebrew) so the map/severity/matcher stay consistent.
        raw_haz = d.get("hazards") or []
        if isinstance(raw_haz, str):
            raw_haz = [raw_haz]
        hazards = _find_hazards(transcript + " " + " ".join(_as_text(h) for h in raw_haz))

        score = max(0, min(10, _as_int(d.get("severity")) or 0))
        severity = Severity(score=score, label=_label_for(score), reasoning="ניתוח LLM (Llama)")

        ambulance = bool(d.get("ambulance_needed")) or (injured is not None and injured > 0)

        return CallAnalysis(
            summary=summary,  # may be blank here; analyze() guarantees a description
            event_type=event_type,
            tags=tags,
            caller=caller,
            location=location,
            casualties=casualties,
            ambulance_needed=ambulance,
            hazards=hazards,
            people_involved=injured,
            distress_level=_distress(transcript),
            severity=severity,
        )


_EVENT_LABEL = {
    "explosion": "פיצוץ", "fire": "שריפה", "traffic_accident": "תאונת דרכים",
    "shooting": "ירי", "medical": "אירוע רפואי", "hazmat": 'חומרים מסוכנים',
}
# Generic phrases we refuse to show as a summary.
_GENERIC = {"", "אירוע לא מזוהה", "אירוע לא מזוהה."}


def _first_words(text: str, n: int = 14) -> str:
    words = (text or "").split()
    snippet = " ".join(words[:n]).strip(" .,:;!?-")
    return snippet


def _casualty_phrase(cas) -> str:
    parts = []
    if cas.injured:
        parts.append("פצוע אחד" if cas.injured == 1 else f"{cas.injured} פצועים")
    if cas.dead:
        parts.append("הרוג אחד" if cas.dead == 1 else f"{cas.dead} הרוגים")
    return ", ".join(parts)


def _ensure_description(transcript: str, a: CallAnalysis) -> str:
    """Return a human description that FOLLOWS the extracted JSON — never the
    generic 'unidentified event'. Uses the model's summary when it wrote one,
    otherwise composes from tags/event/location, and always reflects the injured/
    dead counts from the JSON so the summary stays consistent with the fields."""
    s = (a.summary or "").strip()
    if not s or s in _GENERIC:
        if a.tags:
            what = ", ".join(a.tags)
        elif a.event_type and a.event_type != "unknown":
            what = _EVENT_LABEL.get(a.event_type, a.event_type)
        else:
            what = _first_words(transcript)
        loc = (a.location.normalized or a.location.raw_text) if a.location else ""
        if not what:
            s = "המדווח מתאר סיטואציה לא ברורה"
        elif a.event_type and a.event_type != "unknown":
            s = what + (f" ב{loc}" if loc else "")
        else:
            s = f"המדווח מתאר סיטואציה של {what}" + (f" ב{loc}" if loc else "")

    # Make the summary follow the JSON: append casualties if not already stated.
    cas = _casualty_phrase(a.casualties)
    if cas and "פצוע" not in s and "הרוג" not in s:
        s = f"{s} · {cas}"
    return s


def _as_text(v) -> str:
    """Coerce an LLM field to text — models sometimes return dicts/lists where
    a string was asked for (e.g. location as {street, city})."""
    if v is None:
        return ""
    if isinstance(v, str):
        return v.strip()
    if isinstance(v, dict):
        return ", ".join(_as_text(x) for x in v.values() if x)
    if isinstance(v, (list, tuple)):
        return ", ".join(_as_text(x) for x in v if x)
    return str(v)


def _as_int(v) -> Optional[int]:
    try:
        if v is None or v is False:
            return None
        return int(v)
    except (TypeError, ValueError):
        return None


def _extract_json(text: str) -> str:
    start = text.find("{")
    end = text.rfind("}")
    return text[start: end + 1] if start != -1 and end != -1 else "{}"
