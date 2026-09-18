#!/bin/zsh
set -euo pipefail

PROJECT_DIR="${0:A:h:h}"

if [[ -f "$PROJECT_DIR/.env" ]]; then
  set -a
  source "$PROJECT_DIR/.env"
  set +a
fi

exec "$PROJECT_DIR/.venv/bin/uvicorn" backend.main:app \
  --app-dir "$PROJECT_DIR" \
  --host 127.0.0.1 \
  --port 8000 \
  --proxy-headers \
  --forwarded-allow-ips 127.0.0.1
