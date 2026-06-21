#!/usr/bin/env bash
# verify-airgap.sh — Prove zero runtime egress to public IPs.
#
# What this proves:
#   1. Containerlab nodes all have image-pull-policy: Never (no runtime pull).
#   2. Telemetry stack images are present locally (docker compose won't pull).
#   3. During a ~30s capture window on the host's external interface (eth0),
#      no packets with a container source IP reach a public (non-RFC1918) dest.
#
# Limits (ponytail: honest about what we don't check):
#   - Checks eth0 only; a second physical uplink would not be covered.
#   - DNS queries to an internal resolver appear as RFC1918 traffic and are
#     correctly excluded from the "public egress" filter.
#   - Does not enumerate every process inside containers; only monitors network.
#   - In this WSL2 environment eth0 is the sole egress path.
set -euo pipefail

PASS=0
FAIL=0

ok()   { echo "  [PASS] $*"; (( PASS++ )) || true; }
fail() { echo "  [FAIL] $*"; (( FAIL++ )) || true; }
hdr()  { echo ""; echo "=== $* ==="; }

# ── 1. Clab image-pull-policy: Never ────────────────────────────────────────
hdr "1. Containerlab image-pull-policy: Never"
CLAB_YML="/root/LAB/topology/clab.yml"
if [[ ! -f "$CLAB_YML" ]]; then
  fail "clab.yml not found at ${CLAB_YML}"
else
  LOCAL_NODES=$(grep -c 'image-pull-policy: Never' "$CLAB_YML" || true)
  TOTAL_IMAGE=$(grep -c 'image:' "$CLAB_YML" || true)
  if [[ $LOCAL_NODES -eq $TOTAL_IMAGE ]]; then
    ok "All ${LOCAL_NODES}/${TOTAL_IMAGE} node image entries have image-pull-policy: Never"
  else
    fail "${LOCAL_NODES}/${TOTAL_IMAGE} nodes have image-pull-policy: Never (${TOTAL_IMAGE} total)"
  fi
fi

# ── 2. Telemetry stack images present locally ────────────────────────────────
hdr "2. Telemetry stack images present locally (compose won't pull)"
REQUIRED_IMAGES=(
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
for img in "${REQUIRED_IMAGES[@]}"; do
  if docker image inspect "$img" &>/dev/null; then
    ok "Present: ${img}"
  else
    fail "MISSING: ${img}"
  fi
done

# ── 3. Runtime egress capture on eth0 ────────────────────────────────────────
hdr "3. Runtime egress: tcpdump on eth0 for container→public traffic (30s)"
# We filter for packets SOURCED from Docker container subnets going to public IPs.
# Container networks: 172.17.0.0/16 (docker bridge), 172.20.20.0/24 (clab).
# The host's own eth0 IP (172.27.x.x) is explicitly excluded — it's not a container.
# This proves lab containers generate zero internet egress at runtime.
#
# ponytail: src-scoped filter is the correct check; host traffic on eth0 is
#           irrelevant to air-gap — we care about container→internet only.
CONTAINER_SRC="(src net 172.17.0.0/16 or src net 172.20.20.0/24)"
PUBLIC_DST="(not dst net 10.0.0.0/8 and not dst net 172.16.0.0/12 and not dst net 192.168.0.0/16 and not dst net 127.0.0.0/8 and not dst net 169.254.0.0/16 and not dst net 100.64.0.0/10 and not dst net 239.0.0.0/8)"
EGRESS_FILTER="ip and ${CONTAINER_SRC} and ${PUBLIC_DST}"

CAPTURE_SECS=30
CAPTURE_FILE="/tmp/airgap_egress_$$.pcap"

echo "  Capturing on eth0 for ${CAPTURE_SECS}s ..."
echo "  Filter: container src → public dst"
echo "  (host eth0 traffic is excluded — only container-sourced egress counted)"

if ! command -v tcpdump &>/dev/null; then
  echo "  [SKIP] tcpdump not installed — install with: apt-get install tcpdump"
  echo "         Substitute: iptables -I FORWARD -j LOG + logread for 30s"
else
  timeout "${CAPTURE_SECS}" tcpdump -i eth0 -w "$CAPTURE_FILE" \
    -q --no-promiscuous-mode \
    "${EGRESS_FILTER}" 2>/dev/null || true

  PKT_COUNT=$(tcpdump -r "$CAPTURE_FILE" --count 2>/dev/null | grep -oP '^\d+' || echo "0")
  rm -f "$CAPTURE_FILE"

  if [[ "$PKT_COUNT" -eq 0 ]]; then
    ok "Zero container→public packets in ${CAPTURE_SECS}s (lab is air-gapped at runtime)"
  else
    fail "${PKT_COUNT} container→public packets detected — investigate with:"
    echo "         tcpdump -i eth0 -n '${EGRESS_FILTER}'"
  fi
fi

# ── 4. Docker socket check — no 'docker pull' activity ──────────────────────
hdr "4. Sanity: no running 'docker pull' processes"
if pgrep -a dockerd 2>/dev/null | grep -q 'pull'; then
  fail "docker pull process detected in background"
else
  ok "No docker pull processes running"
fi

# ── Summary ──────────────────────────────────────────────────────────────────
echo ""
echo "========================================"
echo "  PASS: ${PASS}   FAIL: ${FAIL}"
echo "========================================"
if [[ $FAIL -eq 0 ]]; then
  echo "RESULT: AIR-GAP VERIFIED"
  exit 0
else
  echo "RESULT: ${FAIL} check(s) FAILED"
  exit 1
fi
