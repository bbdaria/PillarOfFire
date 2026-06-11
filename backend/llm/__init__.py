"""Analyzer selection.

Default = rule-based mock (offline). Set LLM_ENGINE=llama to use a local/remote
Llama via an OpenAI-compatible endpoint (e.g. Ollama), or LLM_ENGINE=claude for
the Anthropic API. Both fall back to mock if their backend is unavailable, so the
demo never breaks.
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
    if engine == "llama":
        from llm.llama_analyzer import LlamaAnalyzer
        return LlamaAnalyzer()
    return MockAnalyzer()
