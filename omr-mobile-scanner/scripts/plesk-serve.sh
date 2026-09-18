#!/bin/bash
set -euo pipefail

PROJECT_DIR="$(cd "${BASH_SOURCE[0]%/*}/.." && pwd)"
VENV_DIR="$PROJECT_DIR/.venv"
RUN_DIR="$PROJECT_DIR/.run"
PORT="${OMR_LISTEN_PORT:-18080}"
RUNTIME_LIBS_DIR="$PROJECT_DIR/runtime-libs"

mkdir -p "$RUN_DIR"
export LD_LIBRARY_PATH="$RUNTIME_LIBS_DIR:/usr/lib:/usr/lib64${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

if curl --silent --fail --max-time 3 "http://127.0.0.1:$PORT/api/health" >/dev/null; then
  echo "OMR API is already healthy on port $PORT."
  exit 0
fi

if [[ ! -x "$VENV_DIR/bin/python" ]]; then
  echo "Runtime is missing. Run scripts/plesk-bootstrap.sh first." >&2
  exit 1
fi

cd "$PROJECT_DIR"
echo "$$" > "$RUN_DIR/uvicorn.pid"
exec "$VENV_DIR/bin/python" -m uvicorn backend.main:app \
  --host 127.0.0.1 \
  --port "$PORT" \
  --proxy-headers \
  >>"$RUN_DIR/uvicorn.log" 2>&1
