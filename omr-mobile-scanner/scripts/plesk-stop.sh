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
rm -f "$PID_FILE"
echo "Sent the stop signal to OMR API process $PID."
