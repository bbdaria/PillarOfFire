#!/usr/bin/env bash
# Pillar of Fire — one-command launcher for the hackathon demo.
set -euo pipefail
cd "$(dirname "$0")"

# --- Set default environment variables for real STT ---
export STT_ENGINE="${STT_ENGINE:-ivrit}"
# ivrit-ai Hebrew model in CTranslate2 / faster-whisper format. Downloaded from
# Hugging Face on first run (~1.6GB) and cached under ~/.cache/huggingface.
export IVRIT_MODEL="${IVRIT_MODEL:-ivrit-ai/whisper-large-v3-turbo-ct2}"
# ----------------------------------------------------

# --- Set default environment variables for the LLM analyzer ---
export LLM_ENGINE="${LLM_ENGINE:-llama}"
# OpenAI-compatible endpoint (Ollama by default). Falls back to the rule-based
# mock analyzer automatically if this endpoint is unreachable.
#   ollama pull "$LLAMA_MODEL"   # one-time, before first run
export LLAMA_BASE_URL="${LLAMA_BASE_URL:-http://localhost:11434/v1}"
export LLAMA_MODEL="${LLAMA_MODEL:-llama3.1}"
# ----------------------------------------------------

# Create venv + install deps on first run.
if [ ! -d ".venv" ]; then
  echo "Creating virtualenv and installing dependencies..."
  python3 -m venv .venv
  .venv/bin/pip install -q --upgrade pip
  .venv/bin/pip install -q -r requirements.txt
fi

PORT="${PORT:-8000}"
echo "Pillar of Fire running at  http://127.0.0.1:${PORT}"
echo "(STT=${STT_ENGINE:-mock}  LLM=${LLM_ENGINE:-mock})"

cd backend
exec ../.venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port "${PORT}"