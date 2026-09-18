#!/bin/bash
set -euo pipefail

PROJECT_DIR="$(cd "${BASH_SOURCE[0]%/*}/.." && pwd)"
DEPLOY_ROOT="$(cd "$PROJECT_DIR/.." && pwd)"
OUTPUT_FILE="$DEPLOY_ROOT/sena-omr-plesk-deploy.zip"
UV_ARCHIVE="$DEPLOY_ROOT/uv-x86_64-unknown-linux-musl.tar.gz"
UV_DOWNLOAD_URL="https://github.com/astral-sh/uv/releases/download/0.12.14/uv-x86_64-unknown-linux-musl.tar.gz"
UV_SHA256="df163630683e5a2106d3320e2a448fde8eda5e7b9b47f617c5c332769728e735"
STAGE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/sena-omr-package.XXXXXX")"
PACKAGE_ROOT="$STAGE_DIR/package"
APP_ROOT="$PACKAGE_ROOT/omr-mobile-scanner"
TEMP_ZIP="$STAGE_DIR/sena-omr-plesk-deploy.zip"

cleanup() {
  rm -rf "$STAGE_DIR"
}
trap cleanup EXIT

command -v zip >/dev/null 2>&1 || { echo "zip is required." >&2; exit 1; }
if [[ ! -f "$UV_ARCHIVE" ]]; then
  command -v curl >/dev/null 2>&1 || { echo "curl is required to download uv." >&2; exit 1; }
  UV_ARCHIVE="$STAGE_DIR/uv-x86_64-unknown-linux-musl.tar.gz"
  curl --proto '=https' --tlsv1.2 -LsSf "$UV_DOWNLOAD_URL" -o "$UV_ARCHIVE"
fi

if command -v sha256sum >/dev/null 2>&1; then
  echo "$UV_SHA256  $UV_ARCHIVE" | sha256sum -c -
else
  echo "$UV_SHA256  $UV_ARCHIVE" | shasum -a 256 -c -
fi

mkdir -p "$APP_ROOT/backend" "$APP_ROOT/frontend" "$APP_ROOT/scripts"
cp "$DEPLOY_ROOT/.htaccess" "$PACKAGE_ROOT/.htaccess"
cp "$PROJECT_DIR/backend/__init__.py" "$PROJECT_DIR/backend/main.py" \
  "$PROJECT_DIR/backend/omr.py" "$PROJECT_DIR/backend/requirements.txt" "$APP_ROOT/backend/"
cp "$PROJECT_DIR/frontend/index.html" "$PROJECT_DIR/frontend/app.js" \
  "$PROJECT_DIR/frontend/styles.css" "$PROJECT_DIR/frontend/sw.js" \
  "$PROJECT_DIR/frontend/icon.svg" "$PROJECT_DIR/frontend/manifest.webmanifest" "$APP_ROOT/frontend/"
cp "$PROJECT_DIR/scripts/plesk-bootstrap.sh" "$PROJECT_DIR/scripts/plesk-start.sh" \
  "$PROJECT_DIR/scripts/build-plesk-package.sh" "$APP_ROOT/scripts/"
cp "$PROJECT_DIR/.env.production.example" "$PROJECT_DIR/README.md" "$APP_ROOT/"
cp "$UV_ARCHIVE" "$APP_ROOT/uv-x86_64-unknown-linux-musl.tar.gz"

chmod 755 "$APP_ROOT/scripts/"*.sh
(
  cd "$PACKAGE_ROOT"
  zip -q -r "$TEMP_ZIP" .
)
mv "$TEMP_ZIP" "$OUTPUT_FILE"

echo "Created $OUTPUT_FILE"
