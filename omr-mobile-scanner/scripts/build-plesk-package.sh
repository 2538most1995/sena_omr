#!/bin/bash
set -euo pipefail

PROJECT_DIR="$(cd "${BASH_SOURCE[0]%/*}/.." && pwd)"
DEPLOY_ROOT="$(cd "$PROJECT_DIR/.." && pwd)"
OUTPUT_FILE="$DEPLOY_ROOT/sena-omr-plesk-deploy.zip"
UV_ARCHIVE="$DEPLOY_ROOT/uv-x86_64-unknown-linux-musl.tar.gz"
UV_DOWNLOAD_URL="https://github.com/astral-sh/uv/releases/download/0.12.14/uv-x86_64-unknown-linux-musl.tar.gz"
UV_SHA256="df163630683e5a2106d3320e2a448fde8eda5e7b9b47f617c5c332769728e735"
LIBSTDC_DEB_URL="https://deb.debian.org/debian/pool/main/g/gcc-12/libstdc++6_12.2.0-14+deb12u1_amd64.deb"
LIBSTDC_DEB_SHA256="5cd3171216d4ab0fc911cfe9c35509bf2dd8f47761c43b7f6a4296701551a24d"
LIBGCC_DEB_URL="https://deb.debian.org/debian/pool/main/g/gcc-12/libgcc-s1_12.2.0-14+deb12u1_amd64.deb"
LIBGCC_DEB_SHA256="3016e62cb4b7cd8038822870601f5ed131befe942774d0f745622cc77d8a88f7"
STAGE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/sena-omr-package.XXXXXX")"
PACKAGE_ROOT="$STAGE_DIR/package"
APP_ROOT="$PACKAGE_ROOT/omr-mobile-scanner"
TEMP_ZIP="$STAGE_DIR/sena-omr-plesk-deploy.zip"

cleanup() {
  rm -rf "$STAGE_DIR"
}
trap cleanup EXIT

command -v zip >/dev/null 2>&1 || { echo "zip is required." >&2; exit 1; }
command -v ar >/dev/null 2>&1 || { echo "ar is required." >&2; exit 1; }
command -v tar >/dev/null 2>&1 || { echo "tar is required." >&2; exit 1; }
command -v curl >/dev/null 2>&1 || { echo "curl is required." >&2; exit 1; }
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

verify_download() {
  local expected="$1"
  local file="$2"
  if command -v sha256sum >/dev/null 2>&1; then
    echo "$expected  $file" | sha256sum -c -
  else
    echo "$expected  $file" | shasum -a 256 -c -
  fi
}

LIBSTDC_DEB="$STAGE_DIR/libstdc++6.deb"
LIBGCC_DEB="$STAGE_DIR/libgcc-s1.deb"
curl --proto '=https' --tlsv1.2 -LsSf "$LIBSTDC_DEB_URL" -o "$LIBSTDC_DEB"
curl --proto '=https' --tlsv1.2 -LsSf "$LIBGCC_DEB_URL" -o "$LIBGCC_DEB"
verify_download "$LIBSTDC_DEB_SHA256" "$LIBSTDC_DEB"
verify_download "$LIBGCC_DEB_SHA256" "$LIBGCC_DEB"

mkdir -p "$STAGE_DIR/libstdc" "$STAGE_DIR/libgcc"
(
  cd "$STAGE_DIR/libstdc"
  ar x "$LIBSTDC_DEB"
  tar -xf data.tar.xz
)
(
  cd "$STAGE_DIR/libgcc"
  ar x "$LIBGCC_DEB"
  tar -xf data.tar.xz
)

mkdir -p "$APP_ROOT/backend" "$APP_ROOT/database" "$APP_ROOT/frontend" "$APP_ROOT/scripts" "$APP_ROOT/runtime-libs"
cp "$DEPLOY_ROOT/.htaccess" "$PACKAGE_ROOT/.htaccess"
cp "$PROJECT_DIR/backend/__init__.py" "$PROJECT_DIR/backend/main.py" \
  "$PROJECT_DIR/backend/omr.py" "$PROJECT_DIR/backend/storage.py" \
  "$PROJECT_DIR/backend/requirements.txt" "$APP_ROOT/backend/"
cp "$PROJECT_DIR/database/schema.sql" "$APP_ROOT/database/"
cp "$PROJECT_DIR/frontend/index.html" "$PROJECT_DIR/frontend/app.js" \
  "$PROJECT_DIR/frontend/styles.css" "$PROJECT_DIR/frontend/sw.js" \
  "$PROJECT_DIR/frontend/icon.svg" "$PROJECT_DIR/frontend/manifest.webmanifest" "$APP_ROOT/frontend/"
cp "$PROJECT_DIR/scripts/plesk-bootstrap.sh" "$PROJECT_DIR/scripts/plesk-start.sh" \
  "$PROJECT_DIR/scripts/plesk-serve.sh" "$PROJECT_DIR/scripts/plesk-stop.sh" \
  "$PROJECT_DIR/scripts/plesk-detach.py" "$PROJECT_DIR/scripts/init-mysql.py" \
  "$PROJECT_DIR/scripts/build-plesk-package.sh" "$APP_ROOT/scripts/"
cp "$PROJECT_DIR/.env.production.example" "$PROJECT_DIR/README.md" "$APP_ROOT/"
cp "$UV_ARCHIVE" "$APP_ROOT/uv-x86_64-unknown-linux-musl.tar.gz"
printf '%s\n' "$UV_SHA256" > "$APP_ROOT/uv-x86_64-unknown-linux-musl.tar.gz.sha256"
cp "$STAGE_DIR/libstdc/usr/lib/x86_64-linux-gnu/libstdc++.so.6.0.30" \
  "$APP_ROOT/runtime-libs/libstdc++.so.6"
cp "$STAGE_DIR/libgcc/lib/x86_64-linux-gnu/libgcc_s.so.1" \
  "$APP_ROOT/runtime-libs/libgcc_s.so.1"

chmod 755 "$APP_ROOT/scripts/"*.sh
(
  cd "$PACKAGE_ROOT"
  zip -q -r "$TEMP_ZIP" .
)
mv "$TEMP_ZIP" "$OUTPUT_FILE"

echo "Created $OUTPUT_FILE"
