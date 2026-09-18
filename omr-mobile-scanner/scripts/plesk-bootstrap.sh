#!/bin/bash
set -euo pipefail

PROJECT_DIR="$(cd "${BASH_SOURCE[0]%/*}/.." && pwd)"
TOOLS_DIR="$PROJECT_DIR/.tools"
VENV_DIR="$PROJECT_DIR/.venv"
UV_VERSION="0.12.14"
UV_ARCHIVE="$TOOLS_DIR/uv-x86_64-unknown-linux-musl.tar.gz"
UV_EXTRACT_DIR="$TOOLS_DIR/uv-x86_64-unknown-linux-musl"
UV_BUNDLED_ARCHIVE="$PROJECT_DIR/uv-x86_64-unknown-linux-musl.tar.gz"
UV_SHA256="df163630683e5a2106d3320e2a448fde8eda5e7b9b47f617c5c332769728e735"

mkdir -p "$TOOLS_DIR" "$PROJECT_DIR/.run"

if [[ ! -x "$TOOLS_DIR/uv" ]]; then
  # Plesk's jailed shell intentionally has no `uname`, so use the verified
  # x86_64 Linux artifact directly instead of the platform-detecting installer.
  if [[ -f "$UV_BUNDLED_ARCHIVE" ]]; then
    cp "$UV_BUNDLED_ARCHIVE" "$UV_ARCHIVE"
  else
    curl --proto '=https' --tlsv1.2 -LsSf \
      "https://github.com/astral-sh/uv/releases/download/$UV_VERSION/uv-x86_64-unknown-linux-musl.tar.gz" \
      -o "$UV_ARCHIVE"
  fi

  if command -v sha256sum >/dev/null 2>&1; then
    echo "$UV_SHA256  $UV_ARCHIVE" | sha256sum -c -
  elif command -v shasum >/dev/null 2>&1; then
    echo "$UV_SHA256  $UV_ARCHIVE" | shasum -a 256 -c -
  elif command -v php >/dev/null 2>&1; then
    UV_ACTUAL_SHA256="$(php -r 'echo hash_file("sha256", $argv[1]);' "$UV_ARCHIVE")"
    [[ "$UV_ACTUAL_SHA256" == "$UV_SHA256" ]] || {
      echo "uv archive checksum mismatch." >&2
      exit 1
    }
  else
    echo "Cannot verify the uv archive: sha256sum, shasum, or PHP is required." >&2
    exit 1
  fi

  mkdir -p "$UV_EXTRACT_DIR"
  tar -xzf "$UV_ARCHIVE" -C "$TOOLS_DIR"
  mv "$UV_EXTRACT_DIR/uv" "$TOOLS_DIR/uv"
  chmod 755 "$TOOLS_DIR/uv"
  rm -f "$UV_ARCHIVE"
fi

if ! "$VENV_DIR/bin/python" -c 'import sys; raise SystemExit(0 if sys.platform.startswith("linux") else 1)' \
    >/dev/null 2>&1; then
  "$TOOLS_DIR/uv" venv --clear --python 3.12 "$VENV_DIR"
fi

"$TOOLS_DIR/uv" pip install --python "$VENV_DIR/bin/python" -r "$PROJECT_DIR/backend/requirements.txt"

if [[ ! -f "$PROJECT_DIR/.env" ]]; then
  cp "$PROJECT_DIR/.env.production.example" "$PROJECT_DIR/.env"
fi

echo "OMR production runtime is ready."
