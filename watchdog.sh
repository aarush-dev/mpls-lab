#!/usr/bin/env bash
# watchdog.sh — bring up the whole system (lab + copilot) then poll every service.
# A service down N consecutive polls (default 30s grace / POLL_S interval) gets its
# bring-up script re-run (sim-up.sh / copilot-up.sh are both idempotent — safe to re-run).
# ponytail: curl+pgrep poll loop, no process supervisor dep (systemd/supervisord already
# owns THIS script via noc-lab.service pattern) — add one if false-positive flaps show up.
set -uo pipefail
cd "$(dirname "$(readlink -f "$0")")"

POLL_S="${POLL_S:-5}"
GRACE_S="${GRACE_S:-30}"
LOG=/tmp/watchdog.log
GRACE_TICKS=$(( (GRACE_S + POLL_S - 1) / POLL_S ))

log() { echo "$(date -Is) $*" | tee -a "$LOG"; }

declare -A fail_count

# name -> check command (exit 0 = healthy)
declare -A CHECKS=(
  [dataapi]="curl -sf -o /dev/null http://localhost:8000/topology"
  [victoriametrics]="curl -sf -o /dev/null http://localhost:8428/health"
  [loki]="curl -sf -o /dev/null http://localhost:3100/ready"
  [grafana]="curl -sf -o /dev/null http://localhost:3000/a/mplslab-noccopilot-app"
  [copilot_api]="curl -sf -o /dev/null http://127.0.0.1:8100/openapi.json"
  [copilot_predictor]="pgrep -f copilot.emulator.predictor >/dev/null"
  [copilot_trigger]="pgrep -f copilot.forensic.trigger >/dev/null"
  [lab_wired]="docker exec clab-sdwan_mpls_noc-p1 ip -br link show eth1 >/dev/null 2>&1"
)

restart_lab()     { log "restarting LAB (sim-up.sh)";     sudo ./sim-up.sh     >>"$LOG" 2>&1; }
restart_copilot() { log "restarting COPILOT (copilot-up.sh)"; ./copilot-up.sh >>"$LOG" 2>&1; }

log "watchdog starting: poll=${POLL_S}s grace=${GRACE_S}s"
restart_lab
restart_copilot

while true; do
  sleep "$POLL_S"
  for name in "${!CHECKS[@]}"; do
    if eval "${CHECKS[$name]}"; then
      fail_count[$name]=0
    else
      fail_count[$name]=$(( ${fail_count[$name]:-0} + 1 ))
      log "DOWN: $name (${fail_count[$name]}/${GRACE_TICKS} ticks)"
      if [ "${fail_count[$name]}" -ge "$GRACE_TICKS" ]; then
        case "$name" in
          copilot_api|copilot_predictor|copilot_trigger) restart_copilot ;;
          *) restart_lab ;;
        esac
        fail_count[$name]=0
      fi
    fi
  done
done
