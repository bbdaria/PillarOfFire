"""Placeholder for the real ivrit-ai Hebrew STT model.

This is intentionally inert by default so the project runs with no heavy
dependencies. To enable real transcription:

  1. pip install transformers torch faster-whisper soundfile
  2. Set environment variable  STT_ENGINE=ivrit
  3. Provide audio file paths (not demo ids) to stream_chunks().

ivrit-ai publishes Whisper-based Hebrew models on Hugging Face, e.g.
`ivrit-ai/whisper-large-v3-turbo` (https://huggingface.co/ivrit-ai). The skeleton
below shows where the real call goes; it streams segments as they decode so the
UI still gets an incremental transcript.
"""
from __future__ import annotations

import os
from typing import Iterator

from stt.base import STTEngine

MODEL_ID = os.environ.get("IVRIT_MODEL", "ivrit-ai/whisper-large-v3-turbo")


class IvritSTT(STTEngine):
    def __init__(self) -> None:
        self._model = None  # lazy-loaded on first use

    def _ensure_model(self):
        if self._model is None:
            # Deferred import: only pay the cost if the engine is actually used.
            from faster_whisper import WhisperModel  # type: ignore

            # The ivrit-ai models are distributed in CTranslate2 / Whisper format.
            self._model = WhisperModel(MODEL_ID, device="auto", compute_type="int8")
        return self._model

    def warmup(self) -> None:
        # Pre-load (and download, on first run) the weights at startup.
        self._ensure_model()

    def stream_chunks(self, source: str) -> Iterator[str]:
        model = self._ensure_model()
        # `source` is an audio file path here. Segments are yielded as the model
        # decodes through the audio, so the UI gets incremental ("on the fly")
        # transcript text rather than waiting for the whole file.
        segments, _info = model.transcribe(source, language="he")
        for seg in segments:
            text = seg.text.strip()
            if text:
                yield text
