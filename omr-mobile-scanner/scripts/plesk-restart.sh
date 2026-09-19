#!/bin/bash
set -euo pipefail

PROJECT_DIR="$(cd "${BASH_SOURCE[0]%/*}/.." && pwd)"
"$PROJECT_DIR/scripts/plesk-stop.sh" || true
"$PROJECT_DIR/scripts/plesk-start.sh"
