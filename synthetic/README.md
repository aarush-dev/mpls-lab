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
                                 #    40 cols, verified against the current 70-device profile.json)
python3 check.py                # 3. verify schema/labels/ranges/concat-compat
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
- **Fault signatures** — per `fault_type`, the peak tunnel metric perturbation
  and the `lead_time` measured from the labeled rows (congestion/bgp_flap/
  policy_drift come from real rows; tunnel_degrade/node_failure/asymmetric_loss/
  brownout use defaults matching the `faults/` README signatures).
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
  `[t_start, t_impact]` (lead_time window) so a precursor is **visible before
  impact**, then peaks and decays. `time_to_impact_s` is `>0` before impact and
  `<0` after — exactly as `dataapi/export.py` defines it. This is the whole
  point: the model must predict within the lead window.

## Schema == dataapi canonical

`generate.py` imports `dataapi/export.COLUMNS` as the single source of truth for
the 40 columns and their order; dtypes are matched to the real Parquet
(`is_fault` bool, metrics float64, label strings object, `vrf` null like the real
capture). **Real + synthetic concatenate cleanly** (`check.py` proves it), so the
ML team can train on either or both.

## `--scale` knob (ML-scale generation)

| flag | meaning | default |
|------|---------|---------|
| `--days`  | days of telemetry to emit | `2.0` (demo) |
| `--step`  | bucket seconds (match export) | `30` |
| `--scale` | fault-episode **density** multiplier | `1.0` |

Row count = `entities_per_tick × (days·86400/step)`, where `entities_per_tick`
is the current `profile.json` inventory size (661 interfaces + 168 tunnels +
70 devices = 899, as of the live-lab recalibration). `--scale` only changes
fault-episode density, not row count. At `entities_per_tick=899`:

```bash
python3 generate.py --days 2                     # default: 5,178,240 rows
python3 generate.py --days 7 --scale 3           # ~18.1M rows, denser faults
python3 generate.py --days 30 --scale 6 --step 30   # ~77.7M rows, month, ML-scale
```

`entities_per_tick` moves with the lab (it was ~149 before the 70-device
recalibration in `../DOCS/`'s "recalibrate profile.json" commit) — recompute
from `profile.json`'s `inventory` before trusting a cached row-count figure.

> Any previously generated `output/*.parquet` predating that recalibration
> (fewer devices, 21 columns, no `synthetic=true` file metadata) is stale and
> fails `check.py` gate 0 — regenerate rather than ship it.

(To grow entity count too, scale the lab via `topology-spec.yaml` knobs, re-export
a real window, re-run `calibrate.py` — the new inventory flows through.)
