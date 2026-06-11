"""Triage logic: caller-frequency tracking and keyword-based prioritization.

Kept separate from the web/TwiML layer so it is pure, testable Python with no
Twilio or FastAPI dependency. For the prototype this is in-memory; the
CallerTracker interface is deliberately tiny so it can be swapped for a Redis-
backed implementation later without touching main.py.
"""
from __future__ import annotations

import threading
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Deque, Dict, List, Tuple

from config import CRITICAL_KEYWORDS, REPEAT_CALLER_WINDOW_SECONDS


def _now() -> datetime:
    return datetime.now(timezone.utc)


class CallerTracker:
    """Counts how often each phone number calls within a sliding time window.

    Thread-safe: Twilio webhooks can arrive concurrently, so all access to the
    shared dict is guarded by a lock. Old timestamps outside the window are
    pruned on each call, so memory stays bounded to "recent" callers.
    """

    def __init__(self, window_seconds: int = REPEAT_CALLER_WINDOW_SECONDS) -> None:
        self._window = timedelta(seconds=window_seconds)
        self._lock = threading.Lock()
        # caller_id -> timestamps of recent calls (oldest first)
        self._calls: Dict[str, Deque[datetime]] = {}

    def record_call(self, caller_id: str) -> Tuple[int, bool]:
        """Record a new call from `caller_id`.

        Returns (call_count, is_repeat_caller) where call_count is how many
        times this number has called inside the window (including this call),
        and is_repeat_caller is True once that count exceeds one.
        """
        now = _now()
        cutoff = now - self._window
        with self._lock:
            history = self._calls.setdefault(caller_id, deque())
            # Drop calls older than the window so counts reflect *recent* load.
            while history and history[0] < cutoff:
                history.popleft()
            history.append(now)
            count = len(history)
        return count, count > 1


def detect_keywords(transcript: str,
                    keywords: List[str] = CRITICAL_KEYWORDS) -> List[str]:
    """Return the high-priority keywords present in `transcript`.

    Simple substring match: Hebrew root forms (e.g. "פצוע") therefore also
    catch inflections ("פצועים"). Empty/blank transcript -> no matches.
    """
    if not transcript:
        return []
    text = transcript.strip()
    return [kw for kw in keywords if kw in text]


def classify_priority(matched_keywords: List[str]) -> str:
    """CRITICAL if any high-priority keyword matched, else STANDARD."""
    return "CRITICAL" if matched_keywords else "STANDARD"
