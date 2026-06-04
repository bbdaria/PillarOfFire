"""In-memory storage for active calls, incidents, dispatchers and suggestions.

Hackathon-grade: a process-local singleton. Swap for Redis/DB later without
touching callers — they only use the public methods here.
"""
from __future__ import annotations

import threading
from typing import Dict, List, Optional

from models import Call, Incident, Dispatcher, MergeSuggestion, KnownEvent

# A fixed palette so each call gets a stable, distinguishable provenance color.
PALETTE = [
    "#e6194b",  # red
    "#3cb44b",  # green
    "#4363d8",  # blue
    "#f58231",  # orange
    "#911eb4",  # purple
    "#008080",  # teal
]

# Seed dispatchers (the people working the call center). Each has a calm,
# distinct identity tint used only for provenance, never as decoration.
SEED_DISPATCHERS = [
    Dispatcher(dispatcher_id="d-daria", name="דריה", color="#5b8def"),
    Dispatcher(dispatcher_id="d-noa", name="נועה", color="#26a69a"),
    Dispatcher(dispatcher_id="d-amir", name="אמיר", color="#c97bd8"),
]


class Store:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.calls: Dict[str, Call] = {}
        self.incidents: Dict[str, Incident] = {}
        self.dispatchers: Dict[str, Dispatcher] = {}
        self.suggestions: Dict[str, MergeSuggestion] = {}
        # Known large events: the pre-known intelligence layer. Reference data,
        # so it deliberately SURVIVES reset() (unlike live calls/incidents).
        self.known_events: Dict[str, KnownEvent] = {}
        self._color_idx = 0
        self._inc_seq = 0
        self._sug_seq = 0
        self._evt_seq = 0
        self._seed_dispatchers()

    def _seed_dispatchers(self) -> None:
        for d in SEED_DISPATCHERS:
            self.dispatchers[d.dispatcher_id] = d

    def reset(self) -> None:
        """Clear the live picture (calls/incidents/suggestions).

        Known events are pre-known reference data and are intentionally kept.
        """
        with self._lock:
            self.calls.clear()
            self.incidents.clear()
            self.suggestions.clear()
            self._color_idx = 0
            self._inc_seq = 0
            self._sug_seq = 0

    def next_color(self) -> str:
        with self._lock:
            color = PALETTE[self._color_idx % len(PALETTE)]
            self._color_idx += 1
            return color

    def next_incident_id(self) -> str:
        with self._lock:
            self._inc_seq += 1
            return f"inc-{self._inc_seq}"

    def next_suggestion_id(self) -> str:
        with self._lock:
            self._sug_seq += 1
            return f"sug-{self._sug_seq}"

    # --- calls ---
    def upsert_call(self, call: Call) -> None:
        with self._lock:
            self.calls[call.call_id] = call

    def get_call(self, call_id: str) -> Optional[Call]:
        return self.calls.get(call_id)

    def active_calls(self) -> List[Call]:
        return list(self.calls.values())

    # --- incidents ---
    def upsert_incident(self, incident: Incident) -> None:
        with self._lock:
            self.incidents[incident.incident_id] = incident

    def get_incident(self, incident_id: str) -> Optional[Incident]:
        return self.incidents.get(incident_id)

    def active_incidents(self) -> List[Incident]:
        """Only incidents still standing (not merged away)."""
        return [i for i in self.incidents.values() if i.status == "open"]

    # --- dispatchers ---
    def active_dispatchers(self) -> List[Dispatcher]:
        return list(self.dispatchers.values())

    # --- merge suggestions ---
    def upsert_suggestion(self, sug: MergeSuggestion) -> None:
        with self._lock:
            self.suggestions[sug.suggestion_id] = sug

    def get_suggestion(self, suggestion_id: str) -> Optional[MergeSuggestion]:
        return self.suggestions.get(suggestion_id)

    def pending_suggestions(self) -> List[MergeSuggestion]:
        return [s for s in self.suggestions.values() if s.status == "pending"]

    def suggestion_between(self, a: str, b: str) -> Optional[MergeSuggestion]:
        """Find an existing pending suggestion for the same incident pair."""
        pair = {a, b}
        for s in self.suggestions.values():
            if s.status == "pending" and {s.incident_a, s.incident_b} == pair:
                return s
        return None

    # --- known large events ---
    def next_known_event_id(self) -> str:
        with self._lock:
            self._evt_seq += 1
            return f"evt-{self._evt_seq}"

    def upsert_known_event(self, evt: KnownEvent) -> None:
        with self._lock:
            self.known_events[evt.id] = evt

    def get_known_event(self, event_id: str) -> Optional[KnownEvent]:
        return self.known_events.get(event_id)

    def all_known_events(self) -> List[KnownEvent]:
        return list(self.known_events.values())


# Module-level singleton used across the app.
store = Store()
