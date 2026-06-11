"""Pydantic schemas for calls and incidents.

These mirror the structured JSON contract from the product spec. They are the
single source of truth shared between the analyzer, the matcher and the API.
"""
from __future__ import annotations

from typing import List, Optional, Dict
from pydantic import BaseModel, Field


class Location(BaseModel):
    raw_text: str = ""
    normalized: str = ""
    lat: Optional[float] = None
    lng: Optional[float] = None
    confidence: float = 0.0


class Casualties(BaseModel):
    injured: Optional[int] = None
    dead: Optional[int] = None
    unknown: bool = True


class Severity(BaseModel):
    score: int = 1  # 1..10
    label: str = "low"  # low | medium | high | critical
    reasoning: str = ""


class CallAnalysis(BaseModel):
    """The structured extraction for a single call."""
    summary: str = ""
    event_type: str = "unknown"
    location: Location = Field(default_factory=Location)
    casualties: Casualties = Field(default_factory=Casualties)
    hazards: List[str] = Field(default_factory=list)
    people_involved: Optional[int] = None
    urgency_indicators: List[str] = Field(default_factory=list)
    distress_level: str = "unknown"  # calm | concerned | distressed | panicked
    missing_information: List[str] = Field(default_factory=list)
    suggested_questions: List[str] = Field(default_factory=list)
    severity: Severity = Field(default_factory=Severity)
    recommended_next_steps: List[str] = Field(default_factory=list)


class Dispatcher(BaseModel):
    """A person in the command hierarchy.

    role places them in the chain of escalation:
      moked    — call-taker (מוקדנית): takes calls, forwards events upward.
      meshager — dispatcher (משגר): acts on forwarded events (sends resources).
      hamal    — command center (חמ"ל): full overview + dashboards.
    """
    dispatcher_id: str
    name: str
    color: str = "#888888"  # identity tint (used sparingly, e.g. provenance)
    role: str = "moked"  # moked | meshager | hamal


class ResourceDispatch(BaseModel):
    """A resource the משגר sent to an incident (logged with who + when)."""
    resource: str  # ambulance | fire | police
    at: str        # ISO datetime
    by: Optional[str] = None  # meshager dispatcher_id


class Call(BaseModel):
    call_id: str
    timestamp: str  # ISO datetime
    language: str = "he"
    color: str = "#888888"  # per-call provenance color
    status: str = "idle"  # idle | transcribing | analyzed
    transcript: str = ""
    analysis: CallAnalysis = Field(default_factory=CallAnalysis)
    incident_id: Optional[str] = None
    dispatcher_id: Optional[str] = None  # who is handling this call


class MatchScore(BaseModel):
    """Explainable breakdown of why two calls were linked."""
    call_id: str
    total: float = 0.0
    location: float = 0.0
    event_type: float = 0.0
    time: float = 0.0
    semantic: float = 0.0
    shared_entities: float = 0.0


class Incident(BaseModel):
    incident_id: str
    created_at: str
    title: str = ""
    event_type: str = "unknown"
    call_ids: List[str] = Field(default_factory=list)
    dispatcher_ids: List[str] = Field(default_factory=list)  # moked owners (may be many after merge)
    status: str = "open"  # open | merged (merge lifecycle — independent of workflow_status)
    merged_into: Optional[str] = None  # if merged, the surviving incident id
    severity: Severity = Field(default_factory=Severity)
    # --- escalation workflow (moked -> meshager -> resolved) ---
    workflow_status: str = "new"  # new | forwarded | in_progress | resolved
    assigned_meshager_id: Optional[str] = None  # the משגר handling it
    forwarded_by: Optional[str] = None          # moked dispatcher_id who forwarded
    forwarded_at: Optional[str] = None           # ISO datetime
    dispatched: List[ResourceDispatch] = Field(default_factory=list)
    # A manual priority set by moked/meshager; overrides computed `severity` for
    # display and dashboards when present.
    priority_override: Optional[Severity] = None
    # Merged evidence: field name -> list of {value, call_id, dispatcher_id} contributions.
    merged: Dict[str, list] = Field(default_factory=dict)
    # Narrative summary as ordered segments. Each segment is
    # {"text": str, "sources": [{call_id, color, dispatcher_id, detail}]}.
    # Segments with sources are the hoverable, provenance-traced phrases.
    narrative: List[dict] = Field(default_factory=list)
    locations: List[Location] = Field(default_factory=list)
    recommended_next_steps: List[str] = Field(default_factory=list)
    match_scores: List[MatchScore] = Field(default_factory=list)


class MergeSuggestion(BaseModel):
    """A proposed (never automatic) merge between two incidents.

    incident_a is usually the newer incident; incident_b the existing candidate.
    Either may belong to a different dispatcher — merging is cross-dispatcher.
    """
    suggestion_id: str
    created_at: str
    incident_a: str
    incident_b: str
    score: MatchScore
    status: str = "pending"  # pending | approved | rejected


# --- Known Large Events (the pre-known intelligence layer) -----------------

class EventLocation(BaseModel):
    """Where a known large event takes place. radius_meters draws its area."""
    raw_address: str = ""
    normalized_address: str = ""
    lat: Optional[float] = None
    lng: Optional[float] = None
    radius_meters: int = 0


class KnownEvent(BaseModel):
    """A planned / pre-known large gathering (concert, demo, game, …).

    This is NOT an emergency incident. It is reference intelligence entered in
    advance (manually or via Excel import) so that, if an emergency is detected
    near it, the dispatcher gets proactive context.
    """
    id: str
    name: str = ""
    type: str = "other"  # political|cultural|private|religious|sports|festival|other
    description: str = ""
    expected_participants: int = 0
    start_time: str = ""  # ISO datetime
    end_time: str = ""    # ISO datetime
    location: EventLocation = Field(default_factory=EventLocation)
    organizer: str = ""
    police_notes: str = ""
    risk_notes: str = ""
    status: str = "scheduled"  # scheduled|active|ended|cancelled
    source: str = "manual"     # manual|excel_import


class EventContextMatch(BaseModel):
    """The result of matching ONE emergency incident to ONE known event.

    The first six fields are the matching contract from the spec; the rest are
    denormalized event details so the UI can render the alert without a second
    lookup. Only time-relevant matches (active / soon / recently ended) are
    surfaced as alerts — see matchIncidentToKnownEvents.
    """
    known_event_id: str
    distance_meters: int = 0
    relation: str = "nearby"        # inside | nearby
    time_relation: str = "scheduled"  # active | starting_soon | recently_ended | scheduled
    alert_level: str = "info"       # info | important | critical
    reason: str = ""
    # --- denormalized for display ---
    name: str = ""
    type: str = ""
    expected_participants: int = 0
    start_time: str = ""
    end_time: str = ""
    organizer: str = ""
    police_notes: str = ""
    risk_notes: str = ""
    suggestion: str = ""
    lat: Optional[float] = None
    lng: Optional[float] = None
