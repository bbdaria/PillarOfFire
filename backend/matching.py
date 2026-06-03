"""Incident matching, clustering/merging, and incident-level severity.

A new analyzed call is compared against every existing incident's calls. We
score similarity across five signals (location, event type, time proximity,
semantic overlap, shared entities), combine them, and if the best incident
clears a threshold we LINK the call into it; otherwise we open a new incident.

Single-link clustering keeps it simple and explainable for a hackathon demo,
and the per-signal breakdown is surfaced in the UI so responders see WHY calls
were linked (never silently merged).
"""
from __future__ import annotations

import math
import re
from datetime import datetime
from typing import List, Optional, Tuple

from models import Call, Incident, Location, MatchScore, Severity
from store import store
from llm.mock_analyzer import score_severity

# Weights for combining the five similarity signals.
WEIGHTS = {
    "location": 0.30,
    "event_type": 0.20,
    "time": 0.15,
    "semantic": 0.20,
    "shared_entities": 0.15,
}
LINK_THRESHOLD = 0.55
TIME_WINDOW_MIN = 30.0  # calls within this window are considered time-proximate

# Hebrew stopwords to drop before computing transcript overlap.
STOPWORDS = {
    "אני", "יש", "של", "על", "זה", "הוא", "היא", "אבל", "כן", "לא", "מה",
    "אתם", "אותי", "הלו", "שלום", "בבקשה", "כל", "גם", "אז", "כי", "עם",
    "את", "הם", "אנחנו", "מאוד", "כבר", "פה", "שם", "או", "וגם",
}


def _tokens(text: str) -> set:
    words = re.findall(r"[֐-׿]+", text or "")
    return {w for w in words if w not in STOPWORDS and len(w) > 1}


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _haversine_km(a: Location, b: Location) -> Optional[float]:
    if None in (a.lat, a.lng, b.lat, b.lng):
        return None
    r = 6371.0
    dlat = math.radians(b.lat - a.lat)
    dlng = math.radians(b.lng - a.lng)
    h = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(a.lat)) * math.cos(math.radians(b.lat))
         * math.sin(dlng / 2) ** 2)
    return 2 * r * math.asin(math.sqrt(h))


def _location_sim(a: Location, b: Location) -> float:
    km = _haversine_km(a, b)
    if km is not None:
        # 1.0 at same spot, ~0 beyond ~2km.
        return max(0.0, 1.0 - km / 2.0)
    # No coords: compare normalized text token overlap.
    return _jaccard(_tokens(a.normalized or a.raw_text),
                    _tokens(b.normalized or b.raw_text))


def _time_sim(t1: str, t2: str) -> float:
    try:
        d1 = datetime.fromisoformat(t1)
        d2 = datetime.fromisoformat(t2)
    except Exception:
        return 0.5
    diff_min = abs((d1 - d2).total_seconds()) / 60.0
    return max(0.0, 1.0 - diff_min / TIME_WINDOW_MIN)


def score_pair(new: Call, other: Call) -> MatchScore:
    na, oa = new.analysis, other.analysis

    loc = _location_sim(na.location, oa.location)
    evt = 1.0 if (na.event_type == oa.event_type and na.event_type != "unknown") else (
        0.4 if "unknown" in (na.event_type, oa.event_type) else 0.0)
    tim = _time_sim(new.timestamp, other.timestamp)
    sem = _jaccard(_tokens(new.transcript), _tokens(other.transcript))

    entities_new = set(na.hazards) | _tokens(na.location.normalized)
    entities_other = set(oa.hazards) | _tokens(oa.location.normalized)
    ent = _jaccard(entities_new, entities_other)

    total = (WEIGHTS["location"] * loc + WEIGHTS["event_type"] * evt
             + WEIGHTS["time"] * tim + WEIGHTS["semantic"] * sem
             + WEIGHTS["shared_entities"] * ent)

    return MatchScore(call_id=other.call_id, total=round(total, 3),
                      location=round(loc, 3), event_type=round(evt, 3),
                      time=round(tim, 3), semantic=round(sem, 3),
                      shared_entities=round(ent, 3))


def best_incident_for(new: Call) -> Tuple[Optional[Incident], Optional[MatchScore]]:
    """Find the existing incident whose calls best match the new call."""
    best_inc, best_score = None, None
    for inc in store.active_incidents():
        for cid in inc.call_ids:
            other = store.get_call(cid)
            if not other or other.call_id == new.call_id:
                continue
            s = score_pair(new, other)
            if best_score is None or s.total > best_score.total:
                best_score, best_inc = s, inc
    return best_inc, best_score


# --- merging / incident assembly ------------------------------------------

HEB_EVENT = {"explosion": "פיצוץ", "fire": "שריפה", "traffic_accident": "תאונת דרכים",
             "medical": "אירוע רפואי", "hazmat": "חומ\"ס", "unknown": "אירוע"}


def _rebuild_merged_view(inc: Incident) -> None:
    """Rebuild the color-coded, per-source merged evidence for an incident."""
    fields = {
        "summary": [], "event_type": [], "location": [], "hazards": [],
        "casualties": [], "urgency_indicators": [], "distress_level": [],
        "missing_information": [], "suggested_questions": [],
    }
    locations: List[Location] = []
    questions, steps = [], []

    for cid in inc.call_ids:
        c = store.get_call(cid)
        if not c:
            continue
        a = c.analysis
        src = {"call_id": c.call_id, "color": c.color}
        fields["summary"].append({**src, "value": a.summary})
        fields["event_type"].append({**src, "value": a.event_type})
        if a.location.normalized or a.location.raw_text:
            fields["location"].append({**src, "value": a.location.normalized or a.location.raw_text})
        if a.location.lat is not None:
            locations.append(a.location)
        for h in a.hazards:
            fields["hazards"].append({**src, "value": h})
        cas = []
        if a.casualties.injured:
            cas.append(f"{a.casualties.injured} פצועים")
        if a.casualties.dead:
            cas.append(f"{a.casualties.dead} הרוגים")
        if cas:
            fields["casualties"].append({**src, "value": ", ".join(cas)})
        for u in a.urgency_indicators:
            fields["urgency_indicators"].append({**src, "value": u})
        fields["distress_level"].append({**src, "value": a.distress_level})
        for m in a.missing_information:
            fields["missing_information"].append({**src, "value": m})
        for q in a.suggested_questions:
            fields["suggested_questions"].append({**src, "value": q})
            if q not in questions:
                questions.append(q)
        for s in a.recommended_next_steps:
            if s not in steps:
                steps.append(s)

    inc.merged = fields
    inc.locations = _dedupe_locations(locations)
    inc.recommended_next_steps = steps


def _dedupe_locations(locs: List[Location]) -> List[Location]:
    seen, out = set(), []
    for loc in locs:
        key = (round(loc.lat, 4), round(loc.lng, 4))
        if key not in seen:
            seen.add(key)
            out.append(loc)
    return out


def _aggregate_event_type(inc: Incident) -> str:
    counts = {}
    for cid in inc.call_ids:
        c = store.get_call(cid)
        if c and c.analysis.event_type != "unknown":
            counts[c.analysis.event_type] = counts.get(c.analysis.event_type, 0) + 1
    return max(counts, key=counts.get) if counts else "unknown"


def _aggregate_severity(inc: Incident) -> Severity:
    """Re-score the incident using the union of evidence across linked calls."""
    hazards, injured, dead, distress = set(), 0, 0, "calm"
    rank = {"calm": 0, "concerned": 1, "distressed": 2, "panicked": 3}
    for cid in inc.call_ids:
        c = store.get_call(cid)
        if not c:
            continue
        a = c.analysis
        hazards |= set(a.hazards)
        injured = max(injured, a.casualties.injured or 0)
        dead = max(dead, a.casualties.dead or 0)
        if rank.get(a.distress_level, 0) > rank.get(distress, 0):
            distress = a.distress_level
    from models import Casualties
    cas = Casualties(injured=injured or None, dead=dead or None,
                     unknown=(injured == 0 and dead == 0))
    return score_severity(_aggregate_event_type(inc), list(hazards), cas,
                          distress, num_calls=len(inc.call_ids))


def assemble_incident(inc: Incident) -> None:
    """Recompute every derived field of an incident after its calls change."""
    inc.event_type = _aggregate_event_type(inc)
    _rebuild_merged_view(inc)
    inc.severity = _aggregate_severity(inc)
    # Title from the richest call summary, or the event type.
    best = max((store.get_call(c) for c in inc.call_ids if store.get_call(c)),
               key=lambda c: len(c.transcript), default=None)
    if best and best.analysis.location.normalized:
        inc.title = f"{HEB_EVENT.get(inc.event_type, inc.event_type)} - {best.analysis.location.normalized}"
    else:
        inc.title = HEB_EVENT.get(inc.event_type, inc.event_type)
