#!/bin/bash
# Checks the r1 creation in headless Chromium at 240x282. GitHub is never
# contacted: the test answers api.github.com and gist requests itself.
# Usage: tests/run.sh [screenshot dir]
set -u
D="$(cd "$(dirname "$0")/.." && pwd)"          # tools/agent-meter
SITE="$(cd "$D/../../agent-meter" && pwd)"      # the creation itself
W="$(mktemp -d -t agent-meter-tests-XXXXXX)"
cleanup() {
  [[ -n ${WEB:-} ]] && kill "$WEB" 2>/dev/null
  pkill -f -- "--user-data-dir=$W/profile" 2>/dev/null
  sleep 0.5; rm -rf "$W"
}
trap cleanup EXIT
python3 -I -m http.server 8792 --bind 127.0.0.1 --directory "$SITE" > "$W/web.out" 2>&1 &
WEB=$!
setsid chromium --headless=new --remote-debugging-port=9334 --user-data-dir="$W/profile" --no-first-run \
  --disable-gpu about:blank > "$W/chrome.out" 2>&1 &
sleep 3
timeout 300 python3 -I "$D/tests/test_creation.py" 9334 "http://127.0.0.1:8792/" "${1:-}"
