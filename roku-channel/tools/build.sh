#!/usr/bin/env bash
# Build dist/roku-couch.zip for sideloading.
#   1. bake ../config.json (or ../config.example.json, or defaults) into
#      source/generated_config.brs
#   2. generate artwork if images/ is missing it (or always with --assets)
#   3. zip manifest + source/ + components/ + images/ with manifest at the root
set -euo pipefail

cd "$(dirname "$0")/.."   # roku-channel/

if ! command -v python3 >/dev/null 2>&1; then
    echo "build: python3 is required" >&2
    exit 1
fi
if ! command -v zip >/dev/null 2>&1; then
    echo "build: 'zip' is required (apt install zip / brew install zip)" >&2
    exit 1
fi

if [[ "${1:-}" == "--assets" ]] || ! ls images/bg_*.jpg >/dev/null 2>&1 || [[ ! -f images/icon_focus_fhd.png ]]; then
    echo "build: generating images (needs Pillow)"
    python3 tools/gen_assets.py
fi

python3 tools/prep_local_bg.py
python3 tools/gen_config.py

# Optional: validate with BrighterScript + bslint when `npm install` has been run.
if [[ -x node_modules/.bin/bsc ]]; then
    echo "build: validating with bsc"
    node_modules/.bin/bsc --create-package false
fi

mkdir -p dist
rm -f dist/roku-couch.zip
zip -9 -r -q dist/roku-couch.zip manifest source components images fonts \
    -x '*.DS_Store' -x '*/.*' -x '*~'

echo "build: wrote dist/roku-couch.zip ($(wc -c < dist/roku-couch.zip | tr -d ' ') bytes)"
