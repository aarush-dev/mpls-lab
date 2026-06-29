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
> PYTHONPATH=/root/LAB python3 orchestrator.py --scenario mpls_underlay_failure --target p1
> ```

`--target` is a **device name** (node): `p1..p3`, `pe1..pe3`,
`ce_branch1..4`, `ce_hub1..2`, `ce_dc1..2`. Severity ∈ `low|medium|high`
(scales impairment magnitude). `--duration` is total seconds.

---

## Label schema (the contract for the data-API + ML)

Labels are **line-oriented JSON** (`labels/labels.jsonl`), one object per
scenario instance. All timestamps are **UTC ISO-8601** (`...Z`). Join to
telemetry on `device` and the `[t_start, t_end]` window; `t_impact` marks when
the effect became observable, and `lead_time` is the precursor window the model
must predict within.

| field            | type        | meaning |
|------------------|-------------|---------|
| `scenario_id`    | string      | unique id `<type>-<target>-<hex8>` |
| `type`           | string      | scenario type (see table below) |
| `target`         | object      | what was hit: always `device`, plus `interface`/`vrf`/`tunnel`/`neighbor`/`process`/`rate_kbit` as relevant |
| `severity`       | string      | `low` \| `medium` \| `high` |
| `t_start`        | ISO-8601 Z  | injection moment |
| `t_impact`       | ISO-8601 Z  | first moment the effect is observable in telemetry |
| `t_end`          | ISO-8601 Z  | fault cleared / reverted |
| `lead_time`      | float (s)   | `t_impact - t_start` — the precursor lead window |
| `impact_method`  | string      | how `t_impact` was derived (see below) |
| `probe`          | string/null | PromQL query polled to detect impact (null if modelled) |
| `baseline_value` | float/null  | probe value just before injection |
| `impact_value`   | float/null  | probe value at threshold crossing |
| `signature`      | string      | human-readable expected telemetry signature |
| `device`         | string      | universal join key = node name (mirrors `target.device`) |

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
- **`modelled_fallback`** — a `vm_threshold` scenario whose probe never crossed
  within the duration falls back to `t_impact = t_start` and is flagged so the
  ML team can treat it as a weak label.

> Realism note (ponytail, intentional shortcut): SD-WAN tunnel metrics are
> *modelled* by the controller (baseline + diurnal congestion + **live netem
> read-back** from the target's `tc` state). So injected netem on a CE uplink
> genuinely perturbs the emitted telemetry — the loop is real — but the
> jitter/loss values are statistical, not exact dataplane measurements.

---

## Scenarios

The **4 mandated** scenarios cover the signals the PLAN names, plus **3
adversarial extras** and **5 extended scenarios** — **12 total**.

| scenario | mechanism (native tool) | target | `t_impact` | expected telemetry signature |
|----------|------------------------|--------|-----------|------------------------------|
| **`congestion`** (a) | netem **delay+jitter+loss RAMP** on CE uplink (HTB-leaf splice) | `ce_*` | vm_threshold (`sdwan_tunnel_latency_ms`) | latency + jitter **creep** first, then loss appears on the site's tunnels as the ramp saturates — the classic congestion-buildup precursor |
| **`bgp_flap`** (b) | `vtysh clear bgp *` repeated | `pe*`/`ce_*` | modelled (+2 s) | **BGP ADJCHANGE bursts in Loki**; transient prefix withdrawal/relearn, table churn |
| **`tunnel_degrade`** (c) | netem **jitter+loss ramp** on CE uplink + **WireGuard rekey** anomaly (`ip link` bounce wg0) | `ce_*` | vm_threshold (`sdwan_tunnel_loss_pct`) | tunnel jitter + loss climb; **rekey clustering** (handshake retries) in controller `rekey` events |
| **`policy_drift`** (d) | CE VRF **route-map lowering local-preference** + soft-clear | `ce_*` | modelled (+3 s) | local-pref shift on CORP → **route-selection drift**; soft-clear ADJ event; path may deviate from policy |
| `node_failure` (extra) | `kill -9 bgpd` (watchfrr respawns) | `pe*`/`ce_*` | modelled (+1 s) | bgpd gap → prefix withdrawal until watchfrr restart; recoverable outage |
| `asymmetric_loss` (extra) | netem **egress-only loss** on CE uplink | `ce_*` | vm_threshold (`sdwan_tunnel_loss_pct`) | one-directional loss → loss% up while latency stays ~normal (hard-to-diagnose asymmetry) |
| `brownout` (extra) | netem **rate cap** on CE uplink (bandwidth starvation) | `ce_*` | vm_threshold (`sdwan_tunnel_latency_ms`) | queueing latency climbs under load, loss arrives late — slow brownout, not a hard failure |
| `mpls_underlay_failure` | `ip link set <iface> down` on a P-router CE-facing interface | `p*` | modelled (+1 s) | P-PE link down; LDP reconverges to secondary path; ~1 s with BFD enabled |
| `ldp_session_flap` | `vtysh clear mpls ldp neighbor` N times (severity scales count) | `pe*` | modelled | LDP session torn/re-established; Loki logs `ldp_event=Down/Up`; self-recovers per cycle |
| `hub_spoke_congest` | netem **delay+jitter+loss ramp** on hub CE uplink (eth1) | `ce_hub*` | vm_threshold (`sdwan_tunnel_latency_ms`) | hub uplink saturates; all spoke tunnels routed through this hub show rising latency |
| `bgp_cascade` | `vtysh clear bgp *` repeated N times (severity scales count, 8 s gaps) | `ce_hub*`/`pe*` | vm_threshold (`sdwan_path_changes_total`) | repeated session clears; multiple path-switches; `sdwan_path_changes_total` increments |
| `controller_drift` | HTTP POST to SD-WAN controller `/fault/drift` (raises latency threshold multiplier) | `ce_*` (site) | modelled | controller suppresses failover for the site; `sdwan_controller_drift_active` rises; clears via `/fault/drift/clear` |

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
