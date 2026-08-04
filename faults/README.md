# `faults/` — Fault injection + ground-truth labels

The **ML signal** for the air-gapped predictive NOC copilot. This subsystem
injects realistic, problem-statement-shaped faults into the **live**
`sdwan_mpls_noc` Containerlab topology and writes a **ground-truth label
timeline** that joins to the telemetry (metrics in VictoriaMetrics, flows in
nfacctd, logs in Loki, controller on `:9362`) on `device` + time.

Caveman+ponytail: reuse native tools (`containerlab tools netem`, `tc`,
`ip link`, `vtysh`, `kill`), stdlib Python only, nothing reinvented. But the
**label timeline is the ground truth**, so its correctness is treated carefully.

```
faults/
  injectors.py      # injection primitives (apply / revert), one class each
  orchestrator.py   # schedules scenarios, derives t_impact, writes labels
  signatures.py     # shared fault->signature table + ramp math (generator + live)
  events_push.py    # buildup: push the fault's FRR control-plane events into Loki (#65)
  labels/labels.jsonl   # the label timeline (one JSON object per line)
  README.md         # this file — the label-schema contract
```

## Quick start

```bash
cd faults
python3 orchestrator.py --list                       # list scenarios
python3 orchestrator.py --demo                        # ~60s end-to-end congestion demo
python3 orchestrator.py --scenario congestion --target ce_branch1 --severity high --duration 90
python3 orchestrator.py --scenario bgp_flap     --target pe1        --severity medium
python3 orchestrator.py --scenario policy_drift --target ce_branch1 --duration 60
python3 orchestrator.py --scenario congestion --target ce_branch1 --dry-run   # label only, lab untouched
```

> **PYTHONPATH note:** scenarios that import injector classes from the `faults`
> package (e.g. `mpls_underlay_failure`, `ldp_session_flap`, `hub_spoke_congest`,
> `bgp_cascade`) must be run with `PYTHONPATH=/root/LAB` set, or from inside the
> repo root where `faults/` is a package on `sys.path`.
>
> ```bash
> PYTHONPATH=/root/LAB python3 orchestrator.py --scenario mpls_underlay_failure --target p3
> ```

`--target` is a **device name** (node): `p1..p24`, `pe1..pe12`,
`ce_branch1..4`, `ce_hub1..2`, `ce_dc1..2`; for POP-scoped faults use
`pop1..pop6`; for SRLG faults use e.g. `srlg_pop1_2`. Severity ∈ `low|medium|high`
(scales impairment magnitude). `--duration` is total seconds.

---

## Label schema (the contract for the data-API + ML)

Labels are **line-oriented JSON** (`labels/labels.jsonl`), one object per
scenario instance. All timestamps are **UTC ISO-8601** (`...Z`). Join to
telemetry on `device` and the `[t_start, t_end]` window; `t_impact` marks when
the effect became observable, and `lead_time` is the precursor window the model
must predict within.

For **ramping** scenarios the ramp's wall duration is the lead drawn from
`leadpriors.py` (`orchestrator.draw_ramp_seconds` →
`NetemImpair.ramp(total_seconds=)`), so the impairment slope carries the lead
instead of every fault ramping over a fixed ~2 min. The draw is capped at
0.7 × `--duration` so a ramp cannot outlast its own fault — at the default 90 s
that cap binds on nearly every draw, so use a much longer `--duration` to see the
untruncated prior.

**Campaign concurrency:** the active-target lock keys on the RESOURCE a scenario
mutates — `(device, interface|tunnel|vrf|neighbor|process)` — not the bare device,
so a VRF policy drift and an interface impairment run together on one box while two
netem installs on one interface still exclude each other. `node_failure`,
`rr_failure` and `bgp_cascade` are device-exclusive: ProcessKill removes the routing
daemon, so anything needing `vtysh` on that box would be labelled for a fault it
never really injected. `python3 orchestrator.py --selftest` checks this rule and the
ramp draw without a lab.

| field            | type        | meaning |
|------------------|-------------|---------|
| `scenario_id`    | string      | unique id `<type>-<target>-<hex8>` |
| `type`           | string      | scenario type (see table below) |
| `target`         | object      | what was hit: always a dict with a `device` key, plus `interface`/`vrf`/`tunnel`/`neighbor`/`process`/`rate_kbit` as relevant |
| `severity`       | string/null | `low` \| `medium` \| `high`; `null` for scenarios whose injector ignores severity (`severity_inert: True` in `orchestrator.py` — link-set/process-kill faults) |
| `t_start`        | ISO-8601 Z  | injection moment |
| `t_impact`       | ISO-8601 Z  | first moment the effect is observable in telemetry |
| `t_end`          | ISO-8601 Z  | fault cleared / reverted |
| `lead_time`      | float (s)   | `t_impact - t_start` — the precursor lead window |
| `impact_method`  | string      | how `t_impact` was derived (see below) |
| `t_impact_ramp`  | ISO-8601 Z / null | the ramp-derived `t_impact` whenever a ramp ran, recorded even when `impact_method` is `vm_threshold`, so the two estimates can be compared |
| `probe`          | string/null | PromQL query polled to detect impact (null if modelled) |
| `baseline_value` | float/null  | probe value just before injection |
| `impact_value`   | float/null  | probe value at threshold crossing |
| `signature`      | string      | human-readable expected telemetry signature |
| `device`         | string      | universal join key = node name (mirrors `target.device`) |
| `dry_run`        | bool        | true if this row was a label-only run (lab untouched) |
| `error`          | string/null | injector/revert exception text if the scenario failed; row is still written |

### How `t_impact` is derived (documented method)

- **`vm_threshold`** — the orchestrator polls a VictoriaMetrics PromQL `probe`
  every 3 s and records the **first threshold crossing** (relative to the
  pre-injection `baseline_value`) as `t_impact`. Used wherever a metric directly
  reflects the fault (congestion, tunnel degrade, asymmetric loss, brownout).
  This is the *same* metric the AI team consumes, so the label aligns with what
  the model sees.
- **`modelled`** — for transient/structural faults with no clean single-metric
  observable (BGP flap, policy drift, process kill), `t_impact = t_start +
  impact_delay_s` (a small modelled lag reflecting EMA smoothing / reconvergence
  time). The lag is documented per scenario in `orchestrator.py`.
- **`modelled_fallback`** — a `vm_threshold` scenario whose probe returned data
  but never crossed the threshold within the duration falls back to
  `t_impact = t_start` and is flagged so the ML team can treat it as a weak label.
- **`probe_unavailable`** — a `vm_threshold` scenario whose probe returned no
  data for the entire window (VM query error / metric absent).

> Realism note (ponytail, intentional shortcut): SD-WAN tunnel metrics are
> *modelled* by the controller (baseline + diurnal congestion + **live netem
> read-back** from the target's `tc` state). So injected netem on a CE uplink
> genuinely perturbs the emitted telemetry — the loop is real — but the
> jitter/loss values are statistical, not exact dataplane measurements.

---

## Scenarios

The **4 mandated** scenarios cover the signals the PLAN names, plus **3
adversarial extras**, **5 extended scenarios**, and **9 new core/catastrophic/correlated
scenarios** added in Phase 6 — **21 total**. The 9 new scenarios resolve all
their link-sets and node identifiers from `topology/topology-meta.json` at runtime
(no hardcoded interface names). The ground-truth label schema is **unchanged**;
catastrophic core faults set `device` to a real epicenter node (e.g. the specific
P router that failed or the POP ABR at the cut boundary).

| scenario | mechanism (native tool) | target | `t_impact` | expected telemetry signature |
|----------|------------------------|--------|-----------|------------------------------|
| **`congestion`** (a) | netem **delay+jitter+loss RAMP** on CE uplink (HTB-leaf splice) | `ce_*` | vm_threshold (`sdwan_tunnel_latency_ms`) | latency + jitter **creep** first, then loss appears on the site's tunnels as the ramp saturates — the classic congestion-buildup precursor |
| **`bgp_flap`** (b) | `vtysh clear bgp *` repeated | `pe*`/`ce_*` | modelled (+2 s) | **BGP ADJCHANGE bursts in Loki**; transient prefix withdrawal/relearn, table churn |
| **`tunnel_degrade`** (c) | netem **jitter+loss ramp** on CE uplink + **WireGuard rekey** anomaly (`ip link` bounce wg0) | `ce_*` | vm_threshold (`sdwan_tunnel_loss_pct`) | tunnel jitter + loss climb; **rekey clustering** (handshake retries) in controller `rekey` events |
| **`policy_drift`** (d) | CE VRF **route-map lowering local-preference** + soft-clear | `ce_*` | modelled (+3 s) | local-pref shift on CORP → **route-selection drift**; soft-clear ADJ event; path may deviate from policy |
| `node_failure` (extra) | `kill -9 bgpd` (watchfrr respawns) | `pe*`/`ce_*` | modelled (+1 s) | bgpd gap → prefix withdrawal until watchfrr restart; recoverable outage |
| `asymmetric_loss` (extra) | netem **egress-only loss** on CE uplink | `ce_*` | vm_threshold (`sdwan_tunnel_loss_pct`) | one-directional loss → loss% up while latency stays ~normal (hard-to-diagnose asymmetry) |
| `brownout` (extra) | netem **rate cap** on CE uplink (bandwidth starvation) | `ce_*` | modelled (+4 s) | bandwidth starvation on the uplink; not observable in tunnel telemetry (a rate cap has no `delay`/`loss` token for `_read_netem` to pick up, and wg0 RTT doesn't traverse eth1) |
| `mpls_underlay_failure` | `ip link set <iface> down` on a P-router CE-facing interface | non-ABR `p*` (e.g. `p3`) — ABRs rejected with `SystemExit`, they have no P-PE link | modelled (+1 s) | P-PE link down; LDP reconverges to secondary path; ~1 s with BFD enabled |
| `ldp_session_flap` | `vtysh clear mpls ldp neighbor` N times (severity scales count) | `pe*` | modelled | LDP session torn/re-established; Loki logs `ldp_event=Down/Up`; self-recovers per cycle |
| `hub_spoke_congest` | netem **delay+jitter+loss ramp** on hub CE uplink (eth1) | `ce_hub*` | modelled (+4 s) | hub uplink congestion, injected on the hub; not observable in tunnel telemetry (`_read_netem` keys on the spoke's `site`, never the hub's) |
| `bgp_cascade` | `vtysh clear bgp *` repeated N times (severity scales count, 8 s gaps) | `ce_hub*`/`pe*` | modelled (+2 s) | repeated BGP session clears on a hub CE; RIB churn (Loki ADJCHANGE); no probe — `sdwan_path_changes_total` is unlabelled/RNG-driven, a crossing can't be attributed to this fault |
| `controller_drift` | HTTP POST to SD-WAN controller `/fault/drift` (raises latency threshold multiplier) | `ce_*` (site) | modelled | controller suppresses failover for the site; `sdwan_controller_drift_active` rises; clears via `/fault/drift/clear` |
| `p_node_failure` | `MultiLinkFault` brings down **all** core interfaces of one P router atomically | `p1..p24` | modelled (+1 s) | `ospf_neighbor_state` drops to 0 for all peers of that node; traffic reroutes via mesh + PE dual-homing; `mpls_lsp_count` shifts on neighbours |
| `pop_isolation` | `MultiLinkFault` cuts all inter-POP links of one POP → region isolated | `pop1..pop6` | modelled (+2 s) | all inter-area `ospf_neighbor_state` = 0 for the POP; PE routes to the isolated region withdraw; named Phase-6 test (excluded from Poisson campaign) |
| `core_partition` | cuts the ring edge cut-set bisecting the backbone → two area-0 islands | `pop1` (canonical) | modelled (+2 s) | area-0 becomes split; inter-area IA routes on the cut side disappear; backbone reconverges around remaining chords; named test only |
| `srlg_cut` | `MultiLinkFault` brings down both links in one SRLG conduit simultaneously | `srlg_pop1_2` etc. | modelled (+1 s) | correlated dual-link drop; OSPF floods two LSA removals at once; reroutes around the broken adjacency |
| `core_congestion` | netem delay+loss **ramp** on a P-P backbone link (via `NetemImpair`) | ABR e.g. `p1` | modelled (+4 s) | all LSPs transiting that link degrade; latency climbs on cross-POP flows; no link-down event |
| `ospf_area_flap` | flap an inter-POP area-0 adjacency (`LinkFlap`) repeatedly | ABR e.g. `p1` | modelled (+2 s) | repeated SPF runs (`ospf_spf_last_executed_ms` jumps); inter-area reconvergence churn; ECMP path oscillation |
| `path_asymmetry` | `OspfCostShift` raises OSPF cost in one direction only | ABR e.g. `p2` | modelled (+1 s) | forward/return paths diverge; asymmetric latency visible in tunnel metrics; traceroute-level asymmetry |
| `rr_failure` | `kill -9 bgpd` on a route reflector (pe1 or pe2); watchfrr respawns | `pe1` / `pe2` | modelled (+1 s) | `bgp_peer_established` collapses cluster-wide; VPNv4 prefix propagation degrades until RR restarts; covers all 10 RR clients (pe3-pe12) |
| `gray_failure` | netem 0.5–2% loss on a backbone P-P link, NO `link-down` event | ABR e.g. `p5` | vm_threshold (`ospf_neighbor_state` or tunnel loss) | sub-BFD loss; BFD stays up; packets drop silently; hard-to-detect; `gray_failure` label distinguishes it from hard failures |

### Injectors (`injectors.py`)

Every injector class has `apply()` and a clean `revert()` (idempotent). Native
tools only:

- `NetemImpair` — delay/jitter/loss/rate via netem, with `ramp()` for gradual
  buildup. On CE uplinks (which carry an HTB QoS root) netem is spliced as the
  **leaf under the HTB default class** (`1:30`), preserving QoS; on P/PE core
  links (noqueue root) it uses native `containerlab tools netem set`.
- `LinkFlap` — `ip link set <if> down/up`.
- `BgpFlap` — `vtysh clear bgp [neighbor]` (transient, self-recovers).
- `ProcessKill` — `kill -9 $(pidof bgpd)`; watchfrr restarts; revert verifies.
- `WgRekeyAnomaly` — bounce `wg0` to force WireGuard handshake churn.
- `PolicyDrift` — inject/remove a CE VRF route-map altering local-preference.
- `MplsUnderlayFailure` — `ip link set <iface> down/up` on a P-router core interface.
- `LdpSessionFlap` — `vtysh clear mpls ldp neighbor <ip>` N times with a configurable gap.
- `MultiLinkFault` — brings down (or restores) a **set** of interfaces atomically by calling `LinkFlap` on each in sequence with negligible delay. Used by `p_node_failure` (all ifaces of a P router), `pop_isolation` (all inter-POP links of a POP), `core_partition`, and `srlg_cut` (both SRLG-shared links). The interface list is resolved from `topology-meta.json` at scenario start.
- `OspfCostShift` — issues `vtysh -c "interface <iface>" -c "ip ospf cost <N>"` to raise the OSPF cost on one direction of a P-P link. Revert restores the original cost. Used by `path_asymmetry` to split forward/return paths without triggering a link-down.
- `_DriftInjector` — inline injector (no extra class file); calls the controller HTTP API:
  - **apply**: `POST http://172.20.20.56/fault/drift` `{"site": ..., "latency_threshold_mult": N, "ttl_s": T}`
  - **revert**: `POST http://172.20.20.56/fault/drift/clear` `{"site": ...}`

> **Important netem detail:** `containerlab tools netem set` requires a netem
> *root* qdisc and **fails on CE uplinks** because they already have an HTB root
> (the QoS uplink). That is why CE-uplink impairment is applied as an HTB leaf —
> verified against the live lab. The controller's `_read_netem()` greps
> `tc qdisc show dev eth1` for delay/loss, so the leaf is still picked up.

---

## End-to-end proof (the key deliverable)

`python3 orchestrator.py --demo` — a high-severity congestion ramp on
`ce_branch1` for ~60 s. Verified run:

1. **Injector applied** — netem visible on the target mid-run:
   `qdisc netem 31: parent 1:30 ... delay 64ms 16ms loss 4.8%`
2. **Telemetry moved** — `max(sdwan_tunnel_latency_ms{device="ce_branch1"})`
   rose **24.79 ms → 38.15 ms** (Δ +13.36 ms) in VictoriaMetrics, crossing the
   threshold → `t_impact`.
3. **Label row written** with `t_start` / `t_impact` / `t_end` /
   `lead_time = 48.5 s` (`impact_method = vm_threshold`).
4. **Clean revert** — `tc qdisc show dev eth1` back to baseline (HTB root + 3
   `fq_codel` leaves, no netem). Lab left healthy.
