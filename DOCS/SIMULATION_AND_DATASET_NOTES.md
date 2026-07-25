# Simulation, Dataset Generation, Fault Injection — Notes

> Q&A writeup: how the sim works, how the dataset is generated, the fault-injection
> technique, and the plan for adding a temperature + power-consumption feature.

## Simulation architecture — two layers

**Layer A — live lab (148 containers).** `topology-spec.yaml` → `generator/generate.py` emits `clab.yml` + per-node FRR/SNMP/WireGuard configs + `topology-meta.json` (POP/SRLG/ABR map). Deployed via containerlab: 24 P routers (6 POPs × 4, multi-area OSPF+LDP), 12 PE (VPNv4 BGP, RR pe1/pe2), 34 CE (per-VRF eBGP, WireGuard overlay), 78 host containers. Real packets flow — pings, BGP sessions, WireGuard handshakes, all real.

**Layer B — synthetic generator, no lab needed.** `synthetic/generate.py` produces a Parquet in the identical 21-col schema, `pd.concat`-compatible with real data. Used for scale (8.89M rows / 7 days) that a live lab can't produce in reasonable time.

## How telemetry values get their numbers (live lab)

Not fully synthetic — layered:
1. **Real measurement:** `controller/controller.py:119-148` `docker exec`s into each CE spoke, pings the peer's WireGuard IP over `wg0` every ~45s, caches avg/jitter/loss. This is TRUE RTT over the netem-impaired `eth0` (`site_netem()` in `generator/generate.py`, branch~41ms/hub~17ms/dc~12ms, SPEC-NOTES.md:336-354).
2. **Modelled congestion on top:** `controller.py:183-276` `TunnelState.update()` — diurnal-driven M/M/1 queue term (`queue_ms = queue_mult * 9 * rho/(1-rho)`), AR(1) jitter random walk, micro-burst loss, WireGuard rekey clustering under stress.
3. **Fault readback:** faults inject netem on `eth1` (uplink); controller reads it back (`_read_netem`, controller.py:150-181) and folds into the emitted metric — so the lab's own fault stays visible even though the ping (on `wg0`) doesn't traverse it.
4. **Core (P/PE) control-plane telemetry:** no SNMP path for OSPF/LDP/BGP-VPNv4 state (AgentX mismatch), so `telemetry/telegraf/ldp-metrics.sh` shells into each node via `vtysh -c "... json"` and emits `ospf_neighbor_state`, `mpls_lsp_count`, `bgp_peer_established`, etc. straight to VictoriaMetrics remote-write.

All this lands in VictoriaMetrics (SNMP+Prometheus scrape via Telegraf), Loki (syslog/BGP events via Promtail), nfacctd (NetFlow). `dataapi/export.py` joins them + left-joins the fault label timeline on `device` + `ts ∈ [t_start,t_end]` → labeled Parquet.

## Fault injection

Two-file split, `faults/injectors.py` (primitives) + `faults/orchestrator.py` (scenario builders + scheduler):

- **Primitives** (`injectors.py`): `NetemImpair` (tc netem delay/jitter/loss/rate, ramps in 6 steps for congestion buildup), `LinkFlap`, `BgpFlap` (vtysh `clear bgp`, auto-discovers VRF instances), `ProcessKill` (kill -9 bgpd, watchfrr respawns), `WgRekeyAnomaly`, `PolicyDrift` (route-map local-pref), `MplsUnderlayFailure`, `LdpSessionFlap`, `MultiLinkFault` (atomic multi-link down — powers `p_node_failure`/`pop_isolation`/`srlg_cut`/`core_partition`), `OspfCostShift` (asymmetric routing).
- **21 scenario builders** in `orchestrator.py` wire injector + severity scaling + impact probe. Link-sets for core faults are resolved at runtime from `topology-meta.json` — nothing hardcoded.
- **`t_impact` derivation** (orchestrator.py:10-19, 565-587): either poll VictoriaMetrics for a real PromQL threshold crossing (`impact_method="vm_threshold"`) or a fixed modelled delay after `t_start` when no clean metric exists (BGP flap, node kill).
- **Campaign scheduler** (`run_campaign`, orchestrator.py:823-916): Poisson arrivals (`expovariate(1/mean_gap)`), one thread per active fault so concurrent faults on different targets run for real, active-target lock prevents double-faulting a device, SIGINT-safe revert via `finally`. This is what produced the 8.89M-row set.
- Every run writes a JSONL row to `faults/labels/labels.jsonl` — the ground truth.

Synthetic side mirrors this without a lab: `synthetic/calibrate.py` derives per-fault-type peak values + lead_time from a real capture into `profile.json`; `synthetic/generate.py:_inject_faults()` (120-295) vectorized-numpy overlays episodes with the same `t_start/t_impact/t_end` + ramp semantics, plus a 12% cascade chance (second fault on a different device mid-episode).

## Adding temperature + power_consumption

These containers are Linux netns, not real hardware — no ambient sensor exists. Has to be modelled, correlated to load/faults, same pattern as `controller.py`'s tunnel model. Plan:

**Formula (per device):**
- `temp_c = ambient_baseline(site_type) + k_load * utilization + k_fault * fault_heat + AR(1) noise`
  - `utilization` = same diurnal/congestion signal already driving queue_ms (`diurnal.util()`, reused, not reinvented)
  - `fault_heat`: `core_congestion`/`brownout`/`p_node_failure` spike load on remaining links → temp rises; a plain `node_failure` (bgpd killed) drops load → temp falls toward ambient
- `power_watts = idle_watts(role) + k_p * utilization + k_p_fault * fault_load_delta`
  - P/PE draw more idle watts than CE (bigger role → bigger baseline, like `SITE_QUEUE_MULT` tiers already do)

**Where it lives — two options:**

| Option | Fit |
|---|---|
| Extend `controller.py`'s `TunnelState`/`Controller` (CE/hub tunnels only) | Wrong scope — P/PE have no controller instance |
| New sidecar script, same shape as `ldp-metrics.sh` (or a tiny Python exporter), one `device_temp_c{device}` / `device_power_watts{device}` gauge per node, pushed to VM `POST /api/v1/import/prometheus` | Right scope — covers P+PE+CE uniformly, matches existing `noc-ldp-metrics` pattern, no protocol reinvented |

Second option wins — reuse the exact pipeline already proven for `ospf_neighbor_state` etc. New Prometheus metric names: `device_temp_c{device,site_type}`, `device_power_watts{device,site_type}`. Then:
- `dataapi/export.py`: add both to a new `_ENV_METRICS` map (mirrors `_IF_METRICS`/`_TUN_METRICS`, export.py:42-54), 2 new canonical columns.
- `synthetic/calibrate.py`: add `_env_baseline()` deriving per-site_type temp/power mean+std from the real capture (same `_src: real|default` fallback pattern as `_tunnel_baseline`).
- `synthetic/generate.py`: add a `_gen_env()` row-builder alongside `_gen_interfaces`/`_gen_tunnels`, and extend `_inject_faults` with a new `kind="env_spike"` so fault episodes also perturb temp/power (reuses the existing vectorized ramp/decay math, just two more arrays).

Not implemented yet — this is a design plan pending approval before the code lands.
