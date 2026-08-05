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

# predictor LIVENESS heartbeat: prove the LOOP runs, not just that the PID exists (a stuck/stale
# loop keeps its PID). Inject a SYNTHETIC low-severity probe fault into the /labels glob, then
# require a fresh Prediction Record within one poll interval — only a loop that actually re-fetched
# /labels and ticked can produce it. Low severity => decision.alert is false (ADR-0003), so the
# forensic trigger opens NO case for the probe; the probe label + its ledger rows are removed on
# every exit path (trap). ponytail: the probe writes to the ground-truth labels dir for ~one
# interval; it sorts last (zzz-) so a real active fault still wins primary. Ceiling: a dedicated
# dry-run predict endpoint if writing the labels dir at bring-up ever bites.
PROBE_LABEL="faults/labels/zzz-copilot-heartbeat-probe.jsonl"
_clean_probe() {
  rm -f "$PROBE_LABEL"
  python3 - <<'PY' 2>/dev/null || true
import os, sqlite3
p = os.environ["COPILOT_LEDGER_PATH"]
if os.path.exists(p):
    with sqlite3.connect(p) as db:
        db.execute("DELETE FROM ledger WHERE device='hb_probe'")
PY
}
trap _clean_probe EXIT
_clean_probe                                    # clear any leak from a prior crashed run
before=$(python3 - <<'PY'
import os, sqlite3
p = os.environ["COPILOT_LEDGER_PATH"]
print(sqlite3.connect(p).execute("SELECT COUNT(*) FROM ledger WHERE type='prediction'").fetchone()[0] if os.path.exists(p) else 0)
PY
)
python3 - <<'PY'                                # drop a now-spanning low-sev probe into the glob
import json
from datetime import datetime, timezone, timedelta
now = datetime.now(timezone.utc); z = lambda dt: dt.strftime("%Y-%m-%dT%H:%M:%SZ")
open("faults/labels/zzz-copilot-heartbeat-probe.jsonl", "w").write(json.dumps({
    "scenario_id": "copilot-heartbeat-probe", "type": "congestion", "device": "hb_probe",
    "target": {"device": "hb_probe"}, "severity": "low", "t_start": z(now - timedelta(seconds=60)),
    "t_impact": z(now), "t_end": z(now + timedelta(seconds=600)), "lead_time": 60.0,
    "probe": "latency_ms", "baseline_value": 10.0, "impact_value": 40.0, "signature": "hb"}) + "\n")
PY
say "predictor heartbeat (synthetic probe; waiting $((INTERVAL + 3))s)"
sleep "$((INTERVAL + 3))"
after=$(python3 - <<'PY'
import os, sqlite3
p = os.environ["COPILOT_LEDGER_PATH"]
print(sqlite3.connect(p).execute("SELECT COUNT(*) FROM ledger WHERE type='prediction'").fetchone()[0] if os.path.exists(p) else 0)
PY
)
if [ "${after:-0}" -gt "${before:-0}" ]; then
  printf '%-34s %s\n' "predictor heartbeat" "OK (fresh record within one interval)"
else
  printf '%-34s %s\n' "predictor heartbeat" "FAIL"
  echo "  FAIL: no fresh Prediction Record within one interval — predictor loop stuck/stale"
  fail=1
fi

echo
if [ "$fail" -eq 0 ]; then
  say "copilot up. api: http://127.0.0.1:8100  logs: /tmp/copilot-{api,predictor,trigger}.log"
else
  say "one or more checks FAILED — see above"
  exit 1
fi
