# Simulation, dataset generation, fault injection — current state

Every number below re-derived from source, `file:line` cited. Nothing aspirational.

---

## 1. What the simulation actually is

Two layers, both real, plus a modelled third.

### 1.1 Real: a 148-container routed network

`generator/generate.py` reads `topology-spec.yaml` and emits `topology/clab.yml`,
per-node `frr.conf`, `snmpd.conf`, WireGuard configs, `qos.sh`, and
`topology/topology-meta.json`. Containerlab deploys it.

Counts from `topology-spec.yaml:14-37`:

| thing | count | notes |
|---|---|---|
| P routers | 24 | OSPF + LDP only, no BGP. 4 per POP; first 2 of each POP are ABRs |
| PE routers | 12 | OSPF + LDP + MP-BGP VPNv4 + kernel VRFs. 2 per POP |
| POPs | 6 | each = one OSPF non-backbone area (1..6); area 0 = inter-POP backbone |
| CE routers | 34 | 24 branch + 6 hub + 4 dc |
| hosts | 78 | one per (site, VRF): branch×2, hub×3, dc×3 |
| **containers** | **148** | 24+12+34+78 |
| SNMP poll targets | 70 | 24 P + 12 PE + 34 CE (`telegraf.conf` agent list) |

Underlay is OSPF multi-area + LDP with loopback transport
(`topology-spec.yaml:55-60`); overlay is PE-PE MP-BGP VPNv4 with pe1/pe2 as route
reflectors (`topology-spec.yaml:31-32`) and 3 VRFs — CORP, VOICE, GUEST, the last
not present on branches (`topology-spec.yaml:71-85`). Inter-POP links carry
`igp_cost_inter: 100` vs `igp_cost_intra: 10`, so SPF stays in-POP unless
something breaks. `inter_pop_redundancy: 2` gives parallel links per adjacency —
which is what makes an SRLG cut interesting: the "redundant" pair shares a duct.

This layer forwards real packets. OSPF adjacencies, LDP label bindings, BGP
sessions, kernel VRF tables and `tc` HTB QoS are all genuine.

### 1.2 Real: traffic on the wire

`trafficgen/trafficgen.py` moves actual bytes between host containers with
BusyBox `nc` (`trafficgen.py:1-22` — `iperf3` is absent from
`wbitt/network-multitool:alpine-minimal`, so `nc` it is). Per-VRF flow shape
at saturation, `trafficgen.py:74-78`:

| VRF | max concurrent flows | bytes/flow | burstiness | size CV | intent |
|---|---|---|---|---|---|
| VOICE | 60 | 18 K | 0.08 | 0.10 | codec-like: many tiny steady flows |
| CORP | 22 | 900 K | 0.65 | 0.60 | office TCP: fewer, larger, spiky |
| GUEST | 7 | 6 M | 0.90 | 1.10 | bulk: sparse, fat, heavy-tailed |

Flow count and size are drawn from a **seeded** RNG keyed on
`blake2b(site|vrf|tick)` (`trafficgen.py:113-116`) — deliberately not `hash()`,
which Python randomises per process and used to give a different plan every run.

### 1.3 The load curve — one source of truth

`trafficgen/diurnal.py` is imported by trafficgen, the controller, *and* the
env-metrics sidecar, so offered load and every derived metric move together.

- 24 h compressed to `DIURNAL_PERIOD` seconds (default 3600 → 1 h = 1 day).
- Base curve: night floor 0.10, morning bell at 10:00, afternoon mass at 13:30,
  lunch dip carved at 12:30 (`diurnal.py:52-64`).
- Per-VRF profile `(floor, gain, phase_shift)` — `diurnal.py:35-39`:
  VOICE `(0.18, 0.62, 0)` steady; CORP `(0.03, 0.97, 0)` big swing;
  GUEST `(0.03, 0.72, +2.5 h)` evening lean.
- Weekly envelope: weekdays 1.0, weekend `0.60`, cosine-eased at the Fri→Sat and
  Sun→Mon boundaries so the taper isn't a step the model reads as a fault edge
  (`diurnal.py:81-110`).

Closed-form, no numpy, deterministic. Ceiling stated in the module docstring: the
shape is hand-tuned, not fitted to a real capture.

### 1.4 Modelled: the SD-WAN controller

`controller/controller.py` is the one component that is explicitly a simulation,
and it says so in its own HELP strings (`controller.py:459-484`). Per tunnel:

- **latency** = measured wg0 RTT + modelled M/M/1 queue term + netem readback + noise.
  RTT is genuinely measured: a thread pool `docker exec`s `ping -I wg0` into each
  spoke on a ~45 s cadence and caches min/avg/max/loss (`controller.py:129-165`,
  `341-363`). Queue term is `min(60, queue_mult * 9 * ρ/(1-ρ))` — blows up as
  utilisation → 1 (`controller.py:246`).
- **jitter** = measured (max−min) + an AR(1) walk with memory 0.85, so it wanders
  with excursions instead of being white noise (`controller.py:252-256`).
- **loss** = max(measured, modelled floor ~0–0.3%) + congestion tail
  `(ρ−0.80)² × 22` + occasional micro-bursts of 1–4 ticks + netem
  (`controller.py:263-276`).
- **rekeys** — ~every 120 s, accelerating under loss, and *clustering*: stress
  accrues "debt" that drains as a burst, which is the precursor shape
  (`controller.py:288-304`).

Per-tunnel RNG seeded by `zlib.crc32(tunnel_name)` so the noise realisation is
stable across restarts (`controller.py:105`).

Path selection is real logic: score = `loss×10 + latency`, failover when the
current path exceeds 5% loss or 3× its baseline latency, 15% hysteresis, and
recovery back to the per-VRF preferred hub when healthy
(`controller.py:371-424`).

**The honest caveat, verbatim from the source** (`controller.py:9-14`,
`220-223`): the netem term is *not* a measurement. It's the impairment read back
out of the uplink's qdisc **config**, because the wg0 ping doesn't traverse eth1.
It's also per-site, so every tunnel and VRF of a site carries the identical
addend. Any fault label whose `t_impact` is a threshold crossing of these series
is a crossing of a partly modelled series.

### 1.5 Telemetry pipeline

`telemetry/docker-compose.yml` — 11 services: victoriametrics, grafana, telegraf,
nfacctd, loki, promtail, controller, ldp-metrics, env-metrics, kafka, trafficgen.

| pillar | path | cadence |
|---|---|---|
| metrics | SNMP (70 agents) + controller `/metrics` scrape → Telegraf → VictoriaMetrics :8428 | 30 s (`telegraf.conf:7-8`) |
| logs | FRR syslog → Promtail → Loki :3100 | stream |
| flows | pmacct `pmacctd` on every FRR node → nfacctd (IPFIX :4739) | purge-interval |
| device health | `env-metrics.py` → POST `/api/v1/import/prometheus` | 30 s |
| LDP | `telegraf/ldp-metrics.sh` → same import endpoint | 30 s |

### 1.7 Streaming fan-out

`streaming/bridge.py` reads the four sources above and publishes to Kafka; two
consumer **groups** in `streaming/consume.py` then read independently — the
predictive pipeline from the earliest offset (it replays history to fill feature
windows), the copilot from the latest (it only wants current state). Separate
`group.id` values mean separate committed offsets, so each gets a full copy and
neither blocks the other.

| topic | parts | retention | payload |
|---|---|---|---|
| `noc.metrics` | 6 | 1 day | the same canonical 49-column rows, label columns stripped |
| `noc.events` | 6 | 7 days | discrete routing events at **exact** timestamps, templated |
| `noc.faults` | 3 | 30 days | orchestrator label rows |
| `noc.topology` | 1 | 30 days | static graph + the controller's live path choices |

Every record is keyed by `device`, which turns Kafka's per-partition ordering into
a per-device ordering guarantee. `noc.events` exists because 30 s buckets cannot
resolve a BGP reset and its reconvergence when both land in one bucket. Full detail,
including two non-obvious failure modes (no cross-topic ordering; mixed timestamp
formats) in `streaming/README.md`.

### 1.6 Modelled: chassis and optics

`telemetry/envmodel.py` is the single source of truth for physics, imported by
**both** the live sidecar and the synthetic generator so they cannot drift.
Literature-grounded, cited in the module header:

| model | shape | source |
|---|---|---|
| power | `p_idle + (p_max−p_idle)·util + noise`, `IDLE_FRAC = 0.87` | Vishwanath, IEEE JSAC 2014 (measured CRS-3 idle ratio 0.90) |
| temperature | `prev + α(target − prev)`, `α = 0.2` — thermal mass as lag | — |
| temp→failure | **linear** `1 + 0.03(T − 25)`, not Arrhenius | El-Sayed, SIGMETRICS 2012 |
| power noise | σ = 35% of the dynamic band | arXiv 2602.22339 (real power R² = 0.33) |
| optics | SFF-8472 DOM: rx power sags, laser bias climbs with age | SFF-8472 / RFC 3433 ENTITY-SENSOR-MIB |

`ROLE_PMAX_W`: core 3200 W, pe 1650 W, hub/dc 180 W, branch 72 W. Per-POP ambient
is spread by a golden-ratio walk over the POP index — deterministic, no RNG
(`envmodel.pop_ambient_c`).

`env-metrics.py` splits real from modelled explicitly in its own docstring
(`env-metrics.py:9-36`):

- **REAL** — `node_cpu_pct`/`node_mem_pct` from one `docker stats` call;
  `iface_queue_backlog_bytes`/`iface_queue_drops` from `tc -s qdisc`;
  `bgp_msg_rx/tx_total`, `rib_routes`, `ospf_lsa_count` from vtysh JSON.
- **MODELLED** — `device_temp_c`, `device_power_watts`, `device_fan_rpm`,
  `device_psu_voltage_v`, `xcvr_temp_c`, `xcvr_rx_power_dbm`, `xcvr_tx_bias_ma`.

The modelled sensors are driven by measured load, not a free-running clock, so the
correlation with device state is genuine even though the transfer function is not.
Chassis load uses **forwarding** load (the diurnal curve lifted by CPU), not
`cpu/100` — a router's CPU idles at line rate, and using it left temperature and
fan speed flat (`env-metrics.py:330-335`).

---

## 2. How the dataset is generated

Two independent producers, one schema. `dataapi/export.COLUMNS` is the single
source of truth and `synthetic/generate.py` imports it (`generate.py:41`), so the
two can never disagree.

### 2.1 Schema — 49 columns

`dataapi/export.py:38-53`. First 21 are the original schema in their original
order, so readers written against it still work.

- keys: `ts, device, site_type, vrf, entity, entity_type`
- interface: `if_in_octets, if_out_octets, if_oper_status`
- tunnel: `tunnel_latency_ms, tunnel_jitter_ms, tunnel_loss_pct, tunnel_rekeys`
- flow: `flow_bytes, flow_packets`
- label (primary episode): `is_fault, scenario_id, fault_type, severity,
  lead_time_s` — `severity` is an ORDINAL FLOAT (0.33/0.66/1.0), string in
  `severity_label`; `ts` is `timestamp[us, tz=UTC]`, not a string
- **added (interface)**: `if_in_errors, if_in_discards, if_out_errors,
  if_out_discards, q_backlog_bytes, q_drops, xcvr_temp_c, xcvr_rx_power_dbm,
  xcvr_tx_bias_ma`
- **added (device)**: `cpu_pct, mem_pct, bgp_msg_rx, bgp_msg_tx, rib_routes,
  ospf_lsa_count, device_temp_c, device_power_watts, device_fan_rpm,
  device_psu_voltage_v`
- **added (multi-label)**: `time_to_impact_s, fault_types, severities,
  scenario_ids, impact_methods` are index-aligned LISTS — one entry per episode
  overlapping the row, element 0 = primary — plus `n_concurrent` (int8),
  `severity_label`, and the explicit `fault_type_primary / severity_primary /
  scenario_id_primary` aliases

Three `entity_type` values (`export.py:55-60`): `interface` (per physical port),
`tunnel` (per WireGuard tunnel), `device` (whole box, `entity` == device name).

### 2.2 Real path — the join

`dataapi/export.build_dataset()`:

1. Three range-queries against VictoriaMetrics, one per entity scope
   (`export.py:243-246`), each pivoted to one row per (device, entity, ts-bucket).
2. Flows bucketed per device from `docker logs tele-nfacctd` JSON purge records
   (`export.py:154-178`), then merged **only onto the device rows** — merging into
   the whole frame would replicate one measurement across every interface and
   tunnel row of that bucket and inflate a naive sum ~15× (`export.py:252-259`).
3. Labels LEFT-joined on device + **bucket-interval overlap**, not instant
   containment: a row covers `[ts, ts+step)` and is faulty if that overlaps
   `[t_start, t_end]` (`export.py:184-209`). Containment would drop every fault
   window narrower than one bucket — most of the label file.
4. Interface-scoped labels narrow to their own interface; tunnel and device rows
   stay in scope (`export.py:215-217`).
5. Overlapping labels: ALL of them are emitted as index-aligned lists, primary
   (highest severity) at element 0 (`export.attach_labels`). The old
   highest-severity-wins collapse was deterministic but discarded the concurrency
   a multi-label head is built to learn.
6. `time_to_impact_s = t_impact − bucket_ts` per episode — positive before
   impact, negative after. It is a LIST, so `> 0` no longer works on it: use
   `export.precursor_mask(df)`.
7. Written atomically via `os.replace` so two uvicorn workers can't produce a
   footerless Parquet (`export.py:271-275`).

Join key is `device` throughout.

### 2.3 Synthetic path

`synthetic/calibrate.py` reads the newest real Parquet and emits `profile.json`:
per-site octet rates and seeds, per-site-type tunnel baselines,
`device_health` mean/std per key, an inventory, and 21 `fault_signatures` each
carrying `lat_peak / loss_peak / jit_peak / lead_s / kind`
(`calibrate.py:_fault_signatures`). `lead_s` is NO LONGER overwritten from the
capture: the median lead of a 24.5-minute capture at 30 s resolution came out
~2 s, the generator's 4-bucket floor clamped every draw to exactly 120 s, and
`lead_time_s` shipped with CV 0.03. Leads now come from `faults/leadpriors.py`
(per-fault-type bucket ranges, lognormal, p10/p90 on the endpoints), shared with
the live orchestrator. A measured median is kept as `lead_s_hint` only when the
capture spans a full `DIURNAL_PERIOD`.

`synthetic/generate.py` then walks `--days` of `--step` buckets over that
inventory:

- `_gen_interfaces` — cumulative octet counters driven by the diurnal curve,
  Poisson error/discard counters whose rate rises with load, nonlinear queue
  occupancy, and SFF-8472 readings on `eth*` only (`generate.py:88-169`).
- `_gen_devices` — CPU/mem, RIB and LSDB sizes (LSDB zero on CEs — they run BGP
  but no OSPF), BGP counters, and chassis sensors where **temperature is walked
  forward in time** rather than sampled independently. That lag is exactly what
  makes it a precursor instead of a restatement of load (`generate.py:172-234`).
- `_gen_tunnels` — latency/jitter/loss around the per-site-type calibrated
  baseline plus a diurnal congestion bump (`generate.py:237-267`).
- `_inject_faults` — fully vectorised numpy masking per episode, no Python row
  loop (`generate.py:270-600`).

Episode semantics: `t_start` (ramp begins, precursor visible) → `t_impact`
(the SLA crossing) → `t_end` (= `t_impact + recovery`). The lead is drawn per
episode from `faults/leadpriors.py` — lognormal, p10/p90 pinned to the fault
type's bucket range — and the ramp spans it, so the impairment SLOPE carries the
lead. The impairment is piecewise linear through four knots: `t_start`→0,
`t_impact`→`p_cross` (the fraction that breaches `THETA_SLA` for the VRFs on that
entity), `t_impact + 0.3·dur`→1 (the calibrated peak), `t_end`→0. `p_cross == 1`
means the signature never reaches the objective and `impact_method` is `modelled`
rather than `ramp_derived`. The 4-bucket floor survives as a safety net only, and
the generator warns if it fires on more than 5% of episodes — it used to fire on
100% of them.

Details that exist because the naive version was wrong:

- Cumulative counters are perturbed in the **rate**, integrated forward, never in
  the accumulated value — scaling the absolute counter makes it step *backwards*
  when the window ends, which every rate derivation (including this repo's own
  `calibrate._octet_rate`) reads as a counter reset (`generate.py:345-367`).
- Ramp peaks are floored at 1.15× the healthy value, because several calibrated
  peaks sit *below* the generated healthy mean once the diurnal bump is applied —
  so a labelled degradation used to ramp downward (`generate.py:395-408`).
- Only rows a perturbation actually reaches get labelled. The window is
  device-wide but each `kind` moves one entity_type, so labelling the whole window
  marked ~45% of `is_fault` rows byte-identical to baseline (`generate.py:490-499`).
- Overlapping episodes are dropped only when they share a `kind` (one impairment
  install per entity); different kinds may overlap, which is where within-device
  concurrency comes from. Labels are lists, so an overlap no longer overwrites
  anything.
- 22% of episodes trigger a cascade on the SAME device with a DIFFERENT kind — a
  congesting uplink degrading that site's tunnels — which is what produces
  `n_concurrent` up to 3.
- The Parquet carries `synthetic=true` in **file-level key/value metadata**, not
  just the filename, so a renamed copy is still distinguishable
  (`generate.py:652-661`).

`synthetic/check.py` validates the newest output by mtime and asserts 3
entity_types, role-scaled power spread >10×, physical temperature range, per-POP
ambient spread, fault counters rising, gray_failure optical divergence, a per-key
precursor ramp (a global healthy mean confounds the ramp with the diurnal curve),
`lead_time_s` CV ≥ 0.50 with one distinct lead per episode, the three error
counters still zero, `vrf`/`flow_bytes` non-null, `n_concurrent` ≥ 2 somewhere, and
`seed` + `calibrated_from` in the file metadata.

`synthetic/verify_fixes.py <train> <holdout>` is the audit's own acceptance gate:
24 checks including hazard-bin occupancy, `corr(lead, ramp span)`, holdout episode
disjointness and matched load.

### 2.4 Shipped samples

`DATASETS.md` — real capture 49,844 rows (70 devices, 24.5 min, 391 fault rows, 266
precursors, 10 fault types, 17 episodes); synthetic train 2,589,120 rows (1 day,
seed 42, 159,021 fault rows, 124,108 precursors, 719 episodes, all 21 fault types,
lead CV 0.83); synthetic holdout 2,589,120 rows (1 day, `--seed 7`, 156,054 fault
rows, 122,627 precursors, 720 episodes, lead CV 1.03, 0 `scenario_id` overlap with
seed 42). Concat-compatible. Both synthetic files are a full day so the holdout
does not conflate unseen episodes with unseen time-of-day.

---

## 3. Fault injection

### 3.1 Ten primitives

`faults/injectors.py`, each with `apply()` / `revert()`, revert idempotent, stdlib
only, native tools reused rather than reinvented.

| # | class | mechanism | line |
|---|---|---|---|
| 1 | `NetemImpair` | delay/jitter/loss/rate, with `ramp()` | `injectors.py:49` |
| 2 | `LinkFlap` | `ip link` down → hold → up, N times | `:159` |
| 3 | `BgpFlap` | vtysh `clear bgp *` per discovered VRF instance | `:188` |
| 4 | `ProcessKill` | `kill -9 bgpd`; watchfrr respawns | `:246` |
| 5 | `WgRekeyAnomaly` | bounce wg0 → handshake storm | `:281` |
| 6 | `PolicyDrift` | route-map lowering local-pref inbound from PE | `:312` |
| 7 | `MplsUnderlayFailure` | down a P-side P-PE core link | `:403` |
| 8 | `LdpSessionFlap` | `clear mpls ldp neighbor`; self-recovers | `:426` |
| 9 | `MultiLinkFault` | down a computed **set** of links at once | `:456` |
| 10 | `OspfCostShift` | raise cost one direction → path asymmetry | `:490` |

Two mechanics worth knowing:

**netem placement is not uniform** (`injectors.py:15-23`). P/PE core interfaces
have a `noqueue` root, so `containerlab tools netem set` installs a root netem
directly. CE **uplinks already carry an HTB QoS root** with 3 classes — a root
netem install fails with `Invalid qdisc name: must match existing qdisc`. So on
CE uplinks netem is spliced as a **leaf under the HTB default class 1:30**,
replacing its fq_codel. This preserves QoS, stays visible to the controller's
qdisc readback, and `revert()` restores fq_codel.

**Ramping is the point.** `NetemImpair.ramp()` steps impairment from 0 to target
over N increments with a sleep between, so congestion *builds* — the precursor
the ML models learn from. Rate is excluded from the ramp because a rate cap is
binary (`injectors.py:117-140`).

`MultiLinkFault` downs links **in sequence, not atomically**, so OSPF sees the set
go down staggered — which is what actually happens in a duct cut
(`injectors.py:456-468`). Its link-sets are computed from `topology-meta.json`;
nothing is hardcoded.

### 3.2 Twenty-one scenarios

`faults/orchestrator.py:558-581`. Four mandated, the rest adversarial.

| scenario | target class | primitive | impact | ramp |
|---|---|---|---|---|
| `congestion` (a) | CE | netem delay+jitter+loss | **vm_threshold** | yes |
| `bgp_flap` (b) | CE+PE | BgpFlap | modelled | — |
| `tunnel_degrade` (c) | CE | netem + WgRekeyAnomaly | **vm_threshold** | yes |
| `policy_drift` (d) | CE | PolicyDrift | modelled | — |
| `node_failure` | CE+PE | kill bgpd | modelled | — |
| `asymmetric_loss` | CE | egress-only loss | **vm_threshold** | — |
| `brownout` | CE | rate cap | modelled | — |
| `mpls_underlay_failure` | internal P | P-PE link down | modelled | — |
| `ldp_session_flap` | PE | LDP clear | modelled | — |
| `hub_spoke_congest` | hub CE | netem on hub uplink | modelled | yes |
| `bgp_cascade` | hub CE | repeated BGP clears | modelled | — |
| `controller_drift` | hub CE | POST to controller `/fault/drift` | modelled | — |
| `p_node_failure` | any P | all core ifaces down | modelled | — |
| `pop_isolation` | POP | all inter-POP links of a POP | modelled | — |
| `core_partition` | POP seam | full bisecting edge cut-set | modelled | — |
| `srlg_cut` | SRLG | one shared conduit | modelled | — |
| `core_congestion` | ABR | netem on backbone link | modelled | yes |
| `ospf_area_flap` | ABR | flap area-0 adjacency | modelled | — |
| `path_asymmetry` | ABR | one-way OSPF cost hike | modelled | — |
| `rr_failure` | pe1/pe2 | kill bgpd on a route reflector | modelled | — |
| `gray_failure` | ABR | 0.5–2% loss, **below BFD trip** | modelled | — |

`gray_failure` is the highest-value one for prediction: sub-BFD loss means no
down event fires, so there is nothing to alarm on and only slow degradation to
detect (`orchestrator.py:542-555`).

### 3.3 How `t_impact` is decided

`_resolve_impact()` (`orchestrator.py:585-609`) returns one of four methods —
and the distinction is recorded per label, not glossed:

| method | meaning |
|---|---|
| `vm_threshold` | probe polled VictoriaMetrics and **crossed** → measured |
| `modelled_fallback` | probe was readable but never crossed → modelled delay |
| `probe_unavailable` | probe returned nothing all window (VM down / metric absent / bad selector) → modelled, and **no telemetry stands behind it** |
| `modelled` | scenario declares no probe |

Crossing is measured against a **baseline read before injection**, so it's a
delta, not an absolute (`orchestrator.py:647-652`).

**Only 3 of 21 scenarios can produce a measured `t_impact`.** The other 18 are
modelled, and each says why in its own docstring rather than pretending
otherwise — e.g. `brownout`: a rate cap has no observable in the telemetry path
because the controller parses only `delay`/`loss` tokens and the wg0 ping doesn't
traverse eth1 (`orchestrator.py:275-278`). `hub_spoke_congest`: the impairment is
on the *hub's* eth1 but the controller folds netem only from the *spoke's*
(`orchestrator.py:333-337`). `bgp_cascade`: `sdwan_path_changes_total` is a
single unlabelled fabric-wide counter that moves on its own from the controller's
micro-burst RNG, so a crossing can't be attributed (`orchestrator.py:352-356`).

### 3.4 Campaign mode

`run_campaign()` (`orchestrator.py:912`):

- **Poisson arrivals** — inter-arrival `expovariate(1/mean_gap)`, clamped to ≥5 s.
  `mean_gap=120` → ~1 fault per 2 min. Bursty like real incidents, unlike a fixed
  timer. Seeded for reproducibility.
- **Thread per fault**, so concurrent faults on different targets are genuinely
  concurrent, not serialised.
- **Active-target lock** prevents stacking two faults on one device; the target is
  released in an innermost `finally` so a builder that raises can't leak it for the
  life of the campaign (`orchestrator.py:823-909`).
- **19 of 21 scenarios** are in the random pool. `pop_isolation` and
  `core_partition` are excluded — whole-region and backbone cuts overlap
  link-sets, so they're run explicitly (`orchestrator.py:771-774`).
- Per-scenario duration bounds, sampled uniformly (`orchestrator.py:786-806`).
- **SIGINT-safe**: the handler sets an event, threads run their `finally` blocks,
  every injected fault reverts. A label row is written even when the injector
  raised — flagged with the error — rather than leaving core links down and losing
  the row.
- Summary reports **merged** fault seconds (overlapping windows counted once) and
  separately the concurrent sum. Summing durations naively double-counts and used
  to produce negative `healthy_seconds` and >100% `fault_pct`
  (`orchestrator.py:809-820`, `994-1007`).

### 3.5 Labels

One JSON object per fault, appended to `faults/labels/labels.jsonl`
(`orchestrator.py:612-635`): `scenario_id, type, target, severity, t_start,
t_impact, t_end, lead_time, impact_method, probe, baseline_value, impact_value,
signature, device, dry_run, error`, plus `campaign_id` in campaign mode.

`severity` is written as **null** for scenarios whose injector ignores it
(link-set and process-kill faults) — the column must not carry a value the fault
never used (`orchestrator.py:620-622`).

---

## 4. Known limits

1. **MPLS forwarding does not work on this kernel.** `CONFIG_LWTUNNEL` is absent,
   so label imposition fails: `Error: CONFIG_LWTUNNEL is not enabled in this
   kernel.` Observed: `Status: Label Changed Failed`, 114 OSPF routes but 9 in
   pe1's FIB, VPNv4 iBGP stuck in `Connect`, `bgp_peer_established` = 0. Phase 0's
   check gives a false pass — see `docs/PHASE0ENVIRONMENT.md` step 1b. The
   `vrflite` fallback named in `topology-spec.yaml:52-53` is **not implemented**.
2. **`if_in_errors` / `if_in_discards` / `if_out_errors` are constant 0** in
   BOTH paths, deliberately. veth pairs produce no CRC or input errors, so they
   are zero in every real capture; the generator emitted a load-dependent Poisson
   process, which made `if_in_errors > 0` a perfect synthetic-row detector. Now
   both emit 0 and `check.py` asserts it. The OIDs are wired correctly and will
   populate on real hardware, so the literature's top-ranked failure signal is
   reserved, not lost — retrain with it at deployment.
3. **`t_impact` is the SLA crossing where one exists** (`impact_method:
   ramp_derived`, `faults/leadpriors.THETA_SLA`), a live probe crossing where one
   fires (`vm_threshold`), and modelled otherwise — in the shipped synthetic files
   the split is ~49% `ramp_derived` / ~51% `modelled`, because the calibrated
   latency peaks sit below every latency objective and only loss breaches.
4. **`flow_bytes`/`flow_packets` on synthetic device rows are MODELLED from the
   per-VRF flow shapes** (`trafficgen.VRF_FLOW`) scaled by the diurnal curve, not
   calibrated against the real flow rows. They were null before, which was itself
   a synthetic-row detector. Null on P routers, which carry no site VRFs — the
   same shape the real capture shows.
5. **Tunnel latency's fault term is a config readback, not a measurement**
   (§1.4).
6. The diurnal curve shape is hand-tuned, not fitted to a real trace
   (`diurnal.py:23-25`).
