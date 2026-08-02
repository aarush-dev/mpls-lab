# `synthetic/` — data realism + synthetic augmentation

Calibrate to the **real lab capture**, then emit large **labeled** multivariate
time-series in the **exact same schema** as the real data — so synthetic + real
Parquets are interchangeable/concatenable for ML training.

Caveman+ponytail: stdlib + numpy + pandas/pyarrow only. No new deps, no
over-engineering. We **read** `dataapi/`'s outputs and schema; we never modify
`generator/`, `dataapi/`, `telemetry/`, or `faults/`.

```
synthetic/
  calibrate.py   # real Parquet -> profile.json (derive, don't hardcode)
  profile.json   # the calibration profile (committed; small)
  generate.py    # profile.json -> large labeled Parquet in output/
  check.py       # assert-based gate (schema/non-empty/fault%/precursors/ranges/concat)
  output/        # generated Parquets (gitignored — large)
```

## Run

```bash
cd synthetic
python3 calibrate.py            # 1. build profile.json from dataapi/datasets/*.parquet
python3 generate.py             # 2. defaults: 2 days, 30s step -> output/*.parquet (5,178,240 rows,
                                 #    49 cols, verified against the current 70-device profile.json)
python3 check.py                # 3. verify schema/labels/ranges/concat-compat (9 gates)
python3 verify_fixes.py <train> <holdout>   # 4. the audit's 24-check acceptance gate
```

## Calibration approach (`calibrate.py` → `profile.json`)

Everything the generator needs is **derived from the real capture**, not made
up — and where the thin real sample (~15 min, one capture window) can't support
a statistic, we fall back to a sane default and mark it `"_src":"default"` in the
JSON (auditable, ponytail-commented in code). Measured:

- **Interface octet rate per `site_type`** — diff of cumulative SNMP counters
  (bytes/step), median per site; plus an **octet seed** (real per-site absolute
  median) so synthetic counter ranges *overlap* the real data.
- **Tunnel baseline** — `latency/jitter/loss/rekeys` mean/std/p50/min/max from
  the non-fault tunnel rows.
- **Fault signature PEAKS** — per `fault_type`, the peak tunnel metric
  perturbation from the labeled rows (congestion/bgp_flap/policy_drift come from
  real rows; the rest use defaults matching the `faults/` README signatures).
  **Not the lead**: `lead_s` is no longer taken from the capture. A 24.5-minute
  window at 30 s resolution cannot estimate a precursor length — it gave ~2 s,
  which the 4-bucket floor clamped to a constant 120 s. Leads come from
  `../faults/leadpriors.py`, shared with the live orchestrator; a measured median
  is kept as `lead_s_hint` only when the capture spans a full `DIURNAL_PERIOD`.
- **Inventory** — the real device→{site_type, interfaces, tunnels} map, so
  synthetic device/entity naming is **identical to the lab** (`ce_branch1`,
  `eth1`, `ce_branch1-ce_hub1`, …).

## Realism features (`generate.py`)

- **Diurnal curves** — business-hours load multiplier (peak ~15:00 UTC, trough
  ~03:00, weekend dip) drives octet growth and adds congestion latency to
  tunnels. *(ponytail: the diurnal SHAPE is modelled — the real sample has no
  full daily cycle — but amplitude/baseline are calibrated.)*
- **Cumulative octet counters** seeded to overlap real per-site ranges.
- **Labeled fault episodes** — the same scenario TYPES as `faults/`:
  `congestion`, `bgp_flap`, `tunnel_degrade`, `policy_drift` + extras
  (`node_failure`, `asymmetric_loss`, `brownout`).
- **Precursor semantics for ML** — each episode ramps the relevant metric over
  `[t_start, t_impact]` (the drawn lead) so a precursor is **visible before
  impact**, crosses the VRF's SLA objective at `t_impact`, climbs to the
  calibrated peak during the hold, then decays. `time_to_impact_s` is `>0` before
  impact and `<0` after — exactly as `dataapi/export.py` defines it — but it is a
  LIST (one entry per concurrent episode), so use `export.precursor_mask(df)`.
- **Concurrency** — episodes conflict only when they share a `kind`, and 22% of
  them drag a second, different-kind fault onto the same device, so rows carry up
  to 3 overlapping labels for a multi-label head.
- **`if_in_errors` / `if_in_discards` / `if_out_errors` are emitted as 0**, matching
  a container lab where veth pairs raise no CRC or input errors. Generating them
  made `if_in_errors > 0` a perfect synthetic-row detector. Reserved for real
  hardware; `check.py` asserts they stay zero.

## Schema == dataapi canonical

`generate.py` imports `dataapi/export.COLUMNS` as the single source of truth for
the 49 columns and their order; dtypes are matched to the real Parquet
(`is_fault` bool, metrics float64, label strings object, `vrf` null like the real
capture). **Real + synthetic concatenate cleanly** (`check.py` proves it), so the
ML team can train on either or both.

## `--scale` knob (ML-scale generation)

| flag | meaning | default |
|------|---------|---------|
| `--days`  | days of telemetry to emit | `2.0` (demo) |
| `--step`  | bucket seconds (match export) | `30` |
| `--scale` | fault-episode **density** multiplier | `1.0` |
| `--seed`  | RNG seed — change for an independent holdout | `42` |

Row count = `entities_per_tick × (days·86400/step)`, where `entities_per_tick`
is the current `profile.json` inventory size (661 interfaces + 168 tunnels +
70 devices = 899, as of the live-lab recalibration). `--scale` only changes
fault-episode density, not row count. At `entities_per_tick=899`:

```bash
python3 generate.py --days 2                     # default: 5,178,240 rows
python3 generate.py --days 7 --scale 3           # ~18.1M rows, denser faults
python3 generate.py --days 30 --scale 6 --step 30   # ~77.7M rows, month, ML-scale
python3 generate.py --days 1 --scale 3 --seed 7   # holdout: 0 scenario_id overlap with seed 42
```

One seed = one RNG (`generate.generate`), so a different `--seed` gives independent
fault draws, targets and noise while keeping the same `profile.json`
distributions. Output filename gains a `_seed<N>` suffix for any seed but 42, and
the seed is written into the Parquet metadata. Split on `scenario_id`, not on
time — see `../DATASETS.md`.

`entities_per_tick` moves with the lab (it was ~149 before the 70-device
recalibration in `../docs/`'s "recalibrate profile.json" commit) — recompute
from `profile.json`'s `inventory` before trusting a cached row-count figure.

> `check.py` gate 0 requires `synthetic=true`, `seed` AND `calibrated_from` in the
> Parquet's file-level metadata. Every file in `output/` that lacked them was
> deleted (14 of 16) — an unattributable Parquet cannot be traced to a capture or
> reproduced, and one shipped anyway once. Regenerate rather than ship a stale file.

(To grow entity count too, scale the lab via `topology-spec.yaml` knobs, re-export
a real window, re-run `calibrate.py` — the new inventory flows through.)
