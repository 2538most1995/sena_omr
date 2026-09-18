#!/bin/bash
set -euo pipefail

PROJECT_DIR="$(cd "${BASH_SOURCE[0]%/*}/.." && pwd)"
PID_FILE="$PROJECT_DIR/.run/uvicorn.pid"

if [[ ! -f "$PID_FILE" ]]; then
  echo "OMR API is not running (PID file not found)."
  exit 0
fi

PID="$(<"$PID_FILE")"
if [[ ! "$PID" =~ ^[0-9]+$ ]]; then
  echo "Invalid PID file: $PID_FILE" >&2
  exit 1
fi

if ! kill -0 "$PID" 2>/dev/null; then
  rm -f "$PID_FILE"
  echo "OMR API is not running (stale PID $PID)."
  exit 0
fi

kill "$PID"
for _ in {1..20}; do
  if ! kill -0 "$PID" 2>/dev/null; then
    rm -f "$PID_FILE"
    echo "Stopped OMR API process $PID."
    exit 0
  fi
  sleep 0.25
done

echo "OMR API process $PID did not stop in time." >&2
exit 1
