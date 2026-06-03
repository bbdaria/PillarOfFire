#!/usr/bin/env bash
# Pillar of Fire — one-command launcher for the hackathon demo.
set -euo pipefail
cd "$(dirname "$0")"

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
