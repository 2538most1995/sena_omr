#!/bin/bash
set -euo pipefail

PROJECT_DIR="$(cd "${BASH_SOURCE[0]%/*}/.." && pwd)"
PID_FILE="$PROJECT_DIR/.run/uvicorn.pid"
PORT="${OMR_LISTEN_PORT:-18080}"

# 1. Stop recorded PID if alive
if [[ -f "$PID_FILE" ]]; then
  PID="$(<"$PID_FILE")"
  if [[ "$PID" =~ ^[0-9]+$ ]] && kill -0 "$PID" 2>/dev/null; then
    kill "$PID" 2>/dev/null || true
    for _ in {1..5}; do
      kill -0 "$PID" 2>/dev/null || break
      sleep 1
    done
    kill -9 "$PID" 2>/dev/null || true
    echo "Sent stop signal to PID $PID."
  fi
  rm -f "$PID_FILE"
fi

# 2. Guarantee port 18080 is freed from any orphan/zombie Uvicorn
if command -v fuser >/dev/null 2>&1; then
  fuser -k "${PORT}/tcp" 2>/dev/null || true
fi
pkill -9 -f "uvicorn.*${PORT}" 2>/dev/null || true
pkill -9 -f "backend.main:app" 2>/dev/null || true

echo "OMR API stopped."
