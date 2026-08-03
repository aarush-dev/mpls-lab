#!/usr/bin/env bash
# start.sh — start NOC data API on localhost:8000
# Run from anywhere; resolves path relative to this script.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"
# Single worker: the /faults/* live-injection registry is in-process memory, so a
# multi-worker setup would split it (inject on one worker, /faults/active|revert on
# another). Reads are fast + localhost-only, so one worker is plenty.
exec uvicorn app:app --host 127.0.0.1 --port 8000 --workers 1
