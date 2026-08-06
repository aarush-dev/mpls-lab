#!/usr/bin/env bash
# #128 follow-up: the 100-run audit never set session_id/case_id/workspace/skills on any
# request (bare {"question": ...} only) -- so runs 61-70 tested prose reactions to a user
# CLAIMING prior context, never the real API mechanics. This is a 4-request probe that
# actually sets those fields, targeting the exact gaps the issue names.
set -uo pipefail
cd /root/LAB
DIR=scratchpad/harness_probe2
mkdir -p "$DIR/runs"

post() {
  local name="$1" body="$2"
  echo "$body" > "$DIR/runs/req_$name.json"
  code=$(curl -sN -o "$DIR/runs/out_$name.sse" -w '%{http_code}' -X POST localhost:8100/chat \
    -H 'Content-Type: application/json' -d "$body")
  echo "$code" > "$DIR/runs/out_$name.code"
  echo "== $name -> HTTP $code =="
}

# 1. unknown session_id -- expect: does it 404, or silently create a new persisted session?
post unknown_session_id \
  '{"question": "what devices are having issues?", "session_id": "sid-typo-not-real-128"}'

# 2. follow_up (real case_id) with `end` past the case's frozen T_snapshot -- expect: 400
#    (FilterError, ADR-0002 freeze guard). Case + window from GET /cases/<id>.
CASE_ID=$(curl -s localhost:8100/cases | python3 -c "import json,sys; print(json.load(sys.stdin)[0]['id'])")
post case_end_past_snapshot \
  "$(python3 -c "import json; print(json.dumps({'question': 'what happened after this?', 'case_id': '$CASE_ID', 'end': 9999999999}))")"

# 3. workspace: true with NO session_id -- expect: 200, but bash silently unavailable
post workspace_without_session \
  '{"question": "run a quick sanity check with bash", "workspace": true}'

# 4. unknown skill name -- expect: 200, silent no-op (no error, no event)
post unknown_skill_name \
  '{"question": "investigate ce_branch1 for bgp issues", "skills": ["not_a_real_skill_xyz"]}'

echo "done -- see $DIR/runs/out_*.code and *.sse"
