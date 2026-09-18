#!/bin/zsh
set -euo pipefail

PROJECT_DIR="${0:A:h:h}"
SERVICE_LABEL="th.ac.nfe.omr-scanner"

if launchctl print "gui/$(id -u)/$SERVICE_LABEL" >/dev/null 2>&1; then
  launchctl remove "$SERVICE_LABEL"
  for _ in {1..50}; do
    if ! launchctl print "gui/$(id -u)/$SERVICE_LABEL" >/dev/null 2>&1 && \
       ! curl --silent --fail http://127.0.0.1:8000/api/health >/dev/null; then
      break
    fi
    sleep 0.1
  done
  echo "OMR backend stopped."
else
  echo "OMR backend is not running from the project script."
fi
