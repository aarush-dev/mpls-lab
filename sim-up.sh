#!/usr/bin/env bash
# sim-up.sh — bring up the full NOC copilot sim + verify it's actually serving.
# Run from repo root. Idempotent: safe to re-run if some layer is already up.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

say() { echo "== $* =="; }

# 1. Containerlab topology (deploy if absent OR unwired)
# A container existing is NOT proof the lab is healthy: a host/docker restart keeps the
# containers but destroys the containerlab veth links (only eth0/lo survive), so the whole
# control plane stays down. Probe a data-plane link (p1:eth1) to catch that; if it is gone,
# destroy + redeploy re-wires everything and re-runs the exec hooks.
wired() { docker exec clab-sdwan_mpls_noc-p1 ip -br link show eth1 >/dev/null 2>&1; }
if ! docker ps --format '{{.Names}}' | grep -q '^clab-sdwan_mpls_noc-p1$'; then
  say "deploying containerlab topology"
  (cd topology && sudo containerlab deploy -t clab.yml)
elif ! wired; then
  say "topology up but UNWIRED (restart lost the veths) -- destroy + redeploy"
  (cd topology && sudo containerlab destroy -t clab.yml --cleanup && sudo containerlab deploy -t clab.yml)
else
  say "containerlab topology already up + wired"
fi

# 2. Telemetry stack (Telegraf/VM/Loki/Kafka/etc) — tele-grafana stays down on
# purpose, the plugin Grafana below owns :3000 (see grafana-ui-live-integration memory).
say "telemetry compose up"
(cd telemetry && docker compose up -d --no-deps $(docker compose config --services | grep -v '^grafana$'))

# 3. Controller + trafficgen + fault sidecars (same compose file, separate services)
say "controller/trafficgen up"
(cd telemetry && docker compose up -d controller trafficgen env-metrics ldp-metrics 2>/dev/null || true)

# 4. Plugin Grafana (port 3000, mounts grafana ui/plugin/dist)
if ! curl -sf -o /dev/null http://localhost:3000/a/mplslab-noccopilot-app; then
  say "starting plugin grafana on :3000"
  (cd "grafana ui" && docker compose -p noc-plugin up -d --no-deps grafana)
else
  say "plugin grafana already serving on :3000"
fi

# 5. dataapi (FastAPI, workers=1 — fault registry is in-process state)
if ! curl -sf -o /dev/null http://localhost:8000/topology; then
  say "starting dataapi on :8000"
  (cd dataapi && nohup bash start.sh > /tmp/dataapi.log 2>&1 & disown)
  sleep 3
else
  say "dataapi already serving on :8000"
fi

# ---- verify ----
say "verify"
fail=0
check() {
  local label="$1" url="$2" code
  code=$(curl -s -o /dev/null -w '%{http_code}' "$url" || echo 000)
  printf '%-30s %s\n' "$label" "$code"
  [ "$code" = "200" ] || { echo "  FAIL: $label"; fail=1; }
}
check "dataapi /topology"        http://localhost:8000/topology
check "dataapi /faults/active"   http://localhost:8000/faults/active
check "victoriametrics /health"  http://localhost:8428/health
check "loki /ready"              http://localhost:3100/ready
check "plugin grafana"           http://localhost:3000/a/mplslab-noccopilot-app

echo
docker ps --format 'table {{.Names}}\t{{.Status}}' | grep -E 'noc-|tele-|clab-' | grep -vi exited > /dev/null && echo "containers: $(docker ps -q | wc -l) up"

if [ "$fail" -eq 0 ]; then
  say "sim up. Grafana: http://localhost:3000/a/mplslab-noccopilot-app  dataapi: http://localhost:8000"
else
  say "one or more checks FAILED — see above"
  exit 1
fi
