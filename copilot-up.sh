#!/usr/bin/env bash
# copilot-up.sh — bring up the 3 copilot runtime procs (api + predictor + forensic trigger)
# and verify they are actually live, so the ADR-0014 pipeline runs continuously against the lab:
# PA-emulator predicts every ~predict_interval_s -> alert -> forensic trigger opens a case.
# Mirrors sim-up.sh. Idempotent: safe to re-run if a proc is already up.
#
# Scope: copilot procs ONLY. The lab (dataapi :8000 + sim) is assumed already up via
# noc-lab.service / sim-up.sh; this script FAILS FAST if dataapi is absent.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

say() { echo "== $* =="; }

# All 3 procs MUST share ONE ledger file, else the trigger polls an empty ledger the predictor
# never wrote to. Pin it (+ the trigger's cursor) here so every child inherits the same path.
export COPILOT_LEDGER_PATH="${COPILOT_LEDGER_PATH:-$PWD/ledger.db}"
export COPILOT_CURSOR_PATH="${COPILOT_CURSOR_PATH:-$PWD/forensic-cursor.json}"
DATAAPI_URL="${COPILOT_DATAAPI_URL:-http://127.0.0.1:8000}"
INTERVAL=$(python3 -c 'from copilot.config import load; print(load().predict_interval_s)')

# ---- preflight: the lab must be serving before any copilot proc is worth starting ----
say "preflight dataapi ($DATAAPI_URL)"
curl -sf -o /dev/null "$DATAAPI_URL/topology" || {
  echo "  FAIL: dataapi is not serving at $DATAAPI_URL — bring the lab up first (sim-up.sh / noc-lab.service)"
  exit 1
}

# ---- start the 3 procs (nohup-background, house style; dataapi in sim-up.sh does the same) ----
start() {  # start <name> <pgrep-pattern> <logfile> <cmd...>
  local name="$1" pat="$2" log="$3"; shift 3
  if pgrep -f "$pat" >/dev/null; then say "$name already running"; return; fi
  say "starting $name"
  nohup "$@" > "$log" 2>&1 & disown
}
if ! curl -sf -o /dev/null http://127.0.0.1:8100/openapi.json; then
  start "api (uvicorn :8100)" 'copilot.api.app:app' /tmp/copilot-api.log \
        uvicorn copilot.api.app:app --host 127.0.0.1 --port 8100
  sleep 3
else
  say "api already serving on :8100"
fi
start "predictor"        'copilot.emulator.predictor' /tmp/copilot-predictor.log python3 -m copilot.emulator.predictor
start "forensic trigger" 'copilot.forensic.trigger'   /tmp/copilot-trigger.log   python3 -m copilot.forensic.trigger

# ---- verify ----
say "verify"
fail=0
check() {  # check <label> <url>
  local code; code=$(curl -s -o /dev/null -w '%{http_code}' "$2" || echo 000)
  printf '%-34s %s\n' "$1" "$code"
  [ "$code" = "200" ] || { echo "  FAIL: $1"; fail=1; }
}
alive() {  # alive <label> <pgrep-pattern>
  if pgrep -f "$2" >/dev/null; then printf '%-34s %s\n' "$1" "up"
  else printf '%-34s %s\n' "$1" "DOWN"; echo "  FAIL: $1 not running"; fail=1; fi
}
check "dataapi /topology"          "$DATAAPI_URL/topology"
check "api /openapi.json (:8100)"  http://127.0.0.1:8100/openapi.json
alive "predictor loop"             'copilot.emulator.predictor'
alive "forensic trigger loop"      'copilot.forensic.trigger'

# predictor LIVENESS heartbeat: prove the LOOP runs, not just that the PID exists — a fresh
# Prediction Record must land within one poll interval. A fetch-once/stale loop (the bug this
# ticket closes) would write nothing for a fault that is currently active.
say "predictor heartbeat (waiting one interval + slack: $((INTERVAL + 3))s)"
before=$(python3 - <<'PY'
import os, sqlite3
p = os.environ["COPILOT_LEDGER_PATH"]
print(sqlite3.connect(p).execute("SELECT COUNT(*) FROM ledger WHERE type='prediction'").fetchone()[0] if os.path.exists(p) else 0)
PY
)
sleep "$((INTERVAL + 3))"
python3 - "$before" "$DATAAPI_URL" <<'PY'
import os, sqlite3, sys, json, urllib.request
from datetime import datetime, timezone
before = int(sys.argv[1]); dataapi = sys.argv[2]; p = os.environ["COPILOT_LEDGER_PATH"]
after = sqlite3.connect(p).execute("SELECT COUNT(*) FROM ledger WHERE type='prediction'").fetchone()[0] if os.path.exists(p) else 0
if after > before:
    print(f"predictor heartbeat                OK ({before}->{after} prediction records)"); sys.exit(0)
# no new record: honest only if there is genuinely nothing to predict right now. If a fault IS
# active and the loop still wrote nothing, the loop is stuck/stale -> fail loudly.
now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
rows = json.load(urllib.request.urlopen(dataapi.rstrip("/") + "/labels", timeout=10)).get("rows", [])
active = [r for r in rows if r.get("t_start") and r.get("t_end") and r["t_start"] <= now <= r["t_end"]]
if active:
    print(f"predictor heartbeat                FAIL: {len(active)} fault(s) active but no fresh record"); sys.exit(1)
# ponytail: with no active fault the loop correctly writes nothing, so a record can't prove the
# loop ran — the PID check above stands. Ceiling: inject a synthetic probe fault for a hard
# liveness proof even in a quiet lab.
print("predictor heartbeat                idle (no active fault; PID alive proves the proc, loop unverified)")
PY
[ "${PIPESTATUS[0]:-0}" = "0" ] || fail=1

echo
if [ "$fail" -eq 0 ]; then
  say "copilot up. api: http://127.0.0.1:8100  logs: /tmp/copilot-{api,predictor,trigger}.log"
else
  say "one or more checks FAILED — see above"
  exit 1
fi
