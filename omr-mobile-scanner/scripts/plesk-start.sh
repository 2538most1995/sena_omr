#!/bin/bash
set -euo pipefail

PROJECT_DIR="$(cd "${BASH_SOURCE[0]%/*}/.." && pwd)"
VENV_DIR="$PROJECT_DIR/.venv"
RUN_DIR="$PROJECT_DIR/.run"
PID_FILE="$RUN_DIR/uvicorn.pid"
LOG_FILE="$RUN_DIR/uvicorn.log"
# Plesk reserves OMR_PORT for its own application runtime and currently injects
# 8000.  Use an app-specific variable so Apache's proxy and Uvicorn cannot
# silently drift onto different ports.
PORT="${OMR_LISTEN_PORT:-18080}"
RUNTIME_LIBS_DIR="$PROJECT_DIR/runtime-libs"

mkdir -p "$RUN_DIR"
export LD_LIBRARY_PATH="$RUNTIME_LIBS_DIR:/usr/lib:/usr/lib64${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

if [[ ! -x "$VENV_DIR/bin/uvicorn" ]]; then
  echo "Runtime is missing. Run scripts/plesk-bootstrap.sh first." >&2
  exit 1
fi

if curl --silent --fail --max-time 3 "http://127.0.0.1:$PORT/api/health" >/dev/null; then
  echo "OMR API is already healthy on port $PORT."
  exit 0
fi

if [[ -f "$PID_FILE" ]]; then
  EXISTING_PID="$(<"$PID_FILE")"
  if [[ "$EXISTING_PID" =~ ^[0-9]+$ ]] && kill -0 "$EXISTING_PID" 2>/dev/null; then
    # The PID belongs to this app because only this script writes PID_FILE. A
    # live-but-unhealthy process is normally a previous deploy on the wrong
    # port, so replace it instead of leaving every keep-alive run blocked.
    kill "$EXISTING_PID" 2>/dev/null || true
    for _ in {1..10}; do
      kill -0 "$EXISTING_PID" 2>/dev/null || break
      sleep 1
    done
  fi
fi

cd "$PROJECT_DIR"
"$VENV_DIR/bin/python" "$PROJECT_DIR/scripts/plesk-detach.py" \
  --pid-file "$PID_FILE" \
  --log-file "$LOG_FILE" \
  --port "$PORT"

if curl --silent --fail --max-time 3 --retry 30 --retry-delay 1 \
  --retry-all-errors "http://127.0.0.1:$PORT/api/health" >/dev/null; then
  echo "OMR API started on port $PORT."
  exit 0
fi

echo "OMR API did not become ready. Check $LOG_FILE." >&2
exit 1
