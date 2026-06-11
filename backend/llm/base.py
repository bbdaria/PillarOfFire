"""LLM analysis abstraction.

The rest of the system depends only on `Analyzer.analyze(transcript) -> CallAnalysis`.
Concrete analyzers (rule-based mock, real Claude) are interchangeable.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from models import CallAnalysis


class Analyzer(ABC):
    @abstractmethod
    def analyze(self, transcript: str) -> CallAnalysis:
        """Convert a Hebrew transcript into structured incident details."""
        raise NotImplementedError

    def warmup(self) -> None:
        """Optional health check / preload, logged at startup. No-op by default."""
        return None
