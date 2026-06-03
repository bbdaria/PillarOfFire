"""Analyzer selection.

Default = rule-based mock (offline). Set LLM_ENGINE=claude (and ANTHROPIC_API_KEY)
to use the real Claude analyzer; it falls back to mock if anything is missing.
"""
from __future__ import annotations

import os

from llm.base import Analyzer
from llm.mock_analyzer import MockAnalyzer


def get_analyzer() -> Analyzer:
    engine = os.environ.get("LLM_ENGINE", "mock").lower()
    if engine == "claude":
        from llm.claude_analyzer import ClaudeAnalyzer
        return ClaudeAnalyzer()
    return MockAnalyzer()
