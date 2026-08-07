# PA Alerts — prediction to Grafana bridge

## Purpose

`pa_alerts` polls the live PA (predictive-alerting) model on a fixed interval, scores the current lab topology, and turns the risk scores into a small alert list + full prediction table that a Grafana panel reads over HTTP. It sits parallel to the copilot chat path — no copilot involved. Judge injects a fault from a Grafana panel; this service is what lights the panel up. Two independent detection algorithms are implemented (`rank` and `threshold`), selected by env var, no code change needed to switch.

## Entry points

Service (FastAPI app, background scoring thread started on startup):
```
PA_URL=http://127.0.0.1:8001 PA_ALERT_MODE=rank \
  /root/pa-venv/bin/uvicorn pa_alerts.service:app --host 127.0.0.1 --port 8002
```
(`pa_alerts/service.py:19-21`)

Routes:
- `GET /health` — `pa_alerts/service.py:174-178`
- `GET /alerts` — `pa_alerts/service.py:181-186`

Calibration CLI (run once, while lab is fault-free, to (re)generate `calibration.json` for `threshold` mode):
```
PA_URL=http://127.0.0.1:8001 /root/pa-venv/bin/python -m pa_alerts.calibrate --minutes 30
```
(`pa_alerts/calibrate.py:17`, `__main__` block at `pa_alerts/calibrate.py:95-96`)

## Modules

- `pa_alerts/__init__.py` — empty package marker.
- `pa_alerts/service.py` — live scoring loop + FastAPI app.
  - `_score_once()` `pa_alerts/service.py:72-86` — build live windows, POST one snapshot-predict call.
  - `_row(rec)` `pa_alerts/service.py:89-96` — flatten one PA record into a display row.
  - `_alerts_rank(rows)` `pa_alerts/service.py:99-134` — mode A: EWMA-baseline rise detector.
  - `_alerts_threshold(rows)` `pa_alerts/service.py:137-150` — mode B: calibrated absolute-threshold gate.
  - `_loop()` `pa_alerts/service.py:153-166` — background thread body, one tick every `INTERVAL`.
  - `_load_calib()` `pa_alerts/service.py:61-64` — read `calibration.json` into `_CALIB` (called once, at loop start).
  - `health()` / `alerts()` — route handlers, `pa_alerts/service.py:174-186`.
- `pa_alerts/calibrate.py` — offline healthy-baseline collector.
  - `_score()` `pa_alerts/calibrate.py:46-56` — same snapshot-predict call, no persistent state.
  - `main()` `pa_alerts/calibrate.py:59-96` — CLI arg parse, sample-collection loop, quantile computation, write `calibration.json`.
- `pa_alerts/calibration.json` — data file, not code; see Config & schemas.

External deps read but not owned here: `copilot.window.vocab.CHANNELS`, `copilot.window.build.build_windows` (`pa_alerts/service.py:36-37`, `pa_alerts/calibrate.py:32-33`) and the PA prediction service's `/v1/predict/snapshot` endpoint (`PA_URL`).

## Parameters

| name | default | env-var/CLI-flag | units | what it controls | source (file:line) |
|---|---|---|---|---|---|
| `PA_URL` | `http://127.0.0.1:8001` | env `PA_URL` | URL | base URL of the PA prediction service | `service.py:39`, `calibrate.py:35` |
| `TOPOLOGY_ID` | `live_lab` | env `PA_TOPOLOGY_ID` | string | `topology_id` sent in every predict request | `service.py:40`, `calibrate.py:36` |
| `MODE` | `rank` | env `PA_ALERT_MODE` | enum `rank`\|`threshold` | which alert algorithm the loop uses | `service.py:41` |
| `INTERVAL` | `15` | env `PA_ALERT_INTERVAL_S` | seconds | sleep between scoring ticks | `service.py:42` |
| `TARGET_FPR` | `0.01` | env `PA_TARGET_FPR` | probability | `target_fpr` field sent in service.py's snapshot request (unused by `calibrate.py`'s request) | `service.py:43`, `service.py:84` |
| `EWMA_ALPHA` | `0.2` | env `PA_EWMA_ALPHA` | weight, 0-1 | smoothing factor for the per-entity `p_any` baseline (rank mode) | `service.py:44` |
| `RISE_MARGIN` | `0.15` | env `PA_RISE_MARGIN` | probability delta | min `p_any - baseline` rise required to flag "over" (rank mode) | `service.py:45` |
| `MIN_RISK` | `0.6` | env `PA_MIN_RISK` | probability | floor on absolute `p_any`; a rise below this never fires even if margin met (rank mode) | `service.py:46` |
| `TOP_K` | `6` | env `PA_TOP_K_ALERTS` | count | max entries returned in the rank-mode alert list | `service.py:47` |
| `CONSEC` | `2` | env `PA_CONSEC_TICKS` | ticks | consecutive over-margin ticks required to confirm a rank-mode alert | `service.py:48` |
| `CALIB_PATH` | `pa_alerts/calibration.json` | (derived, not overridable) | path | location `_load_calib()` reads | `service.py:49` |
| threshold_global fallback | `0.9` | code constant | probability | used when `calibration.json` missing or lacks `threshold_global` | `service.py:139` |
| predictions cap | `20` | code constant | count | max rows kept in `_STATE["predictions"]` per tick | `service.py:161` |
| `--minutes` | `30` | CLI `--minutes` | minutes | duration of the calibration collection run | `calibrate.py:61` |
| `--interval` | `20` | CLI `--interval` | seconds | sleep between calibration ticks | `calibrate.py:62` |
| `--target-fpr` | `0.01` | CLI `--target-fpr` | probability | target false-positive rate; sets quantile `q = 1 - target_fpr` | `calibrate.py:63`, `83` |
| per-entity min samples | `10` | code constant | count | min samples an entity needs before it gets a `threshold_per_entity` entry | `calibrate.py:88` |

## Data flow

**Live scoring (service.py, every `INTERVAL` s):**
1. `_now_iso()` floors current unix time to the nearest 30s and formats as UTC ISO-8601 (`service.py:67-69`).
2. `build_windows(now, CHANNELS)` (external, `copilot.window.build`) pulls live telemetry (dataapi/VM) and returns `{entity_id: window_dict}` with `device`, `entity_type`, `vrf`, `window`, `etc` (entity_type_code), `stc` (site_type_code) (`service.py:75`).
3. Windows are reshaped into an `entities` list and POSTed to `{PA_URL}/v1/predict/snapshot` with `topology_id`, `window_end_ts=now`, `entities`, `target_fpr=TARGET_FPR` (`service.py:78-86`).
4. Response `records` list, each flattened by `_row()` into `{entity_id, entity_type, device, cause, p_any, calibrated_probability, time_to_impact_s}` (`service.py:89-96`).
5. Rows sorted by `-p_any`, alert list built by `_alerts_rank` or `_alerts_threshold` per `MODE` (`service.py:159-160`).
6. Result written into module-level `_STATE` dict (`ts`, `predictions[:20]`, `alerts`, `n_scored`, `error`, `warm`) — single writer thread, dict swap is the only cross-thread sync (`service.py:161-163`, comment `service.py:53`).
7. `GET /alerts` reads `_STATE` directly and returns it to the Grafana panel's data client (`service.py:181-186`).

**Calibration (calibrate.py, one-shot CLI run):**
1. Same `build_windows` → snapshot-predict call, but the request omits `target_fpr` (`calibrate.py:52-54`).
2. `decision.calibrated_probability` pulled from every returned record, appended to a per-entity list (`per[entity_id]`) and a pooled list (`allv`) (`calibrate.py:72-76`). No `entity_type` filtering here — every scored entity (devices, tunnels, interfaces, VRFs) is sampled.
3. Loop runs for `--minutes`, sleeping `--interval` s between ticks (`calibrate.py:68-81`).
4. Quantiles computed over `allv` (global) and each `per[e]` with ≥10 samples (per-entity), written to `calibration.json` (`calibrate.py:83-90`).
5. `service.py`'s `_load_calib()` reads that file once, at loop startup, into `_CALIB` (`service.py:154`, `61-64`); only consumed by `_alerts_threshold`.

## Calculations

**Timestamp bucketing** — `(int(time.time()) // 30) * 30`, floors to a 30s grid before formatting as ISO (`service.py:67-69`, `calibrate.py:41-43`). Both `service.py` and `calibrate.py` use it identically.

**EWMA baseline update** (rank mode, non-over entities only):
```
baseline[e] = p_any[e]                                   (first observation)
baseline[e] = (1 - EWMA_ALPHA) * baseline[e] + EWMA_ALPHA * p_any[e]   (subsequent, when not over-margin)
```
(`service.py:126-132`). Inputs: `EWMA_ALPHA` (default 0.2), current tick's `p_any`.

**Rise / over-margin test** (rank mode, device entities only):
```
over = (baseline[e] is not None) and (p_any[e] >= MIN_RISK) and (p_any[e] - baseline[e] >= RISE_MARGIN)
```
(`service.py:115-117`). Inputs: `p_any` from current tick, `baseline[e]` from prior ticks, `MIN_RISK`, `RISE_MARGIN`.

**Consecutive-tick confirmation counter:**
```
_OVER[e] = _OVER.get(e, 0) + 1 if over else 0
fires when: over and _OVER[e] >= CONSEC
```
(`service.py:118, 121`). Counter resets to 0 on ANY non-over tick (no decay/hysteresis) — a single tick dipping under the margin restarts confirmation from zero.

**Baseline freeze** — while an entity is over-margin (confirming or firing, `_OVER[e] > 0`), its baseline is updated via `_BASELINE.setdefault(e, r["p_any"])` (`service.py:128-129`). Since the entity already has a baseline key from before the rise started, `setdefault` is a no-op — the baseline literally stops moving at its last pre-rise EWMA value for the whole duration of the alert. This is what stops a sustained fault's own spike from eroding its baseline and self-clearing.

**rise value reported:**
```
rise = p_any[e] - baseline[e]   (only for confirmed alerts)
```
(`service.py:122`), alert list sorted by `-rise`, truncated to `TOP_K` (`service.py:133-134`).

**Threshold-mode fire condition** (device entities only):
```
thr = threshold_per_entity.get(entity_id, threshold_global)
fires when: calibrated_probability >= thr
```
(`service.py:139-147`); `threshold_global` falls back to `0.9` if `calibration.json` missing the key. Alert list sorted by `-calibrated_probability` (`service.py:149`), no `TOP_K` cap applied in threshold mode.

**Calibration quantiles** (`calibrate.py:83-89`):
```
q = 1.0 - target_fpr
threshold_global = quantile(allv, q)                       # pooled across all entities/ticks
threshold_per_entity[e] = quantile(per[e], q)   if len(per[e]) >= 10
```
`allv`/`per[e]` are pooled `calibrated_probability` samples from healthy-lab ticks. `np.quantile` (linear interpolation, numpy default).

## Config & schemas

**`pa_alerts/calibration.json`** — written by `calibrate.py:90`, read by `service.py:61-64`:

| field | type | produced | consumed |
|---|---|---|---|
| `collected_ticks` | int | `calibrate.py:85` (loop-tick counter) | not read by service.py — informational |
| `n_samples` | int | `calibrate.py:85` (`len(allv)`) | not read by service.py — informational |
| `target_fpr` | float | `calibrate.py:85` (`args.target_fpr`) | not read by service.py — informational |
| `threshold_global` | float | `calibrate.py:86` | `service.py:139`, fallback into `_alerts_threshold` |
| `threshold_per_entity` | dict[str, float] | `calibrate.py:88`, entities with ≥10 samples | `service.py:140`, per-entity lookup in `_alerts_threshold` |

Current file on disk (`pa_alerts/calibration.json`): `collected_ticks=37`, `n_samples=9472`, `target_fpr=0.01`, `threshold_global=0.9614`, `threshold_per_entity` has ~230 entries spanning every entity type seen during that collection run (devices `ce_*`/`pe*`/`p*`, tunnels `ce_branchN-ce_hubM`, interfaces `eth*`/`lo`/`wg0`, VRFs `vrf_*`/`CORP`/`GUEST`/`VOICE`).

**PA snapshot-predict interface** (external service, `{PA_URL}/v1/predict/snapshot`) — request/response fields this code touches:
- Request (service.py): `{topology_id, window_end_ts, entities: [{entity_id, device, entity_type, vrf, window, entity_type_code, site_type_code}], target_fpr}` (`service.py:78-84`).
- Request (calibrate.py): same `entities` shape, but **no `target_fpr` key** (`calibrate.py:49-54`).
- Response `records[]` fields read: `entity_id`, `entity_type`, `device`, `risk.p_any_fault_in_horizon`, `risk.fault_types[0].cause`, `risk.fault_types[0].time_to_impact.median_s`, `decision.calibrated_probability` (`service.py:90-96`, `calibrate.py:73-76`).

**`GET /alerts` response** (`service.py:181-186`): `{ts, mode, warm, alerts: [...], predictions: [...], n_scored}`. `alerts` rows are `_row()` output plus `baseline`/`rise` (rank mode, `service.py:122`) or `threshold` (threshold mode, `service.py:148`).

**`GET /health` response** (`service.py:174-178`): `{status, mode, pa_url, interval_s, calibrated, last_ts, n_scored, error}`. `calibrated` = `bool(_CALIB)` — true iff `calibration.json` existed at loop startup.

## Gotchas

- `calibrate.py`'s predict request omits `target_fpr` (`calibrate.py:52-54`) while `service.py`'s includes it (`service.py:84`) — the two callers hit the same endpoint with different request shapes; if the PA service's `target_fpr` default differs from `TARGET_FPR`, `calibration.json` is calibrated against a different regime than what serving actually sends.
- `calibrate.py` samples every entity type unfiltered (`calibrate.py:72-76`), but both `_alerts_rank` and `_alerts_threshold` only ever alert on `entity_type == "device"` (`service.py:113`, `143`) — most of `threshold_per_entity` (tunnels, interfaces, VRFs) is collected and written but never looked up at alert time.
- `_OVER` counter resets to 0 on any single non-over tick (`service.py:118`) — no partial credit; a `p_any` that dips one tick below `RISE_MARGIN` and rises again restarts the `CONSEC` count from scratch.
- Baseline freeze via `setdefault` (`service.py:129`) only initializes a missing key; it is a no-op once a baseline exists, which is the normal case during an alert — read this as "stop updating," not "reset to a captured value."
- `MODE` is read once from env at import time into a module constant (`service.py:41`); changing `PA_ALERT_MODE` after the process starts has no effect without a restart.
- `calibration.json` is loaded once at loop startup (`_load_calib()` called inside `_loop()` before the `while True`, `service.py:154`) — rerunning `calibrate.py` while `service.py` is live does not pick up the new thresholds until restart.
- `threshold_global` silently falls back to `0.9` (`service.py:139`) if `calibration.json` is absent or missing the key — no error raised; only visible signal is `/health`'s `calibrated: false`.
- 30s timestamp bucketing (`service.py:67-69`) with default `INTERVAL=15` means two consecutive ticks can compute the identical `window_end_ts` and re-request the same window from the PA service before the bucket advances.
- On exception in `_loop()`, only `_STATE["error"]` is set; `ts`/`predictions`/`alerts`/`n_scored` are left at their last-good values (`service.py:164-165`) — `/alerts` keeps serving stale data silently during an outage; `/health` is the only place the error surfaces.
- Predictions cap of 20 (`service.py:161`) is a separate constant from the rank-mode `TOP_K` alert cap — if more than 20 devices score in a tick, some scored devices are absent from `predictions` even though they were scored (`n_scored` still counts them).
