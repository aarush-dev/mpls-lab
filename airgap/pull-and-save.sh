#!/usr/bin/env bash
# pull-and-save.sh — Pull (if needed) + docker save | xz every lab image.
# Idempotent: skips images already present in images/.
# Output: airgap/images/<name>.tar.xz + airgap/manifest.txt
#
# ponytail: no temp files, pipe save|xz directly to avoid double-disk usage.
#           manifest written atomically at the end.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BUNDLE_DIR="${SCRIPT_DIR}/images"
MANIFEST="${SCRIPT_DIR}/manifest.txt"

mkdir -p "$BUNDLE_DIR"

# ── Full image list ──────────────────────────────────────────────────────────
# Local builds (must exist — no registry to pull from)
LOCAL_IMAGES=(
  "frr-node:0.1"
  "noc-controller:0.1"
  "noc-trafficgen:0.1"
)

# Registry images (pinned tags)
REGISTRY_IMAGES=(
  "victoriametrics/victoria-metrics:v1.103.0"
  "grafana/grafana:11.1.0"
  "telegraf:1.31.1"
  "pmacct/nfacctd:v1.7.9"
  "grafana/loki:3.1.0"
  "grafana/promtail:3.1.0"
  "wbitt/network-multitool:alpine-minimal"
  "quay.io/frrouting/frr:10.5.1"
)

ALL_IMAGES=("${LOCAL_IMAGES[@]}" "${REGISTRY_IMAGES[@]}")

# ── Pull registry images if not present ─────────────────────────────────────
echo "=== Ensuring registry images are present ==="
for img in "${REGISTRY_IMAGES[@]}"; do
  if docker image inspect "$img" &>/dev/null; then
    echo "  [cached] $img"
  else
    echo "  [pulling] $img"
    docker pull "$img"
  fi
done

# ── Save each image ──────────────────────────────────────────────────────────
echo ""
echo "=== Saving images to ${BUNDLE_DIR} ==="
MANIFEST_LINES=()
for img in "${ALL_IMAGES[@]}"; do
  # Sanitise tag → filename: replace / and : with _
  fname="${img//\//_}"
  fname="${fname//:/_}.tar.xz"
  out="${BUNDLE_DIR}/${fname}"

  if [[ -f "$out" ]]; then
    echo "  [skip] ${fname} already exists"
  else
    echo -n "  [save] ${img} → ${fname} ... "
    docker save "$img" | xz -T0 -3 > "$out"
    echo "done"
  fi

  digest=$(docker inspect --format='{{index .RepoDigests 0}}' "$img" 2>/dev/null) || digest=""
  if [[ -z "$digest" ]]; then
    imgid=$(docker inspect --format='{{.Id}}' "$img" 2>/dev/null) || imgid="unknown"
    digest="local:${imgid:0:19}..."
  fi
  size=$(du -sh "$out" | cut -f1)
  MANIFEST_LINES+=("${img}|${digest}|${size}|${fname}")
done

# ── Write manifest ───────────────────────────────────────────────────────────
{
  printf "%-55s %-80s %6s  %s\n" "IMAGE" "DIGEST" "SIZE" "FILE"
  printf '%s\n' "$(printf '=%.0s' {1..170})"
  for line in "${MANIFEST_LINES[@]}"; do
    IFS='|' read -r img digest size fname <<< "$line"
    printf "%-55s %-80s %6s  %s\n" "$img" "$digest" "$size" "$fname"
  done
} | tee "$MANIFEST"

echo ""
echo "Manifest written to: ${MANIFEST}"
echo "Total bundle size: $(du -sh "${BUNDLE_DIR}" | cut -f1)"
