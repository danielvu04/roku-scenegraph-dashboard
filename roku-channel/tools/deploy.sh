#!/usr/bin/env bash
# Sideload dist/roku-couch.zip onto a Roku in developer mode.
#   tools/deploy.sh <roku-ip> <dev-password>
# The dev-mode web installer user is always "rokudev".
set -euo pipefail

if [[ $# -ne 2 ]]; then
    echo "usage: $0 <roku-ip> <dev-password>" >&2
    exit 2
fi
IP="$1"
PW="$2"

cd "$(dirname "$0")/.."
ZIP="dist/roku-couch.zip"
if [[ ! -f "$ZIP" ]]; then
    echo "deploy: $ZIP not found - run tools/build.sh first" >&2
    exit 1
fi

echo "deploy: uploading $ZIP to http://$IP ..."
if ! RESP=$(curl -sS --fail --digest -u "rokudev:$PW" --connect-timeout 10 \
        -F "mysubmit=Install" -F "archive=@$ZIP" "http://$IP/plugin_install"); then
    echo "deploy: upload failed (wrong IP/password, or developer mode not enabled?)" >&2
    exit 1
fi

# Newer firmware reports via JS: ...trigger('Set message content', 'Install Success.')
# older firmware via <font color="red">...</font>.
MSG=$(printf '%s' "$RESP" | grep -oE "Set message content', '[^']*" | sed "s/.*', '//" || true)
if [[ -z "$MSG" ]]; then
    MSG=$(printf '%s' "$RESP" | grep -oE '<font color="red">[^<]*' | sed 's/<font color="red">//' || true)
fi
if [[ -n "$MSG" ]]; then
    printf 'deploy: %s\n' "$MSG"
else
    echo "deploy: request sent; check the TV (no status text found in response)"
fi
