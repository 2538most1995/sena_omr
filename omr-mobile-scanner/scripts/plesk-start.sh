#!/bin/bash
set -euo pipefail

PROJECT_DIR="$(cd "${BASH_SOURCE[0]%/*}/.." && pwd)"
VENV_DIR="$PROJECT_DIR/.venv"
RUN_DIR="$PROJECT_DIR/.run"
PID_FILE="$RUN_DIR/uvicorn.pid"
LOG_FILE="$RUN_DIR/uvicorn.log"
PORT="${OMR_PORT:-18080}"

mkdir -p "$RUN_DIR"

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
    echo "OMR API process $EXISTING_PID exists but is not ready yet." >&2
    exit 1
  fi
fi

cd "$PROJECT_DIR"
nohup "$VENV_DIR/bin/uvicorn" backend.main:app \
  --host 127.0.0.1 \
  --port "$PORT" \
  --proxy-headers \
  >>"$LOG_FILE" 2>&1 </dev/null &
API_PID=$!
echo "$API_PID" > "$PID_FILE"
disown "$API_PID" 2>/dev/null || true

if curl --silent --fail --max-time 3 --retry 30 --retry-delay 1 \
  --retry-all-errors "http://127.0.0.1:$PORT/api/health" >/dev/null; then
  echo "OMR API started on port $PORT."
  exit 0
fi

echo "OMR API did not become ready. Check $LOG_FILE." >&2
exit 1
