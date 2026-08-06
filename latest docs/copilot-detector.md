# Copilot — Detector

## Purpose
Anomaly-detection layer for the predictive-NOC copilot: takes per-device telemetry
rows and decides whether the device is down / faulted / cause. **`detect.py` is an
unimplemented stub** — both public functions raise `NotImplementedError`
(`copilot/detector/detect.py:1-6`). The module's contract is only known through its
test file (`test_detect.py`) and the fixture builder (`make_fixture.py`); no working
detection logic exists yet in this tree. `__init__.py` re-exports `detect`/`scan`
unchanged (`copilot/detector/__init__.py:1-3`).

Verified by running the test suite: it fails on the first call into `detect()` with
`NotImplementedError` (`copilot/detector/detect.py:2`), confirmed live via
`python3 -m copilot.detector.test_detect`.

## Entry points
No CLI/HTTP entry points in `detect.py` (no `__main__` block, no FastAPI routes —
it's a pair of bare functions). Two runnable scripts exist:

- Fixture sampler:
  ```
  python3 -m copilot.detector.make_fixture [SRC.parquet]
  ```
  Default `SRC` = `synthetic/output/synthetic_multi_d0.2_s30_x3.0_n12.parquet`
  (`copilot/detector/make_fixture.py:14-15,25-26,118`). Writes
  `copilot/detector/fixtures/corpus.parquet`.

- Test / self-check (no framework, assert-based, repo style):
  ```
  python3 -m copilot.detector.test_detect
  ```
  (`copilot/detector/test_detect.py:8`). Currently fails immediately because
  `detect()` is unimplemented (verified above).

## Modules
- **`detect.py`** — stub. `detect(rows)` (`copilot/detector/detect.py:1-2`): intended
  to grade one device-bucket (a `DataFrame` grouped by
  `[topology_id, device, ts]`, per `test_detect.py:26,32`) and return a verdict with
  at least `down`, `faulted`, `cause` fields (inferred from
  `test_detect.py:44,115-116`). `scan(df)` (`copilot/detector/detect.py:5-6`):
  intended to run `detect` over every device-bucket in `df` and return one verdict
  row per bucket, same three fields plus `device`/`ts`
  (`test_detect.py:113-116`). Both raise `NotImplementedError`.
- **`make_fixture.py`** — one-time sampler, not part of the runtime pipeline. Carves
  a small committed TDD corpus out of a full (gitignored) synthetic run
  (`copilot/detector/make_fixture.py:1-9`). `main(src=DEFAULT_SRC)`
  (`copilot/detector/make_fixture.py:58-114`): reads the source run, samples fault /
  hard-negative / clean windows, writes `fixtures/corpus.parquet`. Helpers:
  `_s0(x)` unwraps a length-1 list/array/tuple to its scalar
  (`copilot/detector/make_fixture.py:46-49`); `_device_span(df, topo, device, t0, t1)`
  slices all rows for one device over a closed time range
  (`copilot/detector/make_fixture.py:52-55`).
- **`__init__.py`** — re-exports `detect`, `scan` (`copilot/detector/__init__.py:1-3`).
- **`test_detect.py`** — assert-based tests, no framework. Run with
  `python3 -m copilot.detector.test_detect`. Tests: `test_every_fault_type_detected`,
  `test_server_down_detected_per_type`, `test_hard_negatives_do_not_fire`,
  `test_clean_baseline_quiet`, `test_scan_shape`. These encode the *intended*
  contract for `detect`/`scan` (see Parameters — recall/FP-rate floors).

## Parameters
`detect.py` has no tunables (it's an unimplemented stub — no thresholds exist in
code yet). Table below covers every constant in `make_fixture.py` (the only
implemented logic in this subsystem) plus the acceptance thresholds `test_detect.py`
will hold the eventual `detect()`/`scan()` to.

| name | default | env-var/CLI-flag | units | what it controls | source (file:line) |
|---|---|---|---|---|---|
| `DEFAULT_SRC` | `synthetic/output/synthetic_multi_d0.2_s30_x3.0_n12.parquet` | positional CLI arg (`sys.argv[1]`) | path | source parquet run sampled to build the fixture | make_fixture.py:25-26 |
| `OUT` | `copilot/detector/fixtures/corpus.parquet` | none | path | fixture output path | make_fixture.py:27 |
| `PER_FAULT` | 30 | none | episodes | fault episodes sampled per `fault_type` | make_fixture.py:38 |
| `N_HARD_NEG` | 250 | none | windows | hard-negative windows sampled | make_fixture.py:39 |
| `N_CLEAN` | 250 | none | windows | clean baseline windows sampled | make_fixture.py:40 |
| `PAD` | 2 | none | grid buckets | context padding around a fault/hard-neg run's min/max ts | make_fixture.py:41,75,86 |
| `CLEAN_LEN` | 12 | none | grid buckets | span of a sampled clean window | make_fixture.py:42,104 |
| `SEED` | 7 | none | — | `np.random.default_rng` seed for reproducible sampling | make_fixture.py:43,63 |
| `DETECT_COLS` | 15 named columns (device, entity, entity_type, ts, if_oper_status, if_in_octets, if_out_octets, tunnel_loss_pct, tunnel_latency_ms, tunnel_jitter_ms, cpu_pct, q_backlog_bytes, if_out_discards, xcvr_rx_power_dbm, xcvr_tx_bias_ma) | none | — | detector-visible telemetry columns read from source + kept in fixture | make_fixture.py:31-34 |
| `SAMPLER_COLS` | 5 named columns (topology_id, scenario_id, fault_type, is_fault, is_hard_negative) | none | — | label/key columns read from source for sampling + grading (not detector-visible) | make_fixture.py:35-36 |
| non-best-effort recall floor | 0.90 | none | fraction | min per-`fault_type` recall for `down or faulted` on fault buckets, for every type not in `BEST_EFFORT` | test_detect.py:66 |
| down-rule recall floor | 0.90 | none | fraction | min recall of `detect().down` specifically, on the 6 `IFACE_DOWN` fault types | test_detect.py:83 |
| hard-neg FP-rate ceiling | 0.10 | none | fraction | max allowed `down or faulted` fire-rate on hard-negative trap buckets | test_detect.py:95 |
| clean FP-rate ceiling | 0.05 | none | fraction | max allowed `down or faulted` fire-rate on clean baseline buckets | test_detect.py:107 |
| `IFACE_DOWN` | 6 fault types (node_failure, p_node_failure, srlg_cut, pop_isolation, core_partition, mpls_underlay_failure) | none | — | fault types that physically drop a link (`if_oper_status`→down); each held to the down-rule recall floor | test_detect.py:20-21 |
| `BEST_EFFORT` | 2 fault types (gray_failure, path_asymmetry) | none | — | fault types exempted from the 0.90 recall floor (signal-starved by design); only required to be non-zero recall | test_detect.py:22-24,68-69 |

## Data flow
**Fixture build (implemented):**
`synthetic/output/*.parquet` (full synthetic generator run, gitignored, ~241MB /
17M rows per `make_fixture.py:6-7`, another subsystem's output)
→ `pq.read_table(src, columns=DETECT_COLS + SAMPLER_COLS)` (make_fixture.py:60)
→ `fault_type`/`scenario_id` unwrapped from length-1 containers via `_s0`
(make_fixture.py:61-62)
→ grid bucket `step` = median of diffs of sorted unique `ts` (make_fixture.py:64)
→ three window-sampling passes, each keyed by RNG `SEED`:
  - fault windows: one per sampled `scenario_id` within each `fault_type` group, span
    = episode `[ts.min()-PAD*step, ts.max()+PAD*step]` for that episode's device
    (make_fixture.py:68-77)
  - hard-negative windows: contiguous `is_hard_negative` runs per
    `(topology_id, device)`, split on gaps `> step`, padded by `PAD*step`
    (make_fixture.py:79-91)
  - clean windows: devices/topologies with zero fault and zero hard-negative rows,
    random `CLEAN_LEN`-bucket span (make_fixture.py:93-106)
→ all windows concatenated, columns reduced to
  `DETECT_COLS + [window_id, truth_kind, truth_fault_type]`
(make_fixture.py:108-109)
→ written to `copilot/detector/fixtures/corpus.parquet` (make_fixture.py:110-111).

**Detection (unimplemented):** intended input is a per-device-bucket telemetry
`DataFrame` (`rows` grouped by `[topology_id, device, ts]`, test_detect.py:26,32) →
`detect(rows)` would transform it into a verdict dict with `down`/`faulted`/`cause`
→ `scan(df)` would run this over every bucket in a full `DataFrame` and return one
verdict row per bucket. Neither transform exists; both calls raise
`NotImplementedError` (detect.py:2,6).

## Calculations
Only implemented derived values are in `make_fixture.py` (sampling geometry, not
detection):

- **Grid bucket step** = `median(diff(sort(unique(df.ts))))`, a `Timedelta`
  (make_fixture.py:64). Used as the padding unit for every window.
- **Fault window bounds** = `[episode.ts.min() - PAD*step, episode.ts.max() + PAD*step]`
  for the episode's `(topology_id, device)` (make_fixture.py:75).
- **Hard-negative window bounds** = `[run.min() - PAD*step, run.max() + PAD*step]`
  per contiguous run of `is_hard_negative` timestamps, where a run breaks wherever
  `diff(ts) > step` (make_fixture.py:83-86).
- **Clean window bounds** = `[start, start + CLEAN_LEN*step]`, where `start` is a
  uniformly random element of the device's timestamp grid, excluded from the last
  `CLEAN_LEN` slots (make_fixture.py:103-104).
- **Recall** (test acceptance metric, not a detector output) =
  `hit[fault_type] / tot[fault_type]`, where `hit` counts buckets where
  `down or faulted` (or `down` alone, for the down-rule test) is true on a labelled
  fault bucket (test_detect.py:59,62 and 79,81).
- **False-positive rate** (test acceptance metric) = `fired / tot` over hard-negative
  or clean buckets, `fired` = count where `down or faulted` is true
  (test_detect.py:43-44,88-93,100-105).

No detection-score/threshold math exists yet — `detect()`/`scan()` are stubs.

## Config & schemas
**`fixtures/corpus.parquet`** — schema as written by the current `make_fixture.py`
(make_fixture.py:31-36,108-109):

| field | dtype (source) | meaning |
|---|---|---|
| device | str | device id |
| entity | — | sub-device entity (interface/tunnel/etc.) |
| entity_type | str | entity kind |
| ts | timestamp | bucket timestamp on the telemetry grid |
| if_oper_status | float | interface oper status signal |
| if_in_octets, if_out_octets | float | interface counters |
| tunnel_loss_pct, tunnel_latency_ms, tunnel_jitter_ms | float | tunnel health signals |
| cpu_pct | float | device CPU |
| q_backlog_bytes | float | queue backlog |
| if_out_discards | float | interface discard counter |
| xcvr_rx_power_dbm, xcvr_tx_bias_ma | float | optics telemetry |
| window_id | int | sampler-assigned id, one per sampled window (make_fixture.py:76,90,105) |
| truth_kind | str: `"fault"` \| `"hard_neg"` \| `"clean"` | withheld ground truth kind (make_fixture.py:76,90,105) |
| truth_fault_type | str or null | ground-truth `fault_type` for `"fault"` windows, else null (make_fixture.py:76) |

`test_detect.py` does **not** consume `window_id`/`truth_kind`/`truth_fault_type` —
it groups by `[topology_id, device, ts]` and reads `is_fault`, `fault_type`,
`is_hard_negative` directly per row (test_detect.py:26,29-40). So the fixture must
also carry `topology_id`, `is_fault`, `fault_type`, `is_hard_negative` (`SAMPLER_COLS`,
make_fixture.py:35-36) even though the schema table in `make_fixture.py`'s own OUT
comment only lists `DETECT_COLS + [window_id, truth_kind, truth_fault_type]` — see
Gotchas, the on-disk fixture and the code-declared OUT schema disagree.

No JSON/YAML config files in this subsystem.

## Gotchas
- **`detect.py` is 100% stub.** `detect(rows)` and `scan(df)` both immediately
  `raise NotImplementedError` (detect.py:1-6). Anything importing
  `copilot.detector` and calling either function fails at first use. Verified live:
  `python3 -m copilot.detector.test_detect` dies on the first `detect(g)` call
  (test_detect.py:33) inside `test_clean_baseline_quiet`.
- **On-disk fixture schema does not match what `make_fixture.py` currently
  declares.** Reading the committed-looking file at
  `copilot/detector/fixtures/corpus.parquet` (45,492 rows) shows columns:
  `device, entity_type, ts, if_oper_status, tunnel_loss_pct, tunnel_latency_ms,
  cpu_pct, q_backlog_bytes, if_out_discards, xcvr_rx_power_dbm, xcvr_tx_bias_ma,
  fault_type, is_fault, is_hard_negative, topology_id, tti`. Missing vs.
  `make_fixture.py`'s current `DETECT_COLS`/OUT schema: `entity`, `if_in_octets`,
  `if_out_octets`, `tunnel_jitter_ms`, `window_id`, `truth_kind`,
  `truth_fault_type`, `scenario_id`. Present but undeclared anywhere in
  `make_fixture.py`: `tti`. The file on disk was produced by a different generator
  than the one currently checked in — re-run
  `python3 -m copilot.detector.make_fixture` against a real synthetic source before
  trusting the fixture, or before writing `detect()` against its columns.
- **The fixture is untracked in git**, despite `make_fixture.py`'s docstring
  calling `fixtures/corpus.parquet` "committed" (make_fixture.py:9). `git status`
  shows `copilot/detector/` as an untracked directory (`?? copilot/detector/`) as
  of this branch — nothing under it, including the fixture, is actually committed
  yet.
- **`make_fixture.py` requires a real synthetic run on disk** — `main()` asserts
  `os.path.exists(src)` and gives no fallback (make_fixture.py:59). The default
  source (`synthetic/output/synthetic_multi_d0.2_s30_x3.0_n12.parquet`) is
  gitignored per the docstring (make_fixture.py:6-7), so a fresh checkout cannot
  regenerate the fixture without first generating (or otherwise obtaining) that
  241MB run.
- **`window_id`/`truth_kind`/`truth_fault_type` are dead weight for
  `test_detect.py`** — the test derives ground truth itself from `is_fault` /
  `fault_type` / `is_hard_negative` per row (test_detect.py:34-40), grouping by
  `[topology_id, device, ts]`, not by `window_id`. The sampler's withheld-truth
  columns are unused by the only consumer in this tree.
- **`BEST_EFFORT` fault types have no floor beyond "not totally blind"** — 
  `gray_failure` and `path_asymmetry` are exempt from the 0.90 recall bar and only
  need `hit[ft] > 0` (test_detect.py:68-69). A future `detect()` that barely catches
  one instance of each still passes.
