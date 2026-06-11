"""Mock STT stub.

The prerecorded demo calls were removed, so this engine no longer has anything
to replay — it exists only as an inert fallback so the engine selector in
stt/__init__.py stays importable. Real transcription uses the ivrit engine
(STT_ENGINE=ivrit); see stt/ivrit_stt.py.
"""
from __future__ import annotations

from typing import Iterator

from stt.base import STTEngine


class MockSTT(STTEngine):
    def stream_chunks(self, source: str) -> Iterator[str]:
        return iter(())
