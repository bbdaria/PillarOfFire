"""Mock STT: replays prerecorded Hebrew transcripts as timed chunks.

This simulates a real-time stream without needing audio or a GPU, so the demo
runs anywhere. The chunk boundaries come from demo_data.py.
"""
from __future__ import annotations

from typing import Iterator

from stt.base import STTEngine
from demo_data import DEMO_CALLS


class MockSTT(STTEngine):
    def stream_chunks(self, source: str) -> Iterator[str]:
        call = DEMO_CALLS.get(source)
        if not call:
            return
        for chunk in call["chunks"]:
            yield chunk
