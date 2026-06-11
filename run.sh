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

# --- Auto-Operator (Twilio voice) ---
# Each answer is recorded and transcribed by our ivrit STT. Fetching the Twilio
# recording requires your account credentials — set these before calling:
#   export TWILIO_ACCOUNT_SID=ACxxxxxxxx
#   export TWILIO_AUTH_TOKEN=xxxxxxxx
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
# if [ ! -d ".venv" ]; then
#   echo "Creating virtualenv and installing dependencies..."
#   python3 -m venv .venv
#   .venv/bin/pip install -q --upgrade pip
#   .venv/bin/pip install -q -r requirements.txt
# fi

PORT="${PORT:-8000}"

# Always start on a clear port: if a previous server is still holding PORT,
# stop it first so we never fail with "address already in use". Uses python
# (always present) so this works the same on Windows and Unix.
python - "$PORT" <<'PY'
import sys, subprocess, platform
port = sys.argv[1]
if platform.system() == "Windows":
    out = subprocess.run(["netstat", "-ano", "-p", "tcp"], capture_output=True, text=True).stdout
    pids = {l.split()[-1] for l in out.splitlines() if f":{port} " in l and "LISTENING" in l}
    for pid in pids:
        subprocess.run(["taskkill", "/F", "/PID", pid], capture_output=True)
        print(f"freed port {port} (stopped old server PID {pid})")
else:
    r = subprocess.run(["lsof", "-ti", f"tcp:{port}"], capture_output=True, text=True)
    for pid in r.stdout.split():
        subprocess.run(["kill", "-9", pid], capture_output=True)
        print(f"freed port {port} (stopped old server PID {pid})")
PY

echo "Pillar of Fire running at  http://127.0.0.1:${PORT}"
echo "(STT=${STT_ENGINE:-mock}  LLM=${LLM_ENGINE:-mock})"

cd backend
exec python -m uvicorn app:app --host 127.0.0.1 --port "${PORT}"