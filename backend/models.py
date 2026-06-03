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


class Call(BaseModel):
    call_id: str
    timestamp: str  # ISO datetime
    language: str = "he"
    color: str = "#888888"  # per-call provenance color
    status: str = "idle"  # idle | transcribing | analyzed
    transcript: str = ""
    analysis: CallAnalysis = Field(default_factory=CallAnalysis)
    incident_id: Optional[str] = None


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
    severity: Severity = Field(default_factory=Severity)
    # Merged evidence: field name -> list of {value, call_id} contributions.
    merged: Dict[str, list] = Field(default_factory=dict)
    locations: List[Location] = Field(default_factory=list)
    recommended_next_steps: List[str] = Field(default_factory=list)
    match_scores: List[MatchScore] = Field(default_factory=list)
