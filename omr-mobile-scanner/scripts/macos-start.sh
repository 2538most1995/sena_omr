#!/bin/zsh
set -euo pipefail

PROJECT_DIR="${0:A:h:h}"
VENV_DIR="$PROJECT_DIR/.venv"
RUN_DIR="$PROJECT_DIR/.run"
LOG_FILE="$RUN_DIR/uvicorn.log"
SERVICE_LABEL="th.ac.nfe.omr-scanner"
PYTHON_BIN="$(command -v python3.11 2>/dev/null || command -v python3)"

if [[ -f "$PROJECT_DIR/.env" ]]; then
  set -a
  source "$PROJECT_DIR/.env"
  set +a
fi

if [[ ! -d "$VENV_DIR" ]]; then
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

if [[ ! -x "$VENV_DIR/bin/uvicorn" ]] || \
   ! "$VENV_DIR/bin/python" -c 'import fastapi, httpx, cv2, numpy, pymysql, PIL, multipart' 2>/dev/null; then
  "$VENV_DIR/bin/python" -m pip install -r "$PROJECT_DIR/backend/requirements.txt"
fi

mkdir -p "$RUN_DIR"

if curl --silent --fail http://127.0.0.1:8000/api/health >/dev/null; then
  echo "FastAPI is already running."
  echo "Open http://localhost:8888/"
  exit 0
fi

: > "$LOG_FILE"
for ATTEMPT in {1..3}; do
  launchctl remove "$SERVICE_LABEL" 2>/dev/null || true
  sleep 1
  launchctl submit \
    -l "$SERVICE_LABEL" \
    -o "$LOG_FILE" \
    -e "$LOG_FILE" \
    -- /bin/zsh "$PROJECT_DIR/scripts/run-backend.sh"

  for _ in {1..30}; do
    if curl --silent --fail http://127.0.0.1:8000/api/health >/dev/null; then
      echo "OMR backend started as macOS service $SERVICE_LABEL."
      echo "Open http://localhost:8888/"
      exit 0
    fi
    sleep 0.2
  done
done

echo "FastAPI did not become ready. See $LOG_FILE" >&2
exit 1
