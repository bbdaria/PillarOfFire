"""STT abstraction layer.

The dashboard only ever talks to `STTEngine.stream_chunks(...)`. The concrete
engine (mock vs. real ivrit-ai) is chosen in stt/__init__.py, so swapping in the
real Hebrew model is a one-line change with no impact on the rest of the system.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterator, Optional


class STTEngine(ABC):
    """Abstract Hebrew speech-to-text engine."""

    @abstractmethod
    def stream_chunks(self, source: str) -> Iterator[str]:
        """Yield transcript chunks (partial Hebrew text) for an audio source.

        `source` is an audio file path or a demo call id. Yielding chunks over
        time is what produces the live, incremental transcript in the UI.
        """
        raise NotImplementedError

    def transcribe(self, source: str) -> str:
        """Convenience: full transcript as a single string."""
        return " ".join(self.stream_chunks(source))
