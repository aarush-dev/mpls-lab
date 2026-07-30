#!/usr/bin/env bash
# start.sh — run the Kafka telemetry bridge against the live stack.
# Mirrors dataapi/start.sh: host process, resolves paths relative to itself.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"
# Host processes reach the broker on the mapped HOST listener, not the in-lab one.
export KAFKA_BOOTSTRAP="${KAFKA_BOOTSTRAP:-127.0.0.1:29092}"
exec python3 bridge.py "$@"
