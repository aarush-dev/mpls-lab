# Faults — injection & ground-truth labels

## Purpose

Injects realistic faults into the live `sdwan_mpls_noc` Containerlab topology (via `docker exec`/`tc`/`vtysh`/`ip link`/`containerlab tools netem`) and writes a ground-truth label timeline (`faults/labels/labels.jsonl`) that joins to telemetry (VictoriaMetrics, Loki, controller `:9362`) on `device` + time. Sits downstream of the topology generator (consumes `topology/topology-meta.json`) and upstream of the ML/copilot training pipeline (labels are the supervised-learning target; `signatures.py` is shared byte-for-byte with `synthetic/calibrate.py`+`synthetic/generate.py` so live and synthetic data can't diverge). `faults/orchestrator.py:1-26`.

## Entry points

CLI only, `faults/orchestrator.py` `main()` (`faults/orchestrator.py:1233-1281`):

```bash
cd faults
python3 orchestrator.py --list                                              # list scenarios
python3 orchestrator.py --demo                                              # ~60s congestion demo on ce_branch1
python3 orchestrator.py --scenario congestion --target ce_branch1 --severity high --duration 90
python3 orchestrator.py --scenario congestion --target ce_branch1 --dry-run  # label only, lab untouched
python3 orchestrator.py --campaign --duration 3600 --mean-gap 120 --seed 1   # Poisson-arrival campaign
python3 orchestrator.py --selftest                                          # lock rule + ramp draw, no lab needed
```

Scenarios that import injector classes needing the `faults` package (`mpls_underlay_failure`, `ldp_session_flap`, `hub_spoke_congest`, `bgp_cascade`) need `PYTHONPATH=/root/LAB` or must run from repo root (`faults/README.md:35-42`).

Self-test blocks (not pytest, run directly):
- `python3 injectors.py` — pure command-construction asserts, touches nothing live (`faults/injectors.py:571-656`).
- `python3 signatures.py` — none (no `__main__`; golden-tested by `test_signatures.py`).
- `python3 leadpriors.py` — distribution-shape asserts (`faults/leadpriors.py:125-150`).

Tests (pytest, no lab needed — mocked at seams):
```bash
cd faults && python3 -m pytest test_signatures.py test_events_push.py -v
```
`test_signatures.py` is a golden test: `signatures.py` output must equal a verbatim pre-refactor copy of the generator's closures. `test_events_push.py` asserts pushed precursor events match `synthetic/events.py`'s impact-anchored template set, drop recovery events, and land at buildup-start.

## Modules

- **`injectors.py`** — apply()/revert() primitives, one class per fault mechanism, all `docker exec` + native tools, stdlib only (`faults/injectors.py:1-29`).
  - `NetemImpair` — delay/jitter/loss/rate via `tc netem`; auto-detects HTB vs noqueue root and splices as an HTB leaf on CE uplinks (`faults/injectors.py:55-205`). `ramp()` steps 0→target over N increments (`faults/injectors.py:146-179`).
  - `LinkFlap` — `ip link set <if> down`, sleep, `up`, repeat `count` times (`faults/injectors.py:211-234`).
  - `BgpFlap` — `vtysh clear bgp *` + per-discovered-VRF clears (`faults/injectors.py:240-292`).
  - `ProcessKill` — `kill -9 $(pidof <proc>)`; revert polls for watchfrr respawn, force-restarts after 60s (`faults/injectors.py:298-327`).
  - `WgRekeyAnomaly` — bounces `wg0` N times to force handshake churn (`faults/injectors.py:333-357`).
  - `PolicyDrift` — pushes/removes a CE VRF route-map lowering local-preference; raises `RuntimeError` if peer/ASN undetected (`faults/injectors.py:364-449`).
  - `MplsUnderlayFailure` — `ip link set <iface> down/up` on a P-router core iface (`faults/injectors.py:455-472`).
  - `LdpSessionFlap` — `vtysh clear mpls ldp neighbor <ip>` N times (`faults/injectors.py:478-502`).
  - `MultiLinkFault` — downs/ups a SET of `(device, iface)` links, staggered (`faults/injectors.py:508-536`).
  - `OspfCostShift` — `vtysh` `ip ospf cost <N>` on one direction; revert restores `orig_cost` or `no ip ospf cost` (`faults/injectors.py:542-568`).

- **`orchestrator.py`** — scenario builders, state machine, campaign driver, label writer (`faults/orchestrator.py:1-26`).
  - `SCENARIOS` dict: 21 `scen_*` builder functions, each returns a spec dict (`faults/orchestrator.py:594-617`).
  - `run_scenario()` — buildup→impact→hold→revert state machine for one live injection, writes one label row (`faults/orchestrator.py:716-867`).
  - `run_campaign()` — Poisson-arrival multi-fault driver, one thread per active fault (`faults/orchestrator.py:1132-1229`).
  - `_resolve_impact()` — derives `t_impact`/`impact_method` (`faults/orchestrator.py:646-683`).
  - `_label_row()` — builds the one label schema shared by both run paths (`faults/orchestrator.py:686-713`).
  - `poll_threshold()` / `vm_instant()` — VictoriaMetrics PromQL polling (`faults/orchestrator.py:62-104`).
  - `meta()` / `_p_inter_ifaces()` / `_backbone_iface()` / `_pe_primary_p_loopback()` — reads `topology/topology-meta.json`, derives link-sets/interfaces for core scenarios (`faults/orchestrator.py:121-153`).
  - `draw_ramp_seconds()` — draws ramp wall-duration from `leadpriors`, capped at 0.7×duration (`faults/orchestrator.py:632-643`).
  - `_lock_key()` / `_campaign_fault()` — resource-scoped concurrency lock for the campaign (`faults/orchestrator.py:990-1129`).

- **`signatures.py`** — shared fault→signature table + ramp math; pure numpy, no I/O; imported by both `synthetic/` (dataset) and `events_push.py` (live) (`faults/signatures.py:1-19`).
  - `default_signatures(base_lat, base_loss, base_jit)` — per-fault-type `{lat_peak, loss_peak, jit_peak, lead_s, kind}` (`faults/signatures.py:23-52`).
  - `prog(ep, t_start, t_impact, t_end, dur, sevmul, step, p_cross=1.0)` — piecewise-linear impairment fraction (`faults/signatures.py:55-69`).
  - `tunnel_ramp_targets(sig, lat, jit)` — floored (lat, jit) ramp targets (`faults/signatures.py:72-83`).

- **`leadpriors.py`** — per-fault-type precursor lead priors (lognormal), shared by `synthetic/generate.py` and `orchestrator.py` so both draw from the identical distribution (`faults/leadpriors.py:1-22`).
  - `LEAD_BUCKETS` — (lo, hi) 10th/90th percentile bucket range per fault type (`faults/leadpriors.py:26-55`).
  - `THETA_SLA` — per-VRF SLA objectives (`faults/leadpriors.py:64-68`).
  - `strictest_sla(vrfs)` / `sla_binding_vrf(vrfs)` — tightest SLA across a tunnel's VRFs (`faults/leadpriors.py:74-87`).
  - `lognormal_params(ft)` — (mu, sigma) of the lognormal pinned to the prior's p10/p90 (`faults/leadpriors.py:95-107`).
  - `draw_lead_s(ft, step, normal_draw)` — draws lead in seconds for one episode (`faults/leadpriors.py:110-122`).

- **`events_push.py`** — pushes live FRR control-plane precursor events into Loki during buildup, reusing `synthetic/events.py` unchanged (`faults/events_push.py:1-23`).
  - `build_events(...)` — builds impact-anchored precursor events, drops recovery events (`faults/events_push.py:49-75`).
  - `push_events(events_list)` — POSTs to Loki's `/loki/api/v1/push` (`faults/events_push.py:78-96`).
  - `emit_precursors(...)` — full path: resolve `kind` from `signatures.py`, build events, push; never raises (`faults/events_push.py:99-124`).

## Parameters

| name | default | env-var/CLI-flag | units | what it controls | source (file:line) |
|---|---|---|---|---|---|
| `LAB` | `"sdwan_mpls_noc"` | — | string | Containerlab lab name prefix for `docker exec` node names | `faults/injectors.py:34` |
| `VM_URL` | `http://172.20.20.50:8428` | env `VM_URL` | URL | VictoriaMetrics query endpoint for `t_impact` probing | `faults/orchestrator.py:47` |
| `LOKI_URL` | `http://172.20.20.54:3100` | env `LOKI_URL` | URL | Loki push endpoint for precursor events | `faults/events_push.py:31` |
| `CTRL_URL` | `http://172.20.20.56:9362` | — | URL | SD-WAN controller HTTP (overlay + drift injectors) | `faults/orchestrator.py:371` |
| `--scenario` | none (required unless `--demo`/`--list`/`--campaign`) | CLI `--scenario` | enum(21) | which scenario to run | `faults/orchestrator.py:1235` |
| `--target` | none (required) | CLI `--target` | device name | node/POP/SRLG the scenario targets | `faults/orchestrator.py:1236` |
| `--severity` | `"medium"` | CLI `--severity` | enum(low/medium/high) | impairment magnitude multiplier | `faults/orchestrator.py:1237` |
| `--duration` | `90` | CLI `--duration` | seconds | single-scenario hold duration, or campaign total duration | `faults/orchestrator.py:1238-1239` |
| `--ramp-steps` | `6` | CLI `--ramp-steps` | count | `NetemImpair.ramp()` step count for campaign netem faults | `faults/orchestrator.py:1240` |
| `--mean-gap` | `120` | CLI `--mean-gap` | seconds | campaign Poisson inter-arrival mean | `faults/orchestrator.py:1251-1252` |
| `--seed` | `None` | CLI `--seed` | int | campaign RNG seed | `faults/orchestrator.py:1253-1254` |
| `SEVERITY` | `{low:0.4, medium:0.7, high:1.0}` | — | multiplier | maps severity string → impairment scale `s` used throughout `scen_*` builders | `faults/orchestrator.py:161` |
| `poll_threshold` `timeout_s` | `120` (default) / `int(duration)` (as called) | — | seconds | how long to poll VM before giving up on a threshold crossing | `faults/orchestrator.py:83,672` |
| `poll_threshold` `interval_s` | `3` | — | seconds | VM poll interval | `faults/orchestrator.py:83,672` |
| congestion delay/jitter/loss | `80*s` ms / `20*s` ms / `6*s` % | — | ms, % | CE-uplink netem ramp targets for `congestion` scenario | `faults/orchestrator.py:176` |
| congestion probe threshold | `8.0` | — | ms (delta over baseline) | `sdwan_tunnel_latency_ms` crossing that marks `t_impact` | `faults/orchestrator.py:182` |
| tunnel_degrade delay/jitter/loss | `30*s`/`40*s`/`10*s` | — | ms, % | CE-uplink netem targets for `tunnel_degrade` | `faults/orchestrator.py:209` |
| tunnel_degrade probe threshold | `2.0` | — | % (delta) | `sdwan_tunnel_loss_pct` crossing | `faults/orchestrator.py:217` |
| policy_drift local-pref | `100 - 60*s` | — | BGP local-pref | drift magnitude; higher severity → lower local-pref | `faults/orchestrator.py:227` |
| asymmetric_loss loss | `12*s` | — | % | egress-only loss magnitude | `faults/orchestrator.py:259` |
| asymmetric_loss probe threshold | `2.0` | — | % | `sdwan_tunnel_loss_pct` crossing | `faults/orchestrator.py:265` |
| brownout rate | `int(2000*(1.1-s))` | — | kbit | uplink rate cap; higher severity → tighter cap | `faults/orchestrator.py:276` |
| brownout delay/loss | `15*s` ms / `1.5*s` % | — | ms, % | paired impairment so the cap is observable in tunnel telemetry | `faults/orchestrator.py:282` |
| bgp_flap count | `max(2, int(4*s))` | — | count | number of `clear bgp` cycles | `faults/orchestrator.py:191` |
| bgp_cascade count | `{low:1, medium:3, high:5}` | — | count | number of `clear bgp` cycles on hub CE | `faults/orchestrator.py:354` |
| ldp_session_flap count | `{low:1, medium:2, high:3}` | — | count | number of LDP neighbor clears | `faults/orchestrator.py:316` |
| ospf_area_flap count | `{low:1, medium:2, high:3}` | — | count | number of link flaps | `faults/orchestrator.py:534` |
| hub_spoke_congest delay/jitter/loss | low `20/4/0.5`, medium `80/15/2.0`, high `200/40/8.0` | — | ms, ms, % | severity table for spoke-uplink congestion | `faults/orchestrator.py:337-340` |
| controller_drift mult | `{low:5.0, medium:10.0, high:99.0}` | — | multiplier | `latency_threshold_mult` posted to controller `/fault/drift` | `faults/orchestrator.py:429` |
| path_asymmetry cost | `int(500 + 1500*s)` | — | OSPF cost | one-way cost hike | `faults/orchestrator.py:549` |
| gray_failure loss | `round(0.5 + 1.5*s, 2)` | — | % | sub-BFD loss, capped below BFD trip | `faults/orchestrator.py:583` |
| core_congestion delay/jitter/loss | `60*s`/`15*s`/`4*s` | — | ms, % | P-P backbone link netem ramp | `faults/orchestrator.py:519` |
| `_DURATION_BOUNDS` | per-type `(lo,hi)` seconds, e.g. `congestion:(30,90)`, `gray_failure/controller_drift:(60,180)` | — | seconds | campaign fault hold-duration sampling range (`rng.uniform(lo,hi)`) | `faults/orchestrator.py:945-967` |
| `CAMPAIGN_POOLS` | per-type list of valid targets | — | — | which devices/POPs/SRLGs the campaign may pick for each scenario | `faults/orchestrator.py:910-943` |
| `_EXCLUSIVE` | `{node_failure, rr_failure, bgp_cascade}` | — | — | scenarios that lock the WHOLE device (ProcessKill removes the routing daemon) | `faults/orchestrator.py:987` |
| `draw_ramp_seconds` cap | `0.7 * duration` | — | fraction | ramp cannot outlast the fault | `faults/orchestrator.py:642` |
| `LEAD_BUCKETS` | per-type `(lo,hi)` buckets, e.g. `bgp_flap:(4,10)`, `congestion:(10,40)`, `gray_failure:(20,80)` | — | buckets (× `step`) | 10th/90th percentile precursor-lead prior per fault type | `faults/leadpriors.py:26-55` |
| `THETA_SLA` | VOICE `150ms/1%`, CORP `250ms/2%`, GUEST `400ms/5%` | — | ms, % | SLA objectives per VRF, used to place `t_impact` inside a ramp | `faults/leadpriors.py:64-68` |
| `DEFAULT_RANGE` | `(8, 30)` buckets | — | buckets | fallback lead prior for a fault type not in `LEAD_BUCKETS` | `faults/leadpriors.py:90` |
| `FLOOR_BUCKETS` | `4.0` | — | buckets | safety-net minimum lead (a shorter ramp has no signal) | `faults/leadpriors.py:91` |
| `_P_90_Z` | `1.2815515655446004` | — | z-score | standard-normal 90th-percentile constant used to fit lognormal σ | `faults/leadpriors.py:93` |
| lognormal lower-endpoint lift | `FLOOR_BUCKETS * 1.15` | — | buckets | prevents the safety net firing on ~10% of the 4–10 bucket group | `faults/leadpriors.py:104` |
| `signatures.py` peak table | see Calculations | — | ms/%/s | per-fault-type `lat_peak`/`loss_peak`/`jit_peak`/`lead_s`/`kind` | `faults/signatures.py:29-52` |
| `prog()` mid-knot offset | `0.3 * max(dur, step)` | — | seconds | time after `t_impact` at which impairment reaches full peak (1.0) | `faults/signatures.py:67` |
| `tunnel_ramp_targets` floor | `1.15x` healthy value | — | multiplier | latency/jitter ramp target floor (loss is unfloored additive) | `faults/signatures.py:82` |
| campaign `gap` floor | `max(5.0, expovariate(1/mean_gap))` | — | seconds | minimum inter-arrival gap between campaign faults | `faults/orchestrator.py:1167` |
| `NetemImpair.ramp` default | `steps=6, step_seconds=10.0` | — | count, seconds | default ramp shape when `total_seconds` not given | `faults/injectors.py:146` |
| `LinkFlap` default | `down_seconds=10.0, count=1` | — | seconds, count | default flap timing | `faults/injectors.py:215` |
| `BgpFlap` default | `count=1, gap_seconds=8.0` | — | count, seconds | default flap timing | `faults/injectors.py:255` |
| `WgRekeyAnomaly` default | `count=3, gap_seconds=4.0` | — | count, seconds | default rekey bounce timing | `faults/injectors.py:339` |
| `ProcessKill` revert poll | `12 × 5.0s` (60s) then forced restart | — | seconds | watchfrr respawn wait before forcing `watchfrr.sh restart` | `faults/injectors.py:319-325` |

## Data flow

1. **Topology meta** (`topology/topology-meta.json`, produced by `generator/generate.py`, outside this subsystem) → read once and cached in `_META` by `meta()` (`faults/orchestrator.py:118-128`). Feeds `p_core_ifaces`, `abrs`, `pops`, `inter_pop_links`, `pop_inter_links`, `srlgs`, `pe_pop` into the 9 core/catastrophic scenario builders (`scen_p_node_failure`, `scen_pop_isolation`, `scen_core_partition`, `scen_srlg_cut`, `scen_core_congestion`, `scen_ospf_area_flap`, `scen_path_asymmetry`, `scen_mpls_underlay_failure`, `scen_ldp_session_flap`) and into `CAMPAIGN_POOLS` (`faults/orchestrator.py:901-943`).
2. **CLI args / campaign RNG** → `SCENARIOS[name](target, severity, duration)` builds a spec dict `{type, target, injector, ramp?, duration, probe?, threshold?, impact_method, signature, ...}` (`faults/orchestrator.py:157-617`).
3. **Injector `apply()`** → `docker exec` into the container node (`injectors.py:node()`/`dexec()`) → mutates live `tc`/`ip link`/`vtysh`/`kill` state (`faults/injectors.py:37-49`). For overlay-flagged scenarios, `_OverlayInjector.apply()` also POSTs to controller `/fault/overlay` (`faults/orchestrator.py:374-398`); for `controller_drift`, `_DriftInjector.apply()` POSTs to `/fault/drift` (`faults/orchestrator.py:401-420`).
4. **Precursor events** → `events_push.emit_precursors()` looks up `signatures.default_signatures()[fault_type]["kind"]`, calls `synthetic/events.py` `_spec/_params/_event_id` (external module, reused unchanged) to build impact-anchored FRR log lines, pushes to Loki `/loki/api/v1/push` (`faults/events_push.py:99-124`, `faults/orchestrator.py:803-814`).
5. **Impact detection** → `poll_threshold()` polls `VM_URL` PromQL every `interval_s` until the probe crosses `threshold` relative to `baseline`, or the ramp-derived timestamp is used if no probe (`faults/orchestrator.py:83-104,646-683`).
6. **Label row** → `_label_row()` assembles the schema dict; `write_label()` appends one JSON line to `faults/labels/labels.jsonl` (`faults/orchestrator.py:108-112,686-713`). This is the terminal output — consumed downstream by the ML/copilot training pipeline (join key = `device` + `[t_start,t_end]`).
7. **Revert** (always, in `finally`) → injector `revert()` restores live state; overlay/drift injectors clear their controller-side POST (`faults/orchestrator.py:834-857`).

## Calculations

**Severity multiplier `s`**: `SEVERITY = {"low": 0.4, "medium": 0.7, "high": 1.0}` (`faults/orchestrator.py:161`). Most `scen_*` builders scale their netem/cost/count parameters linearly by `s` (e.g. `delay_ms = 80 * s` for `congestion`, `faults/orchestrator.py:176`).

**Ramp lead draw** (`draw_ramp_seconds`, `faults/orchestrator.py:632-643`):
```
lead, _ = leadpriors.draw_lead_s(name, step=30, normal_draw=random.gauss(0,1))
cap = 0.7 * duration
ramp_s = min(lead, cap)
capped = lead > cap
```
Feeds `NetemImpair.ramp(steps, total_seconds=ramp_s)` in campaign mode.

**Lognormal lead prior** (`leadpriors.lognormal_params`, `faults/leadpriors.py:95-107`):
```
lo, hi = LEAD_BUCKETS[fault_type]              # p10, p90 in buckets
lo = max(lo, FLOOR_BUCKETS * 1.15)             # 4.0*1.15 = 4.6
mu    = (ln(lo) + ln(hi)) / 2
sigma = (ln(hi) - ln(lo)) / (2 * 1.2815515655446004)   # standard-normal p90 z
```
`draw_lead_s(ft, step, normal_draw)` (`faults/leadpriors.py:110-122`):
```
buckets = exp(mu + sigma * normal_draw)
floored = buckets < FLOOR_BUCKETS               # 4.0
lead_s  = max(buckets, FLOOR_BUCKETS) * step
```
This is a lognormal parameterized so its 10th/90th percentiles land exactly on the `LEAD_BUCKETS` endpoints (in bucket units), then scaled to seconds by `step`. Both `synthetic/generate.py` (dataset) and `orchestrator.py` (live) call this same function so lead distributions cannot drift apart.

**`t_impact` derivation** (`_resolve_impact`, `faults/orchestrator.py:646-683`), five methods:
- `vm_threshold` — `poll_threshold()` returns a crossing timestamp; used directly.
- `ramp_derived` — no crossing (or no probe), but the scenario ramped: `t_impact = t_start + ramp_s` (`t_ramp`), i.e. the point the ramp reaches the calibrated peak / SLA-defined level.
- `modelled_fallback` — probe was read, never crossed within `duration`: `t_impact = t_start + spec["impact_delay_s"]` (default `2`, `faults/orchestrator.py:681`).
- `probe_unavailable` — probe returned no data for the whole poll window: same `impact_delay_s` fallback, but flagged as unsupported by telemetry.
- `modelled` — scenario declares no probe and does not ramp: `t_impact = t_start + impact_delay_s`.
- `overlay_lead` — used only by `run_scenario()` (single-scenario CLI path), not campaigns: for overlay-flagged scenarios, `t_impact = t_start + lead` where `lead = duration` directly (the UI/CLI duration knob controls buildup span) (`faults/orchestrator.py:754-759`).

**`lead_time`** (label field): `round((t_impact - t_start).total_seconds(), 1)` (`faults/orchestrator.py:701`).

**`prog()` impairment fraction** (`faults/signatures.py:55-69`) — piecewise-linear interpolation through 4 knots:
```
knots_t = [t_start, t_impact, t_impact + 0.3*max(dur,step), t_end]
knots_p = [0.0,     p_cross,  1.0,                            0.0]
frac(ep) = clip(interp(ep, knots_t, knots_p), 0, 1) * sevmul
```
`p_cross` = impairment fraction at the moment the SLA threshold is crossed (1.0 if the signature never breaches SLA, i.e. plain ramp-then-decay). `sevmul` scales the whole curve by severity.

**`tunnel_ramp_targets()`** (`faults/signatures.py:72-82`):
```
lat_target = max(sig["lat_peak"], lat * 1.15)
jit_target = max(sig["jit_peak"], jit * 1.15)
```
Floored at 1.15× the current healthy value (some calibrated peaks sit below the healthy diurnal mean, so ramping straight at the raw peak could ramp downward). Loss is not floored: caller adds `sig["loss_peak"]` as an absolute bump.

**Campaign `_merged_seconds`** (`faults/orchestrator.py:970-981`) — union of overlapping `[t_start, t_end]` intervals (sorted-sweep merge), used for `fault_seconds` in the campaign summary so concurrent faults are not double-counted.

**Campaign summary fields** (`faults/orchestrator.py:1214-1227`):
```
fault_seconds            = merged_seconds(intervals)                      # union
concurrent_fault_seconds = sum(b-a for a,b in intervals)                   # sum, can exceed fault_seconds
healthy_seconds          = max(0, total_duration - fault_seconds)
fault_pct                = 100 * min(fault_seconds, total_duration) / total_duration
```

**Campaign arrival model** (`faults/orchestrator.py:1132-1229`): Poisson process, inter-arrival `gap = max(5.0, rng.expovariate(1.0/mean_gap))` (`faults/orchestrator.py:1167`); `mean_gap=120` → ~1 fault/2min average.

## Config & schemas

**`faults/labels/labels.jsonl`** — line-oriented JSON, one object per scenario instance, all timestamps UTC ISO-8601 `Z`. Schema built by `_label_row()` (`faults/orchestrator.py:686-713`):

| field | type | producer | notes |
|---|---|---|---|
| `scenario_id` | string | `f"{name}-{target}-{uuid4().hex[:8]}"` | `faults/orchestrator.py:741,1057` |
| `type` | string | `spec["type"]` | == scenario name, enforced by `SCENARIO_TYPES` (`faults/orchestrator.py:620-628`) |
| `target` | object | `spec["target"]` | always has `device`; plus `interface`/`vrf`/`tunnel`/`neighbor`/`process`/`rate_kbit`/`links`/`n_links` as relevant |
| `severity` | string\|null | CLI/campaign draw, or `null` if `spec.get("severity_inert")` | link-set/process-kill faults ignore severity |
| `t_start` | ISO-8601 | `now_utc()` at injection | |
| `t_impact` | ISO-8601 | `_resolve_impact()` | see Calculations |
| `t_end` | ISO-8601 | `now_utc()` after revert | |
| `lead_time` | float (s) | `t_impact - t_start` | |
| `impact_method` | string | `vm_threshold\|ramp_derived\|modelled_fallback\|probe_unavailable\|modelled\|overlay_lead` | |
| `t_impact_ramp` | ISO-8601\|null | ramp-derived timestamp, recorded alongside `vm_threshold` when both exist | |
| `probe` | string\|null | PromQL query polled | |
| `baseline_value` | float\|null | probe value pre-injection | |
| `impact_value` | float\|null | probe value at crossing | |
| `signature` | string | `spec["signature"]` — human-readable expected telemetry pattern | |
| `device` | string | `spec.get("device", target)` — universal join key | |
| `dry_run` | bool | true if lab untouched | |
| `error` | string\|null | injector/revert exception text, or `"early_revert_before_impact"` | |
| `campaign_id` | string | campaign runs only, added post-hoc (`faults/orchestrator.py:1116`) | not present on single-scenario CLI rows |

**`topology/topology-meta.json`** (read, not written, by this subsystem) — fields consumed: `pop_count` (int), `abrs` (list[str]), `pops` (dict `pop\d`→list[P names]), `pe_pop` (dict PE→pop int), `p_core_ifaces` (dict P→list[iface]), `srlgs` (dict SRLG-name→list[[device,iface]]), `inter_pop_links` (list of `{pop_a, pop_b, kind, srlg, links:[[device,iface],...]}`), `pop_inter_links` (dict pop→list[[device,iface]]). Consumed at `faults/orchestrator.py:121-153,443-511`.

**Loki push payload** (`events_push.push_events`, `faults/events_push.py:78-96`) — POST `{LOKI_URL}/loki/api/v1/push`:
```json
{"streams": [{"stream": {labels}, "values": [["<ns_timestamp>", "<line>"]]}]}
```
`labels`: `job=fault-events, device, app=frr, event_type, template_id, severity?`. `line` (JSON string, dataset event row shape from `synthetic/events.py`): `event_id, ts (us), device, entity, event_type, severity, template_id, params (JSON string), scenario_id, topology_id`.

**Controller HTTP payloads** (`faults/orchestrator.py:374-420`):
- `POST /fault/overlay` `{"site", "fault_type", "lead_s", "duration", "severity"}`
- `POST /fault/overlay/clear` `{"site"}`
- `POST /fault/drift` `{"site", "latency_threshold_mult", "ttl_s"}`
- `POST /fault/drift/clear` `{"site"}`

## Gotchas

- **`total_seconds` on `ramp()` divides by `steps-1`**, not `steps` — `steps-1` sleeps happen (none after the final step). Passing `steps=1` divides by `max(1, 0)=1`, giving a single-jump ramp with no real ramp shape (`faults/injectors.py:159-162`).
- **CE-uplink netem is spliced as an HTB leaf, never a root install** — `containerlab tools netem set` fails outright on CE uplinks (they already have an HTB QoS root). The default class is read live off `tc qdisc show` (`_parse_htb`), never hardcoded, because each VRF's uplink default classid differs (`faults/injectors.py:15-28,76-100`).
- **`NetemImpair.revert()` order is load-bearing**: delete-our-handle THEN best-effort restore. A restore-only revert would `tc qdisc replace ... fq_codel` which silently fails (no `sch_fq_codel` module on these nodes) and strands netem permanently on the QoS default class (`faults/injectors.py:181-199`).
- **`PolicyDrift.apply()` raises `RuntimeError`** rather than silently pushing malformed vtysh config if peer/ASN detection fails — because `dexec`'s return code is never checked elsewhere, so a silent failure would still label the fault as "applied" (`faults/injectors.py:399-409`).
- **`ospf_area_flap`/`path_asymmetry`/`core_congestion` use `_backbone_iface()`** which picks an inter-POP link if the target is an ABR, else the target's *last* core iface (a P-PE link). `path_asymmetry`'s revert `orig_cost` must match this choice (100 for inter-POP, 10 for intra-POP) or reverting leaves a link permanently cost-inflated (`faults/orchestrator.py:139-143,550-554`).
- **`scen_mpls_underlay_failure` rejects ABR targets** with `SystemExit` — an ABR's last core iface is an inter-POP backbone link, not a P-PE link, so `[-1]` would mislabel the failure domain (`faults/orchestrator.py:298-302`).
- **`_lock_key()` locks on the RESOURCE mutated, not the bare device** — `(device, interface|tunnel|vrf|neighbor|process)`. Two netem installs on one interface exclude each other; a VRF policy-drift and an interface impairment on the same box do not. `node_failure`/`rr_failure`/`bgp_cascade` are whole-device-exclusive because `ProcessKill` removes the routing daemon `vtysh` needs (`faults/orchestrator.py:984-1006`).
- **`pop_isolation` and `core_partition` are excluded from `CAMPAIGN_POOLS`** (`faults/orchestrator.py:932-935`) — their link-sets can overlap with other core faults; run them only as named single scenarios.
- **`_campaign_fault` releases `active_targets` in an outer `finally`**, independent of the inner try/finally that reverts and writes the label — a builder exception before the injector is created still releases the lock, or the target would be permanently unavailable for the rest of the campaign (`faults/orchestrator.py:1032-1129`).
- **`brownout`'s rate cap is paired with a small delay+loss** because a pure rate cap has no `delay`/`loss` token for the controller's `_read_netem()` to parse, and wg0 RTT doesn't traverse `eth1` — an unpaired rate cap would be a real impairment invisible to telemetry (`faults/orchestrator.py:276-282`).
- **`bgp_cascade` has no probe** — `sdwan_path_changes_total` is an unlabelled, RNG-driven counter the controller increments itself; a crossing cannot be attributed to this fault, so it stays `modelled` (`faults/orchestrator.py:356-360`).
- **`hub_spoke_congest` injects on the hub's own `eth1`**, not a spoke, because a spoke peers every hub — capping one hub-facing link would be invisible; the controller folds netem per spoke site (`faults/orchestrator.py:330-335`).
- **`overlay_lead`'s `lead = duration` directly** (not drawn from `leadpriors`) in `run_scenario()` — a prior comment notes the earlier `[30,60]s` prior-drawn lead pinned every live buildup to ≤60s regardless of the chosen `--duration`; this only affects the single-scenario CLI path, campaigns still use `draw_ramp_seconds` (`faults/orchestrator.py:749-759`).
- **Early-revert before impact still writes a label row** with `t_impact = t_start + lead` and `error="early_revert_before_impact"` — a consumer must check `error` before trusting `t_impact`/`impact_method` on cancelled runs (`faults/orchestrator.py:854-864`).
- **`events_push.emit_precursors` imports `synthetic/events.py` lazily** (pulls pandas/pyarrow) so the stdlib-only injector path never hard-depends on it; any import failure silently no-ops and returns 0 (`faults/events_push.py:36-46`).
- **`_lock_selftest`/`--selftest` and the `injectors.py`/`leadpriors.py` `__main__` blocks require no live lab** — use them to validate logic changes before touching `docker exec`.
