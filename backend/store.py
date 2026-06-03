"""In-memory storage for active calls and incidents.

Hackathon-grade: a process-local singleton. Swap for Redis/DB later without
touching callers — they only use the public methods here.
"""
from __future__ import annotations

import threading
from typing import Dict, List, Optional

from models import Call, Incident

# A fixed palette so each call gets a stable, distinguishable provenance color.
PALETTE = [
    "#e6194b",  # red
    "#3cb44b",  # green
    "#4363d8",  # blue
    "#f58231",  # orange
    "#911eb4",  # purple
    "#008080",  # teal
]


class Store:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.calls: Dict[str, Call] = {}
        self.incidents: Dict[str, Incident] = {}
        self._color_idx = 0

    def reset(self) -> None:
        with self._lock:
            self.calls.clear()
            self.incidents.clear()
            self._color_idx = 0

    def next_color(self) -> str:
        with self._lock:
            color = PALETTE[self._color_idx % len(PALETTE)]
            self._color_idx += 1
            return color

    def upsert_call(self, call: Call) -> None:
        with self._lock:
            self.calls[call.call_id] = call

    def get_call(self, call_id: str) -> Optional[Call]:
        return self.calls.get(call_id)

    def upsert_incident(self, incident: Incident) -> None:
        with self._lock:
            self.incidents[incident.incident_id] = incident

    def get_incident(self, incident_id: str) -> Optional[Incident]:
        return self.incidents.get(incident_id)

    def active_calls(self) -> List[Call]:
        return list(self.calls.values())

    def active_incidents(self) -> List[Incident]:
        return list(self.incidents.values())


# Module-level singleton used across the app.
store = Store()
