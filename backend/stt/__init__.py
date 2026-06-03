"""STT engine selection.

Default is the mock engine (offline, deterministic demo). Set STT_ENGINE=ivrit
to use the real ivrit-ai Hebrew model placeholder.
"""
from __future__ import annotations

import os

from stt.base import STTEngine
from stt.mock_stt import MockSTT


def get_stt_engine() -> STTEngine:
    engine = os.environ.get("STT_ENGINE", "mock").lower()
    if engine == "ivrit":
        from stt.ivrit_stt import IvritSTT
        return IvritSTT()
    return MockSTT()
