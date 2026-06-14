#!/usr/bin/env bash
# Goal 55 latency validation runner: builds the cockpit, starts the server with a
# deliberately SLOW run-creation hook, runs the browser latency test against the
# real page, and tears the server down. Exits non-zero if the test fails.
#
# Usage (from the web/ directory):
#   ./scripts/run_55_latency.sh
#
# Env overrides: PORT, ESPRESSO_TEST_CREATE_DELAY_MS, ESPRESSO (path to the
# `espresso` entry point — the CLI module has no __main__, so use the entry point).
set -euo pipefail

here="$(cd "$(dirname "$0")/../.." && pwd)"   # repo root
port="${PORT:-8455}"
delay="${ESPRESSO_TEST_CREATE_DELAY_MS:-6000}"
espresso="${ESPRESSO:-espresso}"
runs_root="$(mktemp -d)"

echo "==> building cockpit (web/dist)"
( cd "$here/web" && npm run build >/dev/null )

echo "==> starting server on :$port with ESPRESSO_TEST_CREATE_DELAY_MS=$delay"
ESPRESSO_TEST_CREATE_DELAY_MS="$delay" PYTHONPATH="$here" \
  "$espresso" serve --runs-root "$runs_root" --port "$port" \
  >/tmp/g55_serve.log 2>&1 &
server_pid=$!
cleanup() { kill "$server_pid" 2>/dev/null || true; rm -rf "$runs_root"; }
trap cleanup EXIT

# wait for the server to answer
for _ in $(seq 1 40); do
  if curl -s -o /dev/null "http://127.0.0.1:$port/"; then break; fi
  sleep 0.25
done

echo "==> running browser latency validation"
COCKPIT_URL="http://127.0.0.1:$port" node "$here/web/scripts/validate_55_latency.mjs"
