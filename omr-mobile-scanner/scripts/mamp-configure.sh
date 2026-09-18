#!/bin/zsh
set -euo pipefail

PROJECT_DIR="${0:A:h:h}"
HTTPD_CONF="/Applications/MAMP/conf/apache/httpd.conf"
HTTPD_BIN="/Applications/MAMP/Library/bin/httpd"
APACHECTL="/Applications/MAMP/Library/bin/apachectl"
PROXY_CONF="$PROJECT_DIR/mamp/omr-proxy.conf"
INCLUDE_LINE="IncludeOptional \"$PROXY_CONF\""
BACKUP_FILE="$HTTPD_CONF.omr-backup"

if [[ ! -f "$HTTPD_CONF" || ! -x "$HTTPD_BIN" ]]; then
  echo "MAMP Apache was not found under /Applications/MAMP." >&2
  exit 1
fi

if [[ ! -f "$BACKUP_FILE" ]]; then
  cp -p "$HTTPD_CONF" "$BACKUP_FILE"
fi

perl -0pi -e 's/^#LoadModule proxy_module modules\/mod_proxy\.so$/LoadModule proxy_module modules\/mod_proxy.so/m' "$HTTPD_CONF"
perl -0pi -e 's/^#LoadModule proxy_http_module modules\/mod_proxy_http\.so$/LoadModule proxy_http_module modules\/mod_proxy_http.so/m' "$HTTPD_CONF"

if ! grep -Fq "$INCLUDE_LINE" "$HTTPD_CONF"; then
  printf '\n# OMR Mobile Scanner (local MAMP integration)\n%s\n' "$INCLUDE_LINE" >> "$HTTPD_CONF"
fi

if ! "$HTTPD_BIN" -t -f "$HTTPD_CONF"; then
  cp -p "$BACKUP_FILE" "$HTTPD_CONF"
  echo "Apache configuration was invalid and has been restored." >&2
  exit 1
fi

if pgrep -f '/Applications/MAMP/Library/bin/httpd' >/dev/null; then
  "$APACHECTL" graceful
fi

echo "MAMP is configured for OMR at http://localhost:8888/"
echo "Original backup: $BACKUP_FILE"
