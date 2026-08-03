# Committed sample datasets

Three Parquet files are tracked in git as reference samples. Everything else under
`dataapi/datasets/` and `synthetic/output/` stays gitignored — regenerate it.

All three use the canonical **59-column** schema (`dataapi/export.py:COLUMNS`). They
are concat-compatible: `pd.concat([real, synth])` needs no reindex. (The three
committed samples predate the closing-pass columns — see *Closing-pass additions*
below; `finalize_schema` back-fills the 10 new columns as null on any old file.)

| file | rows | source | window |
|---|---|---|---|
| `dataapi/datasets/dataset_1785032386_1785033870_30s.parquet` | 49,844 | live 148-container lab | 2026-07-26 02:19:30Z → 02:44:00Z (24.5 min) |
| `synthetic/output/synthetic_1781481600_d1.0_s30_x3.0.parquet` | 2,589,120 | `synthetic/generate.py` seed 42 — **train** | 2026-06-15 00:00:00Z → 23:59:30Z (1 day) |
| `synthetic/output/synthetic_1781481600_d1.0_s30_x3.0_seed7.parquet` | 2,589,120 | same generator, **seed 7** — holdout | same day, matched diurnal coverage |

Row count is exact arithmetic: 899 keys/bucket (661 interface + 168 tunnel + 70
device) × 2,880 buckets = 2,589,120.

## Contents

| | real | train (seed 42) | holdout (seed 7) |
|---|---|---|---|
| devices | 70 | 70 | 70 |
| `entity_type=interface` rows | 37,944 | 1,903,680 | 1,903,680 |
| `entity_type=tunnel` rows | 8,400 | 483,840 | 483,840 |
| `entity_type=device` rows | 3,500 | 201,600 | 201,600 |
| `is_fault=1` rows | 391 | 159,021 | 156,054 |
| precursor rows | 266 | 124,108 | 122,627 |
| distinct `scenario_id` | 17 | 719 | 720 |
| distinct `fault_type` | 10 | 21 (all of them) | 21 (all of them) |
| rows with ≥2 concurrent faults | 2 | 15,811 | 17,324 |
| max `n_concurrent` | 2 | 3 | 3 |
| `lead_time_s` CV | 1.30 | 0.83 | 1.03 |
| `vrf` populated | 28.1% | 31.1% | 31.1% |

"Precursor rows" = `is_fault` and at least one entry of `time_to_impact_s` > 0.
The label columns are lists now, so `time_to_impact_s > 0` no longer works on them
— use `export.precursor_mask(df)`.

`impact_method` mix — how each episode's `t_impact` was established:

| | real | train | holdout |
|---|---|---|---|
| `vm_threshold` (probe crossed) | 126 | 0 | 0 |
| `ramp_derived` (SLA crossing inside the ramp) | 0 | 86,211 | 86,411 |
| `modelled_fallback` | 18 | 0 | 0 |
| `modelled` | 249 | 89,278 | 87,578 |

The real capture predates `ramp_derived` (`faults/orchestrator.py` gained it with
the lead-prior work); a fresh live campaign will produce it.

## The holdout file

`--seed 7` reseeds the single generator RNG (`synthetic/generate.py:generate`), so
fault draws, targets and noise are independent of the seed-42 file. Verified: the
two share **0** `scenario_id` values (719 vs 720, intersection empty), which is the
split key. Both run a full day, so evaluating on seed 7 tests unseen fault episodes
and not unseen time-of-day — an earlier 12 h holdout had 0.32× the train median
`if_in_octets` and conflated the two.

Both files come from the same `profile.json`, so the *distributions* are shared by
construction. Seed 7 is a holdout over episodes, not over calibration; it does not
test generalisation to a different network.

The seed is in the Parquet file metadata (`seed`, `calibrated_from`, `generator`,
`synthetic=true`), and `synthetic/check.py` asserts `seed` and `calibrated_from` are
present — an unattributable file cannot ship. Seed 42 keeps its old filename; the
`_seed<N>` suffix only appears when the seed is not 42.

## Caveats

- **`if_in_errors`, `if_in_discards`, `if_out_errors` are deliberately 0 in both
  paths.** Container veth pairs produce no CRC or input errors, so every real
  capture has them constant zero. The synthetic path used to generate them from a
  load-dependent Poisson process, which made `if_in_errors > 0` a perfect
  synthetic-row detector — a shortcut for any model trained on the mixed corpus,
  and three features that vanish at live validation. The SNMP OIDs are polled
  correctly (`telemetry/telegraf/telegraf.conf`) and **will populate on real
  hardware**; retrain with them at deployment. `if_out_discards`, `q_drops` and
  `q_backlog_bytes` are genuinely measured (`tc -s qdisc`) and keep their dynamics.
- **`vrf` is a set on tunnel rows.** A WireGuard tunnel carries every VRF of its
  site (`controller.VRF_PREFERRED_HUB` is a preference, not a binding), so tunnel
  rows carry the joined set — `CORP+VOICE` on a branch, `CORP+GUEST+VOICE` on a
  hub/dc. Interface rows carry the single real VRF where the interface is
  VRF-scoped. Null on physical interfaces and device rows.
- **`lead_time_s` is a prior, not a measurement.** Drawn per episode from
  `faults/leadpriors.py` (lognormal, p10/p90 pinned to a per-fault-type bucket
  range). The previous value came from the median lead of this 24.5-minute capture
  — about 2 s, which the 4-bucket floor then clamped to exactly 120 s on 100% of
  episodes. See `docs/SPEC-NOTES.md` for the per-group reasoning.
- **Chassis and optical columns are modelled** — containers have no sensors.
  `device_temp_c`, `device_power_watts`, `device_fan_rpm`, `device_psu_voltage_v`,
  `xcvr_*` come from `telemetry/envmodel.py`, imported by both the live sidecar and
  the generator so they cannot drift. Per-column table in
  `docs/03_TECHNICAL_CODE_GUIDE.md`.
- **VPNv4 dataplane was down during the real capture.** This kernel lacks
  `CONFIG_LWTUNNEL`, so MPLS label imposition fails (`docs/PHASE0ENVIRONMENT.md`).
  OSPF/LDP control-plane metrics are real; VRF-scoped forwarding metrics reflect a
  partial FIB.
- **The real capture's labels were re-joined, not re-measured.** `dataapi/reschema.py`
  re-ran the label join over the same metric columns from
  `faults/labels/labels.jsonl`, which is why its fault rows went 327 → 391: the old
  single-winner collapse dropped overlapping labels that the multi-label join keeps.
  No metric value changed.

## Closing-pass additions (G1–G11) — schema FROZEN at 59 columns

Ten columns added to the canonical schema (`dataapi/export.py:COLUMNS`, len 59):

| column | gap | meaning |
|---|---|---|
| `topology_id` | G1 | which of 12 topologies (10 train + 2 held-out); enables leave-one-topology-out |
| `stream` | G6 | `F` = fault-dense (campaign) / `N` = fault-free + hard negatives; a sampler composes any prevalence |
| `is_hard_negative` | G4 | near-miss row (congestion recedes below SLA, self-heal flap, drain, surge, collector dropout, redundant-link down). `is_fault` stays **False** |
| `is_root`,`cascade_parent_id`,`cascade_depth`,`cascade_motif_id`,`affected_entity_count` | G7 | root/affected dual-head supervision; cascades are depth 2-3, hop-1 same-device (concurrency), hops 2-3 graph-adjacent |
| `injection_seed` | G8 | per-fault RNG draw — reproduce one scenario in the air gap |

**New row key: `(stream, topology_id, device, entity, ts)`.** Streams F and N share
timestamps (independent realities of the same topology), so `stream` and
`topology_id` are part of the key now — group by them for per-series work
(counter monotonicity, etc.).

**Companion tables** written alongside the main Parquet by `generate_multi`:
- `*_events.parquet` (G2) — templated `(template_id, params)` control-plane events at EXACT ts (sub-bucket), from the fault ledger. `synthetic/events.py`.
- `*_topology_edges.parquet` (G3) — interval-encoded graph (`valid_from`/`valid_to`); link flaps close+reopen intervals. `synthetic/topology_paths.py`.
- `*_paths.parquet` (G3) — ordered `hop_sequence` for RouteNet link↔path passing; `path_type` ∈ `{wg_tunnel, ospf_spf_path}` only (no `ldp_lsp` — no MPLS dataplane on this WSL2 host, see `docs/SPEC-NOTES.md` G11).

Acceptance gate: `python3 verify_readiness.py <main> <events> <edges> <paths>`
(all checks pass on the short multi-topology run).

## Regenerate

```bash
# synthetic train + holdout (deterministic: same profile.json + flags + seed
# -> byte-identical file). EXECUTED: 2,589,120 rows each, ~48MB, ~2m25s each.
cd synthetic && python3 generate.py --days 1 --step 30 --scale 3.0
cd synthetic && python3 generate.py --days 1 --step 30 --scale 3.0 --seed 7

# real (needs a deployed lab + telemetry stack)
cd dataapi && python3 export.py --minutes 25 --step 30
# or via the API: curl -o d.parquet 'http://127.0.0.1:8000/datasets?build=true&step=30'

# bring an OLD exported Parquet onto the current schema without the lab
cd dataapi && python3 reschema.py datasets/<file>.parquet
```

Validate: `python3 synthetic/check.py [path]` (9 gates, newest by mtime if no
path), `python3 dataapi/check_dataset.py [path]` (JSON-schema contract),
`python3 synthetic/verify_fixes.py <train> <holdout>` (24 acceptance checks).
