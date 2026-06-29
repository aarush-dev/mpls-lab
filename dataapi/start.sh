#!/usr/bin/env bash
# start.sh — start NOC data API on localhost:8000
# Run from anywhere; resolves path relative to this script.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"
exec uvicorn app:app --host 127.0.0.1 --port 8000 --workers 2
