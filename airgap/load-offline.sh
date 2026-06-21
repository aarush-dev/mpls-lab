#!/usr/bin/env bash
# load-offline.sh — Load every *.tar.xz bundle into Docker on an air-gapped host.
# Run this before `clab deploy` or `docker compose up` on the offline host.
#
# ponytail: streams xz→load directly; no temp extraction needed.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BUNDLE_DIR="${SCRIPT_DIR}/images"

if [[ ! -d "$BUNDLE_DIR" ]]; then
  echo "ERROR: bundle dir not found: ${BUNDLE_DIR}" >&2
  exit 1
fi

shopt -s nullglob
BUNDLES=("${BUNDLE_DIR}"/*.tar.xz)
if [[ ${#BUNDLES[@]} -eq 0 ]]; then
  echo "ERROR: no *.tar.xz files found in ${BUNDLE_DIR}" >&2
  exit 1
fi

echo "=== Loading ${#BUNDLES[@]} image bundle(s) into Docker ==="
LOADED=0
FAILED=0
for bundle in "${BUNDLES[@]}"; do
  fname="$(basename "$bundle")"
  echo -n "  [load] ${fname} ... "
  if xz -d < "$bundle" | docker load 2>&1 | grep -oP '(?<=Loaded image: ).*' | tee /dev/stderr; then
    (( LOADED++ )) || true
    echo "  OK"
  else
    echo "  FAILED"
    (( FAILED++ )) || true
  fi
done

echo ""
echo "=== Verification: confirming expected tags present ==="
EXPECTED=(
  "frr-node:0.1"
  "noc-controller:0.1"
  "noc-trafficgen:0.1"
  "victoriametrics/victoria-metrics:v1.103.0"
  "grafana/grafana:11.1.0"
  "telegraf:1.31.1"
  "pmacct/nfacctd:v1.7.9"
  "grafana/loki:3.1.0"
  "grafana/promtail:3.1.0"
  "wbitt/network-multitool:alpine-minimal"
  "quay.io/frrouting/frr:10.5.1"
)
MISSING=0
for img in "${EXPECTED[@]}"; do
  if docker image inspect "$img" &>/dev/null; then
    echo "  [ok]      ${img}"
  else
    echo "  [MISSING] ${img}"
    (( MISSING++ )) || true
  fi
done

echo ""
if [[ $MISSING -eq 0 ]]; then
  echo "All expected images present. Host is ready for offline deploy."
  exit 0
else
  echo "ERROR: ${MISSING} image(s) missing after load. Check bundle integrity." >&2
  exit 1
fi
