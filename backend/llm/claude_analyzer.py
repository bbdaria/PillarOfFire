"""Real LLM analyzer using the Anthropic Claude API.

Active only when ANTHROPIC_API_KEY is set and LLM_ENGINE=claude. Falls back to
the mock analyzer if the SDK or key is unavailable, so the demo never breaks.
"""
from __future__ import annotations

import json
import os

from models import CallAnalysis
from llm.base import Analyzer
from llm.mock_analyzer import MockAnalyzer

MODEL = os.environ.get("CLAUDE_MODEL", "claude-opus-4-8")

SYSTEM_PROMPT = """You are a decision-support assistant for a Hebrew emergency \
call center. You extract structured details from a transcribed emergency call. \
You are NOT a replacement for human judgement or official protocol. Be cautious; \
prefer marking details as missing over guessing. Suggested questions and next \
steps must be practical and safety-oriented (exact location, number of injured, \
immediate hazards, whether the caller is safe, whether services are present).

Return ONLY a JSON object with keys: summary, event_type, location \
{raw_text, normalized, lat, lng, confidence}, casualties {injured, dead, unknown}, \
hazards (list), people_involved, urgency_indicators (list), distress_level, \
missing_information (list), suggested_questions (list), severity {score 1-10, \
label one of low|medium|high|critical, reasoning}, recommended_next_steps (list). \
Hebrew text in output is fine."""


class ClaudeAnalyzer(Analyzer):
    def __init__(self) -> None:
        self._fallback = MockAnalyzer()
        self._client = None
        try:
            import anthropic  # type: ignore
            if os.environ.get("ANTHROPIC_API_KEY"):
                self._client = anthropic.Anthropic()
        except Exception:
            self._client = None

    def analyze(self, transcript: str) -> CallAnalysis:
        if self._client is None:
            return self._fallback.analyze(transcript)
        try:
            msg = self._client.messages.create(
                model=MODEL,
                max_tokens=1500,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": f"תמלול שיחה:\n{transcript}"}],
            )
            text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
            data = json.loads(_extract_json(text))
            return CallAnalysis(**data)
        except Exception:
            # Any API/parse failure -> safe deterministic fallback.
            return self._fallback.analyze(transcript)


def _extract_json(text: str) -> str:
    start = text.find("{")
    end = text.rfind("}")
    return text[start : end + 1] if start != -1 and end != -1 else "{}"
