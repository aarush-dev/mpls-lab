# Committed sample datasets

Two Parquet files are tracked in git as reference samples. Everything else under
`dataapi/datasets/` and `synthetic/output/` stays gitignored — regenerate it.

Both files use the canonical 40-column schema (`dataapi/export.py:32-56`). They are
concat-compatible: `pd.concat([real, synth])` needs no reindex.

| file | rows | source | window |
|---|---|---|---|
| `dataapi/datasets/dataset_1785032386_1785033870_30s.parquet` | 49,844 | live 148-container lab | 2026-07-26 02:19:30Z → 02:44:00Z (24.5 min) |
| `synthetic/output/synthetic_1781481600_d1.0_s30_x3.0.parquet` | 2,589,120 | `synthetic/generate.py`, seeded from `profile.json` | 2026-06-15 00:00:00Z → 23:59:30Z (1 day) |

## Contents

| | real | synthetic |
|---|---|---|
| devices | 70 | 70 |
| `entity_type=interface` rows | 37,944 | 1,903,680 |
| `entity_type=tunnel` rows | 8,400 | 483,840 |
| `entity_type=device` rows | 3,500 | 201,600 |
| `is_fault=1` rows | 327 | 60,440 |
| precursor rows (`is_fault=1 & time_to_impact_s>0`) | 149 | 12,421 |
| distinct `fault_type` | 9 | 21 (all of them) |

Real capture fault types: `asymmetric_loss`, `congestion`, `gray_failure`,
`mpls_underlay_failure`, `ospf_area_flap`, `p_node_failure`, `policy_drift`,
`rr_failure`, `tunnel_degrade`. Synthetic covers all 21 built by
`faults/scenarios.py`.

## Caveats on the real capture

- **Interface error counters are constant 0.** `if_in_errors`, `if_in_discards`,
  `if_out_errors` never move: veth pairs do not produce CRC/input errors. The SNMP
  OIDs are wired correctly (`telemetry/telegraf/telegraf.conf`) and will populate on
  real hardware. `if_out_discards` and `q_drops` do move (netem drops).
- **Chassis and optical columns are modelled, not measured** — containers have no
  sensors. `device_temp_c`, `device_power_watts`, `device_fan_rpm`,
  `device_psu_voltage_v`, `xcvr_*` come from `telemetry/envmodel.py`, which the live
  sidecar and the synthetic generator both import so they cannot drift. See
  `DOCS/03_TECHNICAL_CODE_GUIDE.md` for the per-column real-vs-modelled table.
- **VPNv4 dataplane was down during capture.** This kernel lacks `CONFIG_LWTUNNEL`,
  so MPLS label imposition fails (see `DOCS/PHASE0ENVIRONMENT.md`). OSPF/LDP
  control-plane metrics are real; VRF-scoped forwarding metrics reflect a partial FIB.

## Regenerate

```bash
# synthetic (deterministic: same profile.json + seed -> same file)
cd synthetic && python3 generate.py --days 1 --step 30 --scale 3.0

# real (needs a deployed lab + telemetry stack)
cd dataapi && python3 export.py --minutes 25 --step 30
# or via the API: curl -o d.parquet 'http://127.0.0.1:8000/datasets?build=true&step=30'
```

Validate either file: `python3 synthetic/check.py` (picks the newest by mtime).
