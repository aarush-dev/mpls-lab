# Copilot — Forensic & E2E

## Purpose

Forensic (`copilot/forensic/`) is the part of the copilot that fires when a Prediction Record
alerts: it freezes the concerned observability window to disk, runs a real agent investigation
against that frozen snapshot only (never live data again), and writes a `case.md` report plus a
resumable chat history. It sits downstream of the PA/emulator predict-loop (which writes
Prediction Records to the Event Ledger) and upstream of the API (`/cases`, `/cases/{id}`, and
`/chat` with `case_id` set) and the Grafana UI case dashboard.

E2E (`copilot/e2e/`) is not product code — it is the integration harness that proves the whole
chain works against real backends (real containerlab faults, real dataapi, real LLM, real KB).
`harness.py` drives scripted questions through `investigate()` over a live stack and writes
`REPORT.md`; `pipeline.py` fires two overlapping real faults through the real emulator + Forensic
trigger to prove the issue-48 cause-keyed-alert_id invariant (2 faults → exactly 2 cases); `ask.py`
is a one-question manual driver reusing the harness's live-stack setup.

## Entry points

- **Forensic trigger daemon** (`copilot/forensic/trigger.py:131` `_main`) — headless loop, wired
  by `copilot-up.sh` / `noc-copilot.service`, shares `ledger.db` with the predictor and API:
  ```
  python3 -m copilot.forensic.trigger
  ```
  Env: `COPILOT_LEDGER_PATH` (default `ledger.db`), `COPILOT_CURSOR_PATH` (default
  `forensic-cursor.json`), plus the standard `copilot.config`/`copilot.api.app` env (LLM, dataapi,
  cases dir). SIGTERM stops it cleanly (`trigger.py:154`).

- **`/cases`** (GET, `copilot/api/app.py:224`) — list open forensic cases (triage summary). Calls
  `copilot.forensic.chat.list_cases`.
  ```
  curl localhost:8000/cases
  ```
- **`/cases/{cid}`** (GET, `copilot/api/app.py:232`) — one case's `case.md` + `prediction.json` +
  chat ids. Calls `copilot.forensic.chat.read_case` via `resolve_case_dir`.
  ```
  curl localhost:8000/cases/alt_scn1__congestion
  ```
- **`/chat` with `case_id`** (POST, `copilot/api/app.py:259`) — a forensic follow-up turn, routed
  to `copilot.forensic.chat.follow_up` instead of a live `investigate()`. `session_id` picks the
  chat (default `INITIAL_CHAT`="initial"); `end` past the case's frozen `T_snapshot` raises 400 via
  `Filters.validate` (`copilot/adapter/contract.py:66`).
  ```
  curl -X POST localhost:8000/chat -H 'content-type: application/json' \
    -d '{"case_id":"alt_scn1__congestion","question":"what else touched this device?"}'
  ```

- **`python3 -m copilot.forensic.test_case`** / `test_trigger` / `test_chat` / `test_synthesis` —
  assert-based self-checks, no framework, run directly (see each file's `__main__`).

- **`python3 -m copilot.e2e.pipeline`** (`copilot/e2e/pipeline.py:210`) — fires two real
  overlapping faults, replays emulate→diagnose tick-by-tick over an isolated ledger, asserts the
  issue-48 invariant. Precondition: `./sim-up.sh && ./copilot-up.sh`.
  ```
  python3 -m copilot.e2e.pipeline
  python3 -m copilot.e2e.pipeline --scenario-b tunnel_degrade --out /tmp/run1
  ```
  Flags (`pipeline.py:196-205`): `--scenario-a`(congestion) `--target-a`(ce_branch1) `--dur-a`(60)
  `--scenario-b`(bgp_flap) `--target-b`(ce_branch1) `--dur-b`(180) `--severity`(high)
  `--out`(pipeline-run). Exits nonzero on invariant failure.

- **`python3 -m copilot.e2e.harness`** (`copilot/e2e/harness.py:229`) — self-check by default
  (`_selfcheck`, cheap, skips if no nim key / dataapi down); full 7-question live run with
  `COPILOT_E2E_LIVE=1`:
  ```
  COPILOT_E2E_LIVE=1 python3 -m copilot.e2e.harness
  ```
  Writes `copilot/e2e/REPORT.md` + `copilot/e2e/traces/<slug>.json` per question.

- **`python3 -m copilot.e2e.ask "<question>"`** (`copilot/e2e/ask.py:20`) — one question, live
  stack, prints the trace + answer to stdout. No args → `DEFAULT_Q` (`ask.py:17`).

## Modules

**`copilot/forensic/trigger.py`** — poll the Event Ledger for new alerting records, fire
`handle(record, window)` once per episode, persist a restart-safe cursor.
- `Cursor` (`trigger.py:39`) — `(ts, fired-at-ts set)` position, JSON-persisted.
- `_epoch(ts)` (`trigger.py:33`) — ISO `...Z` → epoch int.
- `_new_alerts(ledger, cursor)` (`trigger.py:76`) — yields not-yet-fired alerting records ascending
  by ts.
- `poll_once(cfg, ledger, cursor, handle) -> int` (`trigger.py:90`) — one tick: fire + advance
  cursor; catches and logs any `handle` exception, does NOT advance cursor on failure (retried next
  poll).
- `run_forensic(cfg, ledger, cursor, handle, *, stop_fn, sleep) -> int` (`trigger.py:115`) —
  sleep-loop driver, injectable `stop_fn`/`sleep` for tests.

**`copilot/forensic/case.py`** — the production `handle`: freeze window, write prediction.json,
run the initial investigation against a `ReplayAdapter`, write case.md.
- `case_id(record) -> str` (`case.py:50`) — sanitised `alert_id`, one case dir per id.
- `snapshot_window(live_adapter, record, window, window_dir, device=None)` (`case.py:126`) — drains
  metrics/events/flows to exhaustion (device-scoped) + captures topology blast radius, dumps to
  `window_dir`.
- `ReplayAdapter` (`case.py:143`) — a `ToolAdapter` over the frozen snapshot; `metrics`/`events`/
  `flows`/`hops_within`/`walk_topology` methods, no live backend.
- `investigate_record(record, question, window, adapter, ...)` (`case.py:189`) — wraps
  `copilot.agent.investigate` with the record's steer fields (`fault_type`, `is_abstain`,
  `drift_state`) extracted once.
- `case_severity(record) -> str` (`case.py:210`) — high/medium/low/unknown bucket off calibrated
  probability.
- `case_summary(record, cid) -> dict` (`case.py:222`) — the `/cases` list-row shape.
- `render_case_md(record, window, outcome, cid) -> str` (`case.py:230`) — structured header +
  agent's cited prose + trace footer.
- `_verdict_to_kb(record, md, cid, window, retriever, cfg)` (`case.py:262`) — embeds the finalised
  verdict as a KB `incident` Doc, gated by `cfg.ledger_to_kb`.
- `create_case(record, window, *, live_adapter, llm, cfg, cases_root="cases", ...) -> str`
  (`case.py:279`) — the full pipeline; idempotent per `case_id`.
- `make_handler(*, live_adapter, llm, cfg, cases_root="cases", ...)` (`case.py:337`) — binds deps,
  returns the `handle(record, window)` the trigger fires.

**`copilot/forensic/chat.py`** — multi-chat per case + frozen-window follow-ups.
- `case_chats(case_dir) -> SessionStore` (`chat.py:30`) — `cases/<id>/chats/<chat_id>/events.jsonl`.
- `list_cases(cases_root) -> list[dict]` (`chat.py:36`) — cases with a `case.md` present (complete
  marker).
- `read_case(case_dir) -> dict` (`chat.py:53`) — `{id, case_md, prediction, chats}`.
- `frozen_window(case_dir) -> WindowContext` (`chat.py:68`) — reloads the persisted `window.json`,
  `frozen=True`.
- `resolve_case_dir(cases_root, case_id) -> str` (`chat.py:76`) — sanitise + realpath-containment
  check on an untrusted case id; raises `ValueError` on escape/unknown.
- `follow_up(case_dir, chat_id, question, *, llm, cfg, requested_end=None, ...) -> Outcome`
  (`chat.py:93`) — one follow-up turn over the frozen window/replay adapter; rejects
  `requested_end` past `T_snapshot` via `Filters.validate`.

**`copilot/forensic/synthesis.py`** — concurrent-fault fan-out + master synthesis.
- `_fault_record(record, fault) -> dict` (`synthesis.py:42`) — per-co-fault view: device + top
  cause retargeted.
- `_attributed_cites(chat_id, outcome)` (`synthesis.py:65`) — sub-chat cites, id-prefixed
  `<chat_id>:<id>`.
- `master_synthesis(record, window, subs, *, llm, cfg) -> Outcome` (`synthesis.py:93`) — one master
  chat synthesising n sub-chats, gated with inherited cites as `prior_cites`.
- `synthesize_concurrent(case_dir, record, window, replays, *, primary, llm, cfg, ...)`
  (`synthesis.py:116`) — n_concurrent>1 fan-out: reuses the primary run as fault-0, investigates
  fault-1..n-1 each on its own frozen device window, then the master chat. Idempotent via the
  `MASTER_CHAT` chat's presence.

**`copilot/e2e/pipeline.py`** — live end-to-end validation of the issue-48 invariant.
- `_fire_faults(...)` (`pipeline.py:48`) — fires scenario A and B concurrently as real containerlab
  faults (threads), B outlives A.
- `_tick_grid(labels, step_s)` (`pipeline.py:69`) — UTC "now" stamps every `step_s` over the
  labels' combined time span.
- `run(scen_a, tgt_a, dur_a, scen_b, tgt_b, dur_b, severity, out)` (`pipeline.py:90`) — the 3-stage
  driver (inject → replay predict/trigger tick-by-tick → assert invariant), writes
  `checkpoint_{1,2,3}_*.json`, `run_manifest.json`, `ticks_full.jsonl`.

**`copilot/e2e/harness.py`** — live stack setup + scripted question runner.
- `setup(cfg=None)` (`harness.py:76`) — asserts nim profile resolves, dataapi reachable, KB seeds
  >0 docs, one model smoke; returns `(cfg, client, adapter, retriever, skills, kb_dir)`.
- `run_live(cfg=None) -> list[dict]` (`harness.py:109`) — drives `QUESTIONS` through
  `investigate()`, writes per-question trace JSON + `REPORT.md`.
- `_summarize(item, out, secs, crashed=None)` (`harness.py:142`) — classifies a result into a
  verdict: `crashed` / `gated (what's-missing)` / `stopped:<reason>` / `ask-back` / `cited answer` /
  `answer (uncited)`.
- `_assert_reachable(base_url)` (`harness.py:194`) — GETs `/topology`, asserts nodes present.
- `_selfcheck()` (`harness.py:212`) — cheap acceptance check, `__main__` default path.

**`copilot/e2e/ask.py`** — one-question manual driver, reuses `harness.setup()`.
- `main()` (`ask.py:20`) — parses argv as the question (or `DEFAULT_Q`), runs `investigate()`,
  prints the trace + answer.

## Parameters

| name | default | env-var/CLI-flag | units | what it controls | source (file:line) |
|---|---|---|---|---|---|
| `predict_interval_s` | 3 | `Config.predict_interval_s`; not env | seconds | trigger sleep-loop cadence (`run_forensic`) and E2E pipeline tick-grid step | `copilot/config.py:82` |
| `window_x_min` (X) | 10 | `Config.window_x_min` | minutes | forensic window width: `T_snapshot - X*60 .. T_snapshot` | `copilot/config.py:71`, `copilot/window/__init__.py:44` |
| `gate_min_evidence` (N) | 2 | `Config.gate_min_evidence` | count | pre-gate evidence floor for `master_synthesis`'s `run_gate` call | `copilot/config.py:73`, `copilot/forensic/synthesis.py:107` |
| `ledger_to_kb` | True | `Config.ledger_to_kb` | bool | gates `_verdict_to_kb` embedding the case verdict into the KB | `copilot/config.py:60`, `copilot/forensic/case.py:270` |
| `COPILOT_LEDGER_PATH` | `ledger.db` | env | path | Ledger file the trigger daemon shares with predictor/API | `copilot/forensic/trigger.py:148` |
| `COPILOT_CURSOR_PATH` | `forensic-cursor.json` | env | path | `Cursor` persistence file for the trigger daemon | `copilot/forensic/trigger.py:149` |
| `COPILOT_CASES_DIR` | `cases` | env | path | root of case dirs (`get_cases_root`, shared by trigger + API) | `copilot/api/app.py:143-147` |
| `cases_root` (kwarg) | `"cases"` | function arg | path | root passed to `create_case`/`make_handler` | `copilot/forensic/case.py:279`, `:337` |
| `_SOURCES` | `("metrics", "events", "flows")` | constant | — | telemetry sources drained per snapshot | `copilot/forensic/case.py:45` |
| `_TOPO_HOPS` | `(1, 2)` | constant | hops | blast-radius depths captured in topology snapshot | `copilot/forensic/case.py:46` |
| `MAX_LIMIT` | 100 | `ReplayAdapter(max_limit=...)` default | rows | per-page cap on `serve_rows` reads (both live drain and replay) | `copilot/adapter/contract.py:21` |
| `_HIGH` | `"9999-12-31T23:59:59Z"` | constant | — | open-ended upper bound for `ledger.by_time` scans | `copilot/forensic/trigger.py:30` |
| high-severity threshold | 0.8 | constant, inline | probability | `case_severity` "high" cutoff | `copilot/forensic/case.py:219` |
| medium-severity threshold | `dec.get("threshold", 0.5)` | record field, fallback 0.5 | probability | `case_severity` "medium" cutoff (the record's own alert threshold) | `copilot/forensic/case.py:219` |
| `INITIAL_CHAT` | `"initial"` | constant | — | chat id of the case-creation run | `copilot/forensic/chat.py:27` |
| `MASTER_CHAT` | `"master"` | constant | — | chat id of the concurrent-fault synthesis, also the idempotent-refire marker | `copilot/forensic/synthesis.py:29` |
| `WINDOW_S` (e2e) | 900 | constant | seconds | harness question window lookback | `copilot/e2e/harness.py:46` |
| `--dur-a` | 60 | CLI `--dur-a` | seconds | pipeline scenario-A fault duration | `copilot/e2e/pipeline.py:199` |
| `--dur-b` | 180 | CLI `--dur-b` | seconds | pipeline scenario-B fault duration (must outlive A so cause refines A→B) | `copilot/e2e/pipeline.py:202` |
| `--severity` | `"high"` | CLI `--severity` | — | fault severity passed to `run_scenario` for both A and B | `copilot/e2e/pipeline.py:203` |
| `--out` | `"pipeline-run"` | CLI `--out` | path | pipeline output dir (isolated ledger/cursor/cases) | `copilot/e2e/pipeline.py:204` |
| `expected_cases` | 2 | constant, inline | count | issue-48 invariant: exactly 2 cases from 2 overlapping faults | `copilot/e2e/pipeline.py:177` |

## Data flow

**Forensic trigger → case (live path):**
1. **Ledger** (`ledger.db`, written by the predictor / `predict_once`) → `Cursor`-gated scan
   `ledger.by_time(cursor.ts, "9999-12-31T23:59:59Z")` (`trigger.py:79`) → filter
   `type=="prediction"` and `decision.alert` true and not-yet-fired (`trigger.py:80-87`).
2. Per new alerting record: `WindowContext.forensic(_epoch(record["window_end_ts"]), cfg)`
   (`trigger.py:97`) → `handle(record, window)` = `create_case` (`case.py:279`).
3. **Live adapter** (`live_adapter`, e.g. `HttpAdapter` over dataapi) → `snapshot_window` drains
   `metrics`/`events`/`flows` device-scoped to `record.device` to exhaustion via `_drain`
   (`case.py:89`), plus `_snapshot_topology` walks `hops_within`/`walk_topology` at hops 1,2
   (`case.py:105`) → dumped as `{source}.json` + `topology.json` under `cases/<id>/window/`
   (`case.py:126-139`).
4. `prediction.json` = the raw record (`case.py:289`); `window.json` = `{start, end}` bound
   (`case.py:290-291`).
5. **`ReplayAdapter(window_dir)`** loads those JSON files back into memory (`case.py:154-157`) →
   `investigate_record` runs `copilot.agent.investigate` against it only (no live reads possible)
   → `Outcome` (answer + events + cites).
6. `render_case_md` → `case.md` (atomic write, the dashboard's completeness marker,
   `case.py:296-302`).
7. `INITIAL_CHAT` seeded from `outcome.events` into the per-case `SessionStore`
   (`chat.py:30`/`case.py:306-309`); on `ledger_to_kb`, the verdict md embeds into the KB retriever
   as an `incident` Doc (`case.py:275-276`).
8. If `record.n_concurrent > 1`: per co-active fault in `record.concurrent_faults`, a fresh
   `snapshot_window(..., device=<co-fault device>)` if the device differs from primary
   (`case.py:324-331`) → `synthesize_concurrent` runs one investigation chat per fault +
   `master_synthesis` merging their cites (`synthesis.py:116-144`).
9. On success, `Cursor.advance` persists `{ts, fired}` to `COPILOT_CURSOR_PATH` (`trigger.py:110`,
   `trigger.py:65-73`). On `handle` exception, cursor does NOT advance — same alert retried next
   poll (`trigger.py:103-108`).

**Forensic follow-up (API `/chat` with `case_id`):**
`resolve_case_dir` (untrusted id) → `follow_up` reloads `frozen_window(case_dir)` from
`window.json`, rebuilds a fresh `ReplayAdapter(case_dir/window)`, reloads `prediction.json` as
`record`, threads `store.history(chat_id)` from the chat's own `events.jsonl`, runs
`investigate_record`, appends the new turn back to the SAME chat (`chat.py:93-114`).

**E2E pipeline (`pipeline.py:run`):**
`faults/orchestrator.run_scenario` (real containerlab fault injection, threads A+B) → dataapi
`/labels` (`fetch_labels`) diffed against the pre-run baseline to isolate the 2 new labels
(`pipeline.py:112-124`) → `_tick_grid` walks UTC timestamps at `predict_interval_s` cadence over
the labels' combined span → per tick: `predict_once(cfg, ours, ledger, now, drift_tick=i)` (real
emulator seam, writes to an **isolated** `ledger.db` under `--out`) then
`poll_once(cfg, ledger, cursor, handle)` (real trigger tick, `handle` = `make_handler(...)` wrapped
to log forks) → checkpoints written at each stage; `summary.pass = (total_cases==2 and
churn_opened==0)` (`pipeline.py:177-179`) is the process exit condition (`sys.exit` on fail,
`pipeline.py:191`).

**E2E harness (`harness.py:run_live`):** `setup()` builds real `OpenAIClient` (nim profile),
`HttpAdapter` over live dataapi, `LanceRetriever` freshly seeded from `RAGCORPUS_DIR`, loaded
skills → each of the 7 `QUESTIONS` runs through `copilot.agent.investigate` with a rolling
`WindowContext(now - WINDOW_S, now)` → trace JSON + `REPORT.md` written.

## Calculations

- **Case severity bucket** (`case.py:210-219`):
  ```
  p = record.decision.calibrated_probability
  severity = "unknown"                      if p is None
           = "high"                         if p >= 0.8
           = "medium"                       if p >= record.decision.threshold (default 0.5)
           = "low"                          otherwise
  ```
  Inputs: `record["decision"]["calibrated_probability"]`, `record["decision"]["threshold"]`. No
  native severity field exists on the record (ground-truth label's severity is dropped at
  emulate) — this is a derived triage bucket, not a stored value.

- **Forensic window bound** (`copilot/window/__init__.py:44`, consumed by `trigger.py:97`):
  ```
  start = t_snapshot - cfg.window_x_min * 60
  end   = t_snapshot
  frozen = True
  ```
  `t_snapshot = _epoch(record["window_end_ts"])` (`trigger.py:36,97`) — ISO `...Z` parsed via
  `datetime.fromisoformat(ts).timestamp()`, cast to int.

- **Cursor "seen" test** (`trigger.py:60-63`): a record at `(ts, alert_id)` counts as already
  fired if `ts < cursor.ts OR (ts == cursor.ts AND alert_id in cursor.fired)`. `Cursor.advance`
  (`trigger.py:65-73`): if `ts > cursor.ts`, reset `fired = {alert_id}`; else add to the existing
  set. This makes the cursor a `(watermark ts, tie-set-at-watermark)` pair, not a full fired-id
  history — relies on `window_end_ts` being monotonic with ledger persist order.

- **Churn-opened invariant** (`pipeline.py:174-176`):
  ```
  churn_opened = sum(t.cases_opened_this_tick for t in ticks
                      if t.n_concurrent == 2
                      and t.cases_opened_this_tick > 0
                      and t is not <the first tick that opened any case>)
  ```
  i.e. any tick AFTER the first case-opening tick that both has `n_concurrent==2` and opens a
  case counts as churn. Expected 0 — while both faults A,B are active the primary cause is stable
  so re-emitted records share `alert_id` (`emulate.py:133-146` `_alert_id`), the ledger
  no-ops (`INSERT OR IGNORE`), and no new case forks.

- **`_alert_id`** (`copilot/emulator/emulate.py:133-146`, read by trigger/pipeline but computed in
  the emulator): `alt_{scenario_id}__{reported_cause}` — keyed on cause only, deliberately NOT
  `n_concurrent`, so a churning concurrency count (1→2→1) doesn't re-fire a case-creation.

- **Deduped case count** — `create_case` is idempotent per `case_id(record)` = the sanitised
  `alert_id` (`case.py:50-57`); one case dir per distinct id, backstopped (not primary-guarded —
  the trigger's cursor is primary) by checking `chats.read(INITIAL_CHAT)` before re-seeding
  (`case.py:307-309`) and `chats.read(MASTER_CHAT)` before re-running the fan-out
  (`synthesis.py:128-132`).

## Config & schemas

**`cases/<id>/prediction.json`** — the raw §3.3 Prediction Record verbatim (written by
`create_case`, `case.py:289`; read by `list_cases`/`read_case`/`follow_up`). Fields consumed here:
`device`, `window_end_ts`, `decision.{alert, calibrated_probability, threshold, abstain}`,
`risk.fault_types[0].{cause, family}`, `health.drift_state`, `explanation_ref.alert_id`,
`model_version`, `n_concurrent`, `concurrent_faults` (list of `{device, cause}`).

**`cases/<id>/window.json`** — `{"start": int, "end": int}`, the frozen window bound
(`case.py:290-291`); reloaded by `frozen_window` as a `WindowContext(start, end, frozen=True)`
(`chat.py:68-73`).

**`cases/<id>/window/{metrics,events,flows}.json`** — list of
`{"device": str|None, "ts": int, "line": str}` rows, written by `_drain` (`case.py:89-102`);
`line` is the unframed payload (evidence-delimiter-stripped, `case.py:76-85`); `ts` is always
epoch int (inherited from the live adapter's ISO→int normalisation, per `#40`, `case.py:24-26`).
Read back by `ReplayAdapter.__init__` (`case.py:154-155`), re-scoped by `device`/`pattern` then
re-served through `serve_rows` (same F2 pipeline the live path uses).

**`cases/<id>/window/topology.json`** — `{"hops": {"<device>:<n>": [device...]}, "walk":
{"<device>:<n>": [NodeState-as-dict...]}}` for `n` in `_TOPO_HOPS=(1,2)`, written by
`_snapshot_topology` (`case.py:105-123`); empty on any read error or no device. Read back by
`ReplayAdapter.hops_within`/`walk_topology` (`case.py:178-185`).

**`cases/<id>/case.md`** — the report (markdown, not JSON): structured verdict header (device,
predicted cause+family, alert/probability/threshold, abstain, model-health, window bounds,
model_version) + `## Report` (agent's cited prose) + `## Trace` (tool_call / gate event lines),
built by `render_case_md` (`case.py:230-259`); atomic write, gates `list_cases` completeness.

**`cases/<id>/window-<i>/`** — for `i` in `1..n_concurrent-1` where the co-fault's device differs
from the primary: a second frozen snapshot in the same shape as `window/`, keyed by the co-fault's
own device (`case.py:326-329`).

**`cases/<id>/chats/<chat_id>/events.jsonl`** — one `SessionStore` per case (`chat.py:30`); each
line is one appended `Event` (loop event shape: `type`, `data`, occurrence ts — owned by
`copilot.agent`/`copilot.memory`, not re-defined here). Chat ids: `INITIAL_CHAT="initial"`
(case-creation run), `fault-<i>` for `i>=1` (co-fault sub-investigations,
`synthesis.py:32-35`), `MASTER_CHAT="master"` (synthesis).

**`forensic-cursor.json`** (path from `COPILOT_CURSOR_PATH`, default `forensic-cursor.json`) —
`{"ts": str, "fired": [alert_id, ...]}`, written by `Cursor.advance` (`trigger.py:72-73`), loaded
in `Cursor.__init__` (`trigger.py:55-58`).

**E2E pipeline outputs** (under `--out`, default `pipeline-run/`):
- `run_manifest.json` — `{started_utc, git_sha, params, cfg, artifacts, ended_utc, scenario_ids,
  result}` (`pipeline.py:97-108`, updated at end `:182-184`).
- `checkpoint_1_labels.json` — the 2 new `/labels` rows sorted by `t_start`.
- `checkpoint_2_ticks.json` — per-tick `{tick, now, record_emitted, primary_cause, alert_id,
  n_concurrent, cases_opened_this_tick}`.
- `ticks_full.jsonl` — every tick's FULL emitted record (including ledger no-op re-emits), one
  JSON line each.
- `checkpoint_3_cases.json` — `{total_cases, expected_cases: 2, churn_opened_extra, cases: [...],
  pass: bool}`.
- `ledger.db`, `forensic-cursor.json`, `cases/<id>/` — isolated copies of the same shapes above, so
  the harness run never touches production's `ledger.db`.

**E2E harness outputs:**
- `copilot/e2e/traces/<slug>.json` — `{question, elapsed_s, crashed, stopped, answer, events:
  [{type, ...data}]}` per scripted question, one file per `QUESTIONS` entry
  (`harness.py:130-135`).
- `copilot/e2e/REPORT.md` — markdown summary table + per-question detail, regenerated each live
  run (`harness.py:167-191`); current committed copy shows 7/7 questions run, verdicts ranging
  cited-answer / gated / ask-back / stopped:step_cap (see file for the actual recorded run).

## Gotchas

- **`case.md` is the completeness marker, not `prediction.json`.** `list_cases` skips a case dir
  until `case.md` exists (`chat.py:46`); a case mid-investigation (prediction.json written,
  case.md not yet) is invisible to `/cases`, and `GET /cases/{id}` 404s via `FileNotFoundError`
  (`app.py:236-239`) rather than exposing a half-built case.

- **Trigger cursor does not advance on `handle` failure** (`trigger.py:103-108`): a bad/slow LLM
  run leaves the SAME alert retried every poll forever until it succeeds — no dead-letter, no
  backoff. A permanently-broken downstream (e.g. dead LLM endpoint) means the trigger loop spins
  retrying the same alert and never progresses past it.

- **`n_concurrent` is deliberately excluded from `_alert_id`** (`emulate.py:141-145`): case
  creation only re-fires on a cause change, not a concurrency-count change, because creating a
  case drains the live adapter and runs a full agent — keying on concurrency would hammer
  dataapi/LLM on every 1→2→3 tick. This means a case's `n_concurrent` value in `prediction.json`
  is whatever it was AT THE MOMENT the alert_id first appeared, not necessarily the peak observed.

- **`ReplayAdapter` re-scopes BEFORE `serve_rows`, unlike `StubAdapter`** (`case.py:150-152`,
  `168-176`): a follow-up that queries a neighbour device honestly returns `()` if that device's
  rows were never captured in the frozen snapshot — faithful to a real HTTP live run's
  server-side scoping, not the stub's more permissive behavior. A follow-up asking about anything
  outside the frozen device's captured window returns empty, not an error.

- **Function-local imports to break cycles**: `case.py:306` imports `chat.py` inside
  `create_case` (chat.py imports `ReplayAdapter` from case.py — top-level would cycle);
  `case.py:317` imports `synthesis.py` inside `create_case` for the same reason; `trigger.py:140-144`
  imports `api.app` inside `_main` (api.app → forensic.chat → trigger would cycle at module load).

- **Topology snapshot silently drops on ANY read fault, not per-hop** (`case.py:119-122`): a dead
  backend during hop-1 capture means hop-2 is skipped too ("deeper hops share the same transport
  so they'd fail too") — no partial-capture, no retry. A case created during a topology-endpoint
  blip has empty `hops`/`walk` for the ENTIRE case, forever (frozen).

- **`master_synthesis` gathers no cites of its own** — it only inherits and attributes sub-chats'
  cites (`synthesis.py:12-16,65-69,100`). If a sub-chat's investigation produced zero cites (e.g.
  gate failed), the master has nothing of that fault's evidence to synthesise with, even though
  the sub-chat's prose answer may still be non-empty.

- **`window.t_snapshot` freeze guard fires at `Filters.validate`, not at the API boundary**
  (`contract.py:66-69`): `follow_up`'s own pre-check (`chat.py:104-105`) only raises when
  `requested_end` is explicitly passed AND exceeds `T_snapshot`; any tool call the agent itself
  makes with a wider `end` is caught later, deep inside the adapter, by the same guard — so a
  gate/investigate failure from a frozen-window violation surfaces as a `FilterError` from
  wherever in the loop the tool call happened, not from `follow_up`'s entry.

- **E2E pipeline uses its OWN isolated `Ledger`/`Cursor`/cases root under `--out`**
  (`pipeline.py:130-136`), never the production `ledger.db` — so a pipeline run does not advance
  or interact with a live trigger daemon's cursor, and running the pipeline concurrently with a
  live daemon on the same dataapi is safe for ledger state (but both still fire real faults
  against the same lab).

- **`create_case` is a backstop-idempotent, not primary-idempotent, function**
  (`case.py:283-284,307-309`): the trigger's `Cursor` is the primary dedup; `create_case`'s own
  `chats.read(INITIAL_CHAT)` check is a secondary guard against a re-fire from a caller that
  bypasses the trigger (e.g. a test or the E2E pipeline calling `make_handler` directly) — a
  caller that re-invokes `create_case` with a fresh `case_dir` computed differently (e.g. a
  mutated `alert_id`) gets a brand-new case, not a dedup.
