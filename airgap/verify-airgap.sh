#!/usr/bin/env bash
# verify-airgap.sh — Prove zero runtime egress to public IPs.
#
# What this proves:
#   1. Containerlab nodes all have image-pull-policy: Never (no runtime pull).
#   2. Telemetry stack images are present locally (docker compose won't pull).
#   3. During a ~30s capture window on ALL interfaces (`tcpdump -i any`), no
#      packet with a container source IP is seen heading to a public dest.
#      `-i any` is load-bearing: it sees the packet on the docker/clab bridge
#      BEFORE the host's MASQUERADE rule rewrites the source, which a capture
#      on eth0 alone never can (there the src is already the host's own IP).
#   4. No `docker pull` happened during this script's run (docker events).
#
# Limits (ponytail: honest about what we don't check):
#   - DNS queries to an internal resolver appear as RFC1918 traffic and are
#     correctly excluded from the "public egress" filter.
#   - Does not enumerate every process inside containers; only monitors network.
#   - A 30s window cannot catch a phone-home whose interval is hours (Loki/
#     Grafana usage reporting) — those are disabled in config instead.
set -euo pipefail

PASS=0
FAIL=0
START_EPOCH=$(date +%s)

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

# ── 3. Runtime egress capture (all interfaces) ───────────────────────────────
hdr "3. Runtime egress: tcpdump -i any for container→public traffic (30s)"
# We filter for packets SOURCED from Docker container subnets going to public IPs.
# Container networks: 172.17.0.0/16 (docker bridge), 172.20.20.0/24 (clab).
# Capture MUST be on `any`, not eth0: the host SNATs both subnets on egress
# (iptables MASQUERADE), so on eth0 the source is already the host address and
# a src-net filter can never match — an eth0 capture is a guaranteed-empty
# no-op. On `any` the bridge-side copy still carries the container source.
CONTAINER_SRC="(src net 172.17.0.0/16 or src net 172.20.20.0/24)"
PUBLIC_DST="(not dst net 10.0.0.0/8 and not dst net 172.16.0.0/12 and not dst net 192.168.0.0/16 and not dst net 127.0.0.0/8 and not dst net 169.254.0.0/16 and not dst net 100.64.0.0/10 and not dst net 239.0.0.0/8)"
EGRESS_FILTER="ip and ${CONTAINER_SRC} and ${PUBLIC_DST}"

CAPTURE_SECS=30
CAPTURE_FILE="/tmp/airgap_egress_$$.pcap"

echo "  Capturing on all interfaces for ${CAPTURE_SECS}s ..."
echo "  Filter: container src → public dst"

# A capture with nothing running proves nothing — say so instead of passing.
RUNNING=$(docker ps -q | wc -l)
if [[ "$RUNNING" -eq 0 ]]; then
  fail "No containers running — runtime egress is UNVERIFIED (start the lab first)"
elif ! command -v tcpdump &>/dev/null; then
  fail "tcpdump not installed — runtime egress is UNVERIFIED (apt-get install tcpdump)"
else
  echo "  (${RUNNING} container(s) running)"
  timeout "${CAPTURE_SECS}" tcpdump -i any -w "$CAPTURE_FILE" \
    -q --no-promiscuous-mode \
    "${EGRESS_FILTER}" 2>/dev/null || true

  # tcpdump writes the pcap header the moment the capture starts, so an empty
  # or absent file means the capture never ran — never score that as a pass.
  if [[ ! -s "$CAPTURE_FILE" ]]; then
    fail "tcpdump wrote no pcap — capture did not run, egress is UNVERIFIED"
  elif ! PKT_COUNT=$(tcpdump -r "$CAPTURE_FILE" --count 2>/dev/null | grep -oP '^\d+'); then
    fail "pcap unreadable — capture result is UNVERIFIED"
  elif [[ "$PKT_COUNT" -eq 0 ]]; then
    ok "Zero container→public packets in ${CAPTURE_SECS}s (lab is air-gapped at runtime)"
  else
    fail "${PKT_COUNT} container→public packets detected — investigate with:"
    echo "         tcpdump -i any -n '${EGRESS_FILTER}'"
  fi
  rm -f "$CAPTURE_FILE"
fi

# ── 4. No image pull during this run ─────────────────────────────────────────
hdr "4. Sanity: no 'docker pull' during this run"
# dockerd emits an `image pull` event per pull (client-side `docker pull` is a
# separate process, so grepping dockerd's argv — the old check — never matched).
PULLS=$(docker events --since "$START_EPOCH" --until 0s \
          --filter type=image --filter event=pull 2>/dev/null | wc -l)
if [[ "$PULLS" -gt 0 ]]; then
  fail "${PULLS} image pull event(s) since this script started:"
  docker events --since "$START_EPOCH" --until 0s --filter type=image --filter event=pull 2>/dev/null | sed 's/^/         /'
else
  ok "No image pull events since ${START_EPOCH} ($(date -d "@${START_EPOCH}" '+%H:%M:%S'))"
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
