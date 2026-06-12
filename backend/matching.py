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
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from models import Call, Incident, Location, MatchScore, Severity, MergeSuggestion
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

# Stopwords (Hebrew + English) dropped before computing transcript overlap.
STOPWORDS = {
    # Hebrew
    "אני", "יש", "של", "על", "זה", "הוא", "היא", "אבל", "כן", "לא", "מה",
    "אתם", "אותי", "הלו", "שלום", "בבקשה", "כל", "גם", "אז", "כי", "עם",
    "את", "הם", "אנחנו", "מאוד", "כבר", "פה", "שם", "או", "וגם",
    # English
    "the", "a", "an", "of", "at", "in", "on", "to", "is", "are", "was", "were",
    "and", "or", "there", "they", "we", "you", "it", "this", "that", "with",
    "for", "has", "have", "from", "by", "his", "her", "i",
}


def _tokens(text: str) -> set:
    # Hebrew (֐-׿) AND Latin words, so English/mixed transcripts also match.
    words = re.findall(r"[a-z֐-׿]+", (text or "").lower())
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
    # Best of geographic proximity and place-name overlap, so a strong text match
    # (e.g. both "faculty of computer science") still counts even when one call has
    # a full address and the other a partial one that geocoded elsewhere.
    km = _haversine_km(a, b)
    geo = max(0.0, 1.0 - km / 2.0) if km is not None else 0.0  # 1.0 same spot, ~0 >2km
    text = _jaccard(_tokens(a.normalized or a.raw_text),
                    _tokens(b.normalized or b.raw_text))
    return max(geo, text)


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

    # Semantic: best of raw-transcript overlap and the LLM's extracted tags
    # (tags are concise + normalized, so they match across phrasings/languages).
    def _tagset(a):
        return {t for tag in (a.tags or []) for t in _tokens(tag)}
    sem = max(_jaccard(_tokens(new.transcript), _tokens(other.transcript)),
              _jaccard(_tagset(na), _tagset(oa)))

    entities_new = set(na.hazards) | _tagset(na) | _tokens(na.location.normalized)
    entities_other = set(oa.hazards) | _tagset(oa) | _tokens(oa.location.normalized)
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
             "shooting": "אירוע ירי", "medical": "אירוע רפואי", "hazmat": "חומ\"ס", 
             "unknown": "אירוע", " terror_attack": "פיגוע", "flood": "הצפה", "earthquake": "רעידת אדמה", "landslide": "מפולת" }
HEB_HAZARD = {"gas": "גז", "smoke": "עשן", "fire": "אש",
              "explosion": "פיצוץ", "vehicle": "כלי רכב", "flood": "הצפה", "landslide": "מפולת"}
# How each hazard reads inside the narrative paragraph.
HAZARD_PHRASE = {"gas": "ריח גז חזק", "smoke": "עשן כבד", "fire": "אש",
                 "explosion": "חשש לפיצוץ", "vehicle": "כלי רכב מעורבים"}


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
        src = {"call_id": c.call_id, "color": c.color, "dispatcher_id": c.dispatcher_id}
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


def build_narrative(inc: Incident) -> None:
    """Compose a flowing summary paragraph as provenance-tagged segments.

    The paragraph reads as one description of the event; every fact-bearing
    phrase carries the list of calls that contributed it (with what each said),
    so the UI can reveal sources on hover. Plain connectors carry no sources.
    """
    calls = [c for c in (store.get_call(cid) for cid in inc.call_ids) if c]
    segs: list = []

    def src(c, detail: str) -> dict:
        return {"call_id": c.call_id, "color": c.color,
                "dispatcher_id": c.dispatcher_id, "detail": detail}

    def join(text: str) -> None:  # connector with no provenance
        segs.append({"text": text, "sources": []})

    def fact(text: str, sources: list) -> None:
        segs.append({"text": text, "sources": sources})

    if not calls:
        inc.narrative = []
        return

    # 1. event type + location
    event_he = HEB_EVENT.get(inc.event_type, "אירוע")
    ev_sources = [src(c, HEB_EVENT.get(c.analysis.event_type, c.analysis.event_type))
                  for c in calls if c.analysis.event_type != "unknown"]
    if not ev_sources:
        ev_sources = [src(c, "אירוע לא מזוהה") for c in calls]
    fact(f"דווח על {event_he}", ev_sources)

    loc_count: dict = {}
    loc_src: dict = {}
    for c in calls:
        name = c.analysis.location.normalized or c.analysis.location.raw_text
        if name:
            loc_count[name] = loc_count.get(name, 0) + 1
            loc_src.setdefault(name, []).append(src(c, name))
    if loc_count:
        loc_name = max(loc_count, key=loc_count.get)
        join(" ב")
        fact(loc_name, loc_src[loc_name])
    join(". ")

    # 2. casualties (range if calls disagree)
    def casualty_sentence(getter, noun: str, lead: str) -> None:
        items = [(c, getter(c)) for c in calls if getter(c)]
        if not items:
            return
        nums = [n for _, n in items]
        lo, hi = min(nums), max(nums)
        phrase = f"{lo} {noun}" if lo == hi else f"בין {lo} ל-{hi} {noun}"
        join(lead)
        fact(phrase, [src(c, f"{n} {noun}") for c, n in items])
        join(". ")

    casualty_sentence(lambda c: c.analysis.casualties.injured, "פצועים", "ישנם ")
    casualty_sentence(lambda c: c.analysis.casualties.dead, "הרוגים", "דווח על ")

    # 3. hazards (ordered by first appearance)
    haz_src: dict = {}
    for c in calls:
        for h in c.analysis.hazards:
            haz_src.setdefault(h, []).append(src(c, HEB_HAZARD.get(h, h)))
    if haz_src:
        join("בנוסף, מדווח על ")
        items = list(haz_src.items())
        for i, (h, sources) in enumerate(items):
            fact(HAZARD_PHRASE.get(h, h), sources)
            if i < len(items) - 1:
                join(" ו" if i == len(items) - 2 else ", ")
        join(".")

    inc.narrative = segs


def assemble_incident(inc: Incident) -> None:
    """Recompute every derived field of an incident after its calls change."""
    inc.event_type = _aggregate_event_type(inc)
    _rebuild_merged_view(inc)
    build_narrative(inc)
    inc.severity = _aggregate_severity(inc)
    # Owners = union of the dispatchers handling the linked calls (order-stable).
    owners: List[str] = []
    for cid in inc.call_ids:
        c = store.get_call(cid)
        if c and c.dispatcher_id and c.dispatcher_id not in owners:
            owners.append(c.dispatcher_id)
    inc.dispatcher_ids = owners
    # Title from the richest call summary, or the event type.
    best = max((store.get_call(c) for c in inc.call_ids if store.get_call(c)),
               key=lambda c: len(c.transcript), default=None)
    if best and best.analysis.location.normalized:
        inc.title = f"{HEB_EVENT.get(inc.event_type, inc.event_type)} - {best.analysis.location.normalized}"
    else:
        inc.title = HEB_EVENT.get(inc.event_type, inc.event_type)


# --- merge suggestions & cross-dispatcher merging -------------------------

def score_incident_pair(a: Incident, b: Incident) -> Optional[MatchScore]:
    """Best similarity between any call of A and any call of B (single-link)."""
    best: Optional[MatchScore] = None
    for ca in a.call_ids:
        new = store.get_call(ca)
        if not new:
            continue
        for cb in b.call_ids:
            other = store.get_call(cb)
            if not other or other.call_id == new.call_id:
                continue
            s = score_pair(new, other)
            if best is None or s.total > best.total:
                best = s
    return best


def suggest_merges_for(inc: Incident) -> List[MergeSuggestion]:
    """Compare a (new) incident against every other open incident and raise a
    pending merge suggestion wherever similarity clears the threshold.

    Suggestions are cross-dispatcher by nature: the candidate incident may be
    owned by a different operator. Nothing is merged here — only proposed.
    """
    created: List[MergeSuggestion] = []
    for other in store.active_incidents():
        if other.incident_id == inc.incident_id:
            continue
        # Skip pairs that already share a call (shouldn't happen, but safe).
        if set(inc.call_ids) & set(other.call_ids):
            continue
        if store.suggestion_between(inc.incident_id, other.incident_id):
            continue
        score = score_incident_pair(inc, other)
        if score and score.total >= LINK_THRESHOLD:
            sug = MergeSuggestion(
                suggestion_id=store.next_suggestion_id(),
                created_at=datetime.now(timezone.utc).isoformat(),
                incident_a=inc.incident_id,
                incident_b=other.incident_id,
                score=score,
            )
            store.upsert_suggestion(sug)
            created.append(sug)
    return created


def merge_incidents(survivor: Incident, absorbed: Incident) -> Incident:
    """Fold `absorbed` into `survivor`, preserving per-call provenance.

    Reassigns calls, unions owners and match scores, reassembles the merged
    view, and marks the absorbed incident as merged (kept for audit trail).
    """
    for cid in absorbed.call_ids:
        if cid not in survivor.call_ids:
            survivor.call_ids.append(cid)
        c = store.get_call(cid)
        if c:
            c.incident_id = survivor.incident_id
            store.upsert_call(c)
    survivor.match_scores.extend(absorbed.match_scores)

    absorbed.status = "merged"
    absorbed.merged_into = survivor.incident_id
    store.upsert_incident(absorbed)

    assemble_incident(survivor)
    store.upsert_incident(survivor)
    return survivor
