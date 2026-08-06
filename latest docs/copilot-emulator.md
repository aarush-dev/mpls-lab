# Copilot — PA Emulator

## Purpose

Turns a ground-truth fault label (dataapi `/labels` row) into a full-fidelity §3.3 Prediction
Record without a real PA model existing yet (`copilot/emulator/emulate.py:1-29`). It is the ONLY
seam between copilot and the prediction stack (ADR-0003): callers (agent gate, agent loop, forensic
trigger) consume Prediction Records identically whether `cfg.emulate_pa` is `true` (emulator reads
ground truth) or `false` (real PA HTTP client, `real_pa.py`). Sits between dataapi (ground-truth
labels + live telemetry windows) and the rest of copilot (gate, loop, forensic case, event ledger).
The periodic driver (`predictor.py`, ADR-0014) fires this on a cadence and writes records to the
Event Ledger; the forensic trigger (owned elsewhere) reacts to `decision.alert == true`.

## Entry points

No CLI/FastAPI routes in this subsystem — it is a library + a background daemon `__main__`.

- **Predictor daemon** (`copilot/emulator/predictor.py:70-94`, `_main`): headless loop, wired by
  `copilot-up.sh` / `noc-copilot.service`.
  ```
  python3 -m copilot.emulator.predictor
  ```
  Reads `COPILOT_DATAAPI_URL` (falls back to `cfg.dataapi_url`) and `COPILOT_LEDGER_PATH` (falls
  back to `ledger.db`) from env; stops on SIGTERM.

- **Self-checks** (assert-based, no framework — see `predictor.py:13`, `emulate.py:28`,
  `real_pa.py:16`):
  ```
  python3 -m copilot.emulator.test_emulate
  python3 -m copilot.emulator.test_predictor
  python3 -m copilot.emulator.test_real_pa
  ```

- **Programmatic seam** (what other components call — not a CLI, but the real entry point):
  ```python
  from copilot.config import load
  from copilot.emulator import prediction
  cfg = load()
  rec = prediction(cfg, labels, now="2026-06-21T14:56:02Z")
  ```

## Modules

- **`emulate.py`** — core: ground-truth label -> §3.3 Prediction Record; the `emulate_pa` seam
  router.
  - `emulate_record(label, *, error_profile="light", n_concurrent=1, now=None, drift_tick=0, concurrent_faults=None) -> dict` — the full record builder. `emulate.py:149`
  - `prediction(cfg, labels, *, now=None, real_pa=None, drift_tick=0) -> dict | None` — the seam: routes `cfg.emulate_pa` to emulator or real PA. `emulate.py:284`
  - `fetch_labels(base_url, *, fetch=None) -> list[dict]` — pulls `/labels` from dataapi. `emulate.py:307`
  - `family(fault_type) -> str` — coarse §2.1 family lookup. `emulate.py:57`
  - `fault_type(record) -> str | None` — top reported cause accessor. `emulate.py:248`
  - `is_abstain(record) -> bool` — abstain accessor (softens quality gate). `emulate.py:257`
  - `drift_state(record) -> str | None` — model-health rung accessor (trust gate). `emulate.py:262`
  - `to_wire(record) -> dict` — wraps a record as an Event-Ledger row. `emulate.py:269`
  - `persist(ledger, record) -> None` — idempotent-by-alert_id append to the ledger. `emulate.py:276`
  - internal: `_hash01` (deterministic [0,1) jitter seed, `emulate.py:62`), `_abstain` (`emulate.py:69`), `_jitter` (`emulate.py:80`), `_drift` (`emulate.py:91`), `_confuse` (`emulate.py:102`), `_incidence_curve` (`emulate.py:118`), `_fault_ref` (`emulate.py:125`), `_alert_id` (`emulate.py:133`), `_active_at` (`emulate.py:323`), `_no_real_pa` (`emulate.py:330`).

- **`predictor.py`** — the periodic firing loop (R4b, ADR-0014) that drives `emulate.py` on a
  cadence and persists to the ledger.
  - `predict_once(cfg, labels, ledger, now, *, drift_tick=0, real_pa=None) -> dict | None` — one tick. `predictor.py:23`
  - `run_predictor(cfg, base_url, ledger, *, now_fn, stop_fn, sleep=time.sleep, fetch=None, real_pa=None) -> int` — sleep-loop driver, returns tick count. `predictor.py:39`
  - `_main()` — process entrypoint (daemon), builds `RealPA` lazily when `emulate_pa` is false. `predictor.py:70`

- **`real_pa.py`** — HTTP client for the real PA service (`emulate_pa=false` path), and the
  translation shim from the real model's response into the §3.3 record shape.
  - `class RealPA` — `.__init__(cfg, *, post=None, get=None)` (`real_pa.py:33`), `.channels()` (fetches model channel order from `/v1/health`, falls back to vendored `CHANNELS`, `real_pa.py:56`), `.predict(now)` (matches the `real_pa(now)` seam signature; scores the live window, returns primary alerting record or `None`, `real_pa.py:66`), `._snapshot` (one `/v1/predict/snapshot` call, `real_pa.py:82`), `._temporal` (per-entity `/v1/predict` calls, `real_pa.py:91`), `._to_record` (fills fields the real response omits: `device`, `explanation_ref.alert_id`, `n_concurrent`/`concurrent_faults`, `decision.abstain`, `health.drift_state`, `anomaly.vq_label`; `real_pa.py:101`).
  - `_cause(rec) -> str` — top cause reader off a raw real-PA record. `real_pa.py:25`
  - `_no_real_pa(now=None)` — placeholder that raises if `RealPA` isn't wired. `real_pa.py:122`
  - Depends on `copilot.window.build.build_windows` and `copilot.window.vocab` (outside this
    subsystem — not documented here) to build live scoring windows.

- **`__init__.py`** — re-exports the public surface: `drift_state, emulate_record, family,
  fault_type, fetch_labels, is_abstain, persist, predict_once, prediction, run_predictor, to_wire`.
  `__init__.py:10-19`

- Test files (`test_emulate.py`, `test_predictor.py`, `test_real_pa.py`) — run via the
  `python3 -m copilot.emulator.test_*` commands above; not documented line-by-line per task rules.

## Parameters

| name | default | env-var/CLI-flag | units | what it controls | source (file:line) |
|---|---|---|---|---|---|
| `emulate_pa` | `True` | `config.yaml: emulate_pa` | bool | routes `prediction()` to emulator (true) vs real PA client (false) | `copilot/config.py:58`, used `copilot/emulator/emulate.py:297` |
| `error_profile` | `"light"` | `config.yaml: error_profile` | enum `oracle\|light\|heavy` | how much the emulator perturbs cause/TTI/abstain/drift vs ground truth | `copilot/config.py:79`, used `copilot/emulator/emulate.py:149` |
| `predict_interval_s` | `3` | `config.yaml: predict_interval_s` | seconds | sleep between predictor ticks | `copilot/config.py:82`, used `copilot/emulator/predictor.py:66` |
| `drift_distrust_at` | `"R3"` | `config.yaml: drift_distrust_at` | enum rung `R0..R5` | trust-gate flag threshold on `health.drift_state` (consumed outside this subsystem, `copilot/agent/gate.py:159`) | `copilot/config.py:80` |
| `dataapi_url` | `"http://127.0.0.1:8000"` | `COPILOT_DATAAPI_URL` (env override, `app.py`) / `config.yaml: dataapi_url` | URL | base URL `fetch_labels`/`run_predictor` hit for `/labels` | `copilot/config.py:85`, used `copilot/emulator/predictor.py:84`, `emulate.py:307` |
| `pa_url` | `"http://127.0.0.1:8001"` | `config.yaml: pa_url` | URL | base URL of the real PA inference service | `copilot/config.py:88`, used `copilot/emulator/real_pa.py:34` |
| `pa_target_fpr` | `0.01` | `config.yaml: pa_target_fpr` | conformal FPR, one of `{0.005,0.01,0.02,0.05}` | alert budget sent to the real PA's `/v1/predict[/snapshot]` | `copilot/config.py:89`, used `copilot/emulator/real_pa.py:35,88,96` |
| `pa_top_k` | `3` | `config.yaml: pa_top_k` | count | fault causes requested per entity from the real PA | `copilot/config.py:90`, used `copilot/emulator/real_pa.py:36,88,96` |
| `pa_mode` | `"snapshot"` | `config.yaml: pa_mode` | enum `snapshot\|temporal` | real PA serving mode: one graph-fused call vs per-entity calls | `copilot/config.py:91`, used `copilot/emulator/real_pa.py:37,74` |
| `pa_topology_id` | `"live_lab"` | `config.yaml: pa_topology_id` | string | topology id sent with `/v1/predict/snapshot` | `copilot/config.py:92`, used `copilot/emulator/real_pa.py:38,87` |
| `_CONFIDENCE` map | `low:0.55, medium:0.70, high:0.85, critical:0.95` | (code constant) | probability | ground-truth `severity` -> the record's one confidence scalar; everything else reads off it | `copilot/emulator/emulate.py:51`, applied `emulate.py:163` |
| `_HORIZONS_S` | `(120, 300, 600, 1800)` | (code constant) | seconds | the §3.3 cumulative-incidence / survival curve horizons | `copilot/emulator/emulate.py:52` |
| `_MODEL_VERSION` | `"emulator-v1"` | (code constant) | string | `record.model_version` identity tag | `copilot/emulator/emulate.py:53` |
| `_THRESHOLD` | `0.62` | (code constant) | probability | decision threshold τ*(u_t); `decision.alert` fires at/above this | `copilot/emulator/emulate.py:54`, applied `emulate.py:232-233` |
| light TTI jitter span | `0.2` (±20%) | (code constant) | fraction | max fractional TTI perturbation under `error_profile="light"` | `copilot/emulator/emulate.py:84` |
| heavy TTI jitter span | `0.5` (±50%) | (code constant) | fraction | max fractional TTI perturbation under `error_profile="heavy"` | `copilot/emulator/emulate.py:84` |
| heavy abstain confidence floor | `0.75` | (code constant) | probability | `heavy` abstains whenever `conf < 0.75` | `copilot/emulator/emulate.py:76` |
| light abstain rule | `severity == "low"` | (code constant) | — | `light` abstains only on low-severity ground truth | `copilot/emulator/emulate.py:77` |
| `_LADDER` | `("R0","R1","R2","R3","R4","R5")` | (code constant) | enum ladder | ADR-0003 model-health degradation rungs | `copilot/emulator/emulate.py:88` |
| drift start rung | light=`R1` (idx 1), heavy=`R3` (idx 3) | (code constant) | ladder index | starting rung by error profile before ticks climb it | `copilot/emulator/emulate.py:98` |
| drift climb rate | 1 rung / 3 ticks | (code constant) | ticks | how fast `drift_tick` climbs the ladder, capped at `R5` | `copilot/emulator/emulate.py:98` |
| novelty-per-rung | `0.05 + 0.03 * rung_idx` | (code constant) | scalar | `codebook_novelty` as a function of drift ladder index | `copilot/emulator/emulate.py:99` |
| light confuse rate | `0.15` | (code constant) | probability | chance `light` reports a confusable sibling cause instead of true type | `copilot/emulator/emulate.py:113` |
| heavy confuse rate | `0.4` | (code constant) | probability | chance `heavy` reports a confusable sibling cause | `copilot/emulator/emulate.py:113` |
| oracle drift | fixed `("R0", 0.01)` | (code constant) | — | oracle profile: always healthy, near-zero novelty | `copilot/emulator/emulate.py:97` |
| `attention_attribution` weight | `0.6` | (code constant) | scalar | fixed localization edge weight, one-hop device->interface | `copilot/emulator/emulate.py:215` |
| `temperature` | `1.2` | (code constant) | scalar | fixed calibration temperature reported in `decision` | `copilot/emulator/emulate.py:231` |
| `conformal_set_valid_at_fpr` | `0.01` | (code constant) | FPR | fixed value reported in `decision` (emulator does not vary this) | `copilot/emulator/emulate.py:234` |
| `gate_ensemble_variance` | `0.03` | (code constant) | scalar | fixed epistemic-uncertainty sub-term | `copilot/emulator/emulate.py:236` |
| `expert_weights` | `hazard_head:0.5, forecast:0.2, graph:0.18, vae:0.12` | (code constant) | weights (sum≈1) | fixed fusion weights reported in `decision` | `copilot/emulator/emulate.py:238` |
| `forecast.horizon_steps` | `480` | (code constant) | steps | fixed forecast horizon length reported per record | `copilot/emulator/emulate.py:204` |
| `vq_code` modulus | `512` | (code constant) | codebook size | `vq_code = sha1(cause) % 512` | `copilot/emulator/emulate.py:173` |

## Data flow

**Emulator path (`cfg.emulate_pa=True`):**
1. Input: ground-truth fault rows from dataapi's `/labels` endpoint (jsonl-backed
   `faults/labels/labels.jsonl`), shape `{type, target, severity, t_start, t_impact, t_end,
   lead_time, probe, baseline_value, impact_value, signature, device, scenario_id}`
   (`emulate.py:154-156`).
2. `fetch_labels(base_url)` GETs `{base_url}/labels`, `.json()["rows"]` (`emulate.py:316-320`), or a
   test can inject a canned `fetch` callable.
3. `run_predictor` re-fetches this list EVERY tick (not once at boot) so a fault injected mid-run is
   seen (`predictor.py:45-46`, `predictor.py:61`).
4. `prediction(cfg, labels, now=...)` filters to labels active at `now` (`_active_at`: lexical UTC
   compare of `t_start <= now <= t_end`, `emulate.py:323-327`); `len(active)` becomes
   `n_concurrent`; the first active label is the primary (`emulate.py:299-304`).
5. `emulate_record(label, ...)` transforms the one label into a full §3.3 record (see Calculations).
6. Output: `predict_once` calls `persist(ledger, rec)` -> `to_wire` wraps it
   `{"type":"prediction","ts":record["window_end_ts"],"record":record}` -> `ledger.append(alert_id,
   wire, device=...)` (`emulate.py:269-281`, `predictor.py:31-32`). Idempotent by `alert_id`: a
   same-cause re-emit is a ledger no-op.
7. Consumers read the record back out via `fault_type()` (skill selection,
   `copilot/agent/loop.py:318`), `is_abstain()` (gate softening), `drift_state()` (trust banner,
   `copilot/agent/gate.py:159-175`, `copilot/agent/loop.py:422-424`) — all outside this subsystem.

**Real-PA path (`cfg.emulate_pa=False`):**
1. `run_predictor` builds `RealPA(cfg).predict` once, does NOT fetch `/labels` (real PA self-gates
   on `decision.alert`, `predictor.py:49-53,61`).
2. `RealPA.predict(now)` calls `build_windows(now, channels, ...)` (`copilot.window.build`, outside
   this subsystem) to get live per-entity telemetry windows from dataapi.
3. POSTs to `{pa_url}/v1/predict/snapshot` (mode=snapshot, one call, all entities) or
   `{pa_url}/v1/predict` per entity (mode=temporal) (`real_pa.py:82-98`).
4. Filters to `decision.alert==true` records, sorts by `calibrated_probability` descending, takes
   the winner as primary, the rest as `concurrent_faults` (`real_pa.py:74-79,101-119`).
5. `_to_record` back-fills fields the real model's response omits (`device`,
   `explanation_ref.alert_id`, `n_concurrent`, `decision.abstain=False`, `health.drift_state="R0"`,
   `anomaly.vq_label`) so the shape matches the emulator's output (`real_pa.py:101-119`).
6. Same `predict_once` -> `persist` -> ledger path as above.

## Calculations

All derived values read off ONE confidence scalar per record — "the record's numbers are a readout
of ground truth; they cannot disagree with each other" (`emulate.py:20`).

- **`conf` (confidence)** = `_CONFIDENCE[label.severity]`, default `0.60` if severity unmapped.
  `emulate.py:51,163`. Drives risk, forecast spread, decision, and uncertainty below.

- **`ftype` (reported cause)** = `_confuse(label, profile, true_type)`:
  - `oracle` -> `true_type` always.
  - `light`/`heavy`: let `sibs` = other `_FAMILY` keys mapping to the same family as `true_type`.
    If `_hash01(scenario_id, "confuse") < rate` (rate = `0.15` light / `0.4` heavy), pick
    `sibs[int(_hash01(scenario_id,"pick") * len(sibs)) % len(sibs)]`; else `true_type`.
  `emulate.py:102-115`.

- **`tti` (time to impact, median seconds)** = `max(1.0, lead * (1 + jitter))`, where `lead =
  float(label.lead_time or 300.0)` and
  `jitter = (_hash01(scenario_id, "tti") - 0.5) * 2 * span`, `span = 0.5` (heavy) / `0.2` (light) /
  `0` (oracle). `emulate.py:80-85,164-165`.
  - `time_to_impact.iqr_s` = `[round(tti*0.6,1), round(tti*1.4,1)]` (fixed ±40% band around the
    median). `emulate.py:193`.

- **`abstain`** — `oracle`: always `False`. `heavy`: `conf < 0.75`. `light`:
  `label.severity == "low"`. `emulate.py:69-77`.

- **`drift_state, novelty` (model health)**:
  - `oracle` -> `("R0", 0.01)`.
  - else: `idx = min(5, start + max(0, drift_tick)//3)`, `start = 1` (light) / `3` (heavy);
    `drift_state = _LADDER[idx]`; `novelty = round(0.05 + 0.03*idx, 3)`.
  `emulate.py:91-99`.

- **`incidence` (cumulative-incidence curve F_k(h))** — for each `h` in `_HORIZONS_S`:
  `p = round(conf * min(1.0, h / 1800), 3)` (monotone non-decreasing by construction — scales
  linearly with horizon fraction of the max 1800s horizon, capped at `conf`). `emulate.py:118-122`.
  - `survival_curve.p` = `round(1 - incidence.p, 3)` per horizon (`emulate.py:198-199`).
  - `hazard_curve`: bin@120s `lambda = incidence[0].p`; bin@300s `lambda = round(incidence[1].p -
    incidence[0].p, 3)` (only the first two horizons get a hazard bin). `emulate.py:194-196`.
  - `p_no_impact_in_horizon` = `round(1 - conf, 3)` (`emulate.py:200`).

- **`spread` (forecast quantile spread)** = `round(abs(impact - baseline), 3)`, where `baseline =
  float(label.baseline_value or 0.0)`, `impact = float(label.impact_value or baseline)`.
  `emulate.py:170-172,211`.
  - Forecast quantiles: `0.05 = [baseline*0.95, impact*0.95]`, `0.5 = [baseline, impact]`, `0.95 =
    [baseline*1.05, impact*1.15]` — fixed ±5%/+15% bands around the two-point baseline->impact
    trajectory. `emulate.py:207-209`.

- **`vq` (vector-quantization code)** = `int(sha1(ftype).hexdigest(), 16) % 512`. `emulate.py:173`.

- **`decision.fused_probability`** = `round(conf, 3)`. **`calibrated_probability`** =
  `round(conf * 0.95, 3)` (fixed 5% calibration discount). `emulate.py:229-230`.
  - **`decision.alert`** = `(conf * 0.95) >= 0.62 and not abstain` (`_THRESHOLD`).
    `emulate.py:232-233`.
  - **`uncertainty.aleatoric`** = `round(1 - conf, 3)`; **`uncertainty.epistemic`** =
    `round(novelty * 2, 3)`. `emulate.py:235-236`.

- **`localization.counterfactual.risk_delta`** = `-round(conf * 0.3, 3)` (fixed 30% of confidence,
  negated). `emulate.py:218`.

- **`anomaly.novelty_pvalue`** = `round(max(0.001, 1 - conf) / 10, 4)`. `emulate.py:222`.

- **`alert_id`** = `f"alt_{scenario_id}__{reported_cause}"` — keyed on `scenario_id` + the REPORTED
  (post-confuse) cause only, deliberately excluding `n_concurrent`: concurrency churn (1->2->3)
  must not re-open a forensic case every tick; a cause refinement mints a fresh id and opens a new
  case. `emulate.py:133-146`.

- **`n_concurrent`** (real-PA path) = `len(alerting)` where `alerting` = records with
  `decision.alert==True` from the batch response. `real_pa.py:75,107`.

## Config & schemas

- **Input: `/labels` row** (dataapi, backed by `faults/labels/labels.jsonl`, read via
  `fetch_labels`, `emulate.py:307-320`). Fields consumed: `scenario_id`, `type`, `target.device`,
  `target.vrf`, `target.entity_type`, `severity` (`low|medium|high|critical`), `t_start`,
  `t_impact`, `t_end` (ISO-8601 UTC), `lead_time` (float, seconds), `probe` (channel name),
  `baseline_value`, `impact_value` (floats), `signature` (free text), `device`. Consumed at
  `emulate.py:160-172`.

- **Output: §3.3 Prediction Record** (`emulate_record` return, `emulate.py:175-245`). Top-level:
  `entity_id`, `entity_type`, `vrf`, `device`, `window_end_ts`, `model_version`, `n_concurrent`
  (int ≥1), `concurrent_faults` (list of `{device, cause}`), plus five blocks:
  - `risk`: `fault_types[]` (each: `cause`, `family`, `cumulative_incidence[]` (`horizon_s`,`p`),
    `time_to_impact` (`median_s`, `iqr_s`), `hazard_curve[]` (`bin_s`,`lambda`)),
    `survival_curve[]` (`horizon_s`,`p`), `p_no_impact_in_horizon`.
  - `forecast`: `horizon_steps`, `channels[]` (`name`, `quantiles` {`0.05`,`0.5`,`0.95`}),
    `quantile_spread_q90_q10`.
  - `localization`: `attention_attribution[]` (`relation`,`hop`,`weight`), `minimal_subgraph`
    (`nodes`,`edges`), `counterfactual` (`edge_removed`,`risk_delta`).
  - `anomaly`: `novelty_pvalue`, `vq_code`, `vq_label`, `codebook_novelty`.
  - `decision`: `fused_probability`, `calibrated_probability`, `temperature`, `threshold`, `alert`
    (bool), `conformal_set_valid_at_fpr`, `uncertainty` (`aleatoric`,`epistemic`,
    `gate_ensemble_variance`), `abstain` (bool), `expert_weights`.
  - `health`: `drift_state` (R0-R5), `codebook_novelty` — folded into the record per ADR-0003
    (no separate `/health` endpoint) (`emulate.py:11-13,241`).
  - `explanation_ref`: `alert_id`, `incident_memory_written` (bool, always `False` here),
    `explanation_status` (`"pending"`).

- **Event-Ledger wire row** (`to_wire`, `emulate.py:269-273`): `{"type": "prediction", "ts":
  record["window_end_ts"], "record": <full record above>}`. Written via `ledger.append(alert_id,
  wire_row, device=record["device"])` (`emulate.py:281`; `Ledger` class lives in `copilot/memory`,
  outside this subsystem).

- **`config.yaml` keys read by this subsystem** (`copilot/config.py:131-153` parses; see Parameters
  table for values/lines): `emulate_pa`, `error_profile`, `predict_interval_s`,
  `drift_distrust_at`, `dataapi_url`, `pa_url`, `pa_target_fpr`, `pa_top_k`, `pa_mode`,
  `pa_topology_id`. Validated in `Config.__post_init__` (`copilot/config.py:101-124`): e.g.
  `error_profile` must be in `{oracle,light,heavy}` (`config.py:106-107`), `pa_target_fpr` must be
  one of `{0.005,0.01,0.02,0.05}` (`config.py:120-121`), `pa_mode` in `{snapshot,temporal}`
  (`config.py:122-123`).

## Gotchas

- `alert_id` is keyed on `scenario_id + reported cause` ONLY, never `n_concurrent` — a tick that
  only grows the concurrency count re-emits the same id and is a ledger no-op; only a cause
  refinement opens a new forensic case. Deliberate: case creation drains the live adapter and runs
  an agent, so keying on churning concurrency would hammer it every tick. `emulate.py:133-146`.

- `error_profile` perturbations are ALL keyed by `hashlib.sha1` of `scenario_id` (no RNG, no wall
  clock) so a run reproduces byte-for-byte across restarts — do not swap in `random`/`hash()` when
  extending this. `emulate.py:62-66`.

- `drift_tick` is caller-supplied (from `predictor.py`'s tick counter), not derived from wall time —
  passing the wrong tick (e.g. resetting to 0 mid-run) silently resets model-health back down the
  ladder. `emulate.py:91-99`, `predictor.py:62`.

- `oracle` profile ignores `drift_tick` entirely — always `("R0", 0.01)` regardless of tick, by
  design (oracle = perfect readout). `emulate.py:96-97`.

- `run_predictor` re-fetches `/labels` EVERY tick, not once at boot (#55 fix) — a fault injected
  after daemon start must be visible; if you're debugging "why does the daemon do N GETs" this is
  why, not a bug. `predictor.py:45-46,61`.

- The `try/except Exception` around each tick body in `run_predictor` is deliberate broad-catch: a
  transient dataapi outage skips one tick instead of killing the daemon. Don't narrow it without
  checking #55's intent. `predictor.py:56-64`.

- `emulate_pa=False` builds `RealPA` lazily inside `run_predictor` only if `real_pa` wasn't already
  injected — a test or caller that wants to intercept the real-PA client MUST pass `real_pa=...`
  explicitly, or it gets a live `RealPA(cfg)` hitting `cfg.pa_url` over HTTP. `predictor.py:51-53`.

- With `emulate_pa=False`, `run_predictor` never calls `fetch_labels` (`labels = []`,
  `predictor.py:61`) — the real PA self-gates on `decision.alert`, so `/labels` ground truth is
  unused on that path; don't expect emulator-side label filtering logic to apply.

- `_to_record` (real-PA path) OVERWRITES `decision.abstain` to `False` unconditionally — the real
  model is documented as never abstaining; if that assumption changes upstream, this line silently
  discards a real abstain signal. `real_pa.py:111`.

- `_to_record`'s `health.drift_state` is hardcoded to `"R0"` because real drift lives on a separate,
  unbuilt endpoint — the trust gate never distrusts records from a live real PA yet, even if the
  underlying model has actually drifted. `real_pa.py:114-115`.

- `RealPA.channels()` swallows ANY exception from `/v1/health` and silently falls back to the
  vendored `CHANNELS` list (`copilot.window.vocab`) — a real channel-order mismatch between the
  vendored constant and a live model would fail silently here, not loudly. `real_pa.py:56-64`.

- `_active_at` uses a lexical string compare (`start <= now <= end`) instead of parsing timestamps —
  only correct because labels are always written zero-padded UTC with the same offset; any label
  source that varies formatting breaks this silently. `emulate.py:323-327`.

- `n_concurrent` is clamped to `max(1, int(n_concurrent))` in `emulate_record` even if a caller
  passes `0` or a negative number — never trust it to reflect an actual zero-fault window; `None`
  active labels return `None` from `prediction()` instead, upstream of this clamp. `emulate.py:182`,
  `emulate.py:299-301`.
