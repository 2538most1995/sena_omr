#!/bin/zsh
set -euo pipefail

if curl --silent --fail http://127.0.0.1:8000/api/health >/dev/null; then
  echo "FastAPI : ready on http://127.0.0.1:8000"
else
  echo "FastAPI : not running"
fi

if curl --silent --fail http://localhost:8888/api/health >/dev/null; then
  echo "MAMP    : proxy ready on http://localhost:8888"
else
  echo "MAMP    : proxy not ready"
fi
