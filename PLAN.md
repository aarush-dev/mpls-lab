# Plan — Air-Gapped Predictive Copilot: Full Network Simulation + Telemetry (Phases 1–2)

> Handoff note: this plan was researched and approved in a remote authoring environment.
> Build/deploy it on the **local agent / workstation** (full kernel + 18 cores / 120 GB / 300 GB).
> See `docs/PHASE0-ENVIRONMENT.md` for what was verified remotely and what the local agent must
> re-check on its own kernel before deploying.

## Context

Network + data foundation for an ISRO BAH 2026 entry (air-gapped offline AI NOC copilot that
predicts SD-WAN-over-MPLS faults before impact). The AI/ML/LLM/RAG work is the user's team's job.
**Scope = Objectives 1–2 (Phases 1–2):** a full, reproducible Containerlab SD-WAN-over-MPLS
lab, complete telemetry collection, fault injection with ground-truth labels, and a clean,
documented data-export interface so the AI engineers can train/simulate models directly.

**Because this is a simulated lab, producing realistic, problem-statement-shaped telemetry IS
part of the task** — there is no external "real" data to fetch. The lab is the data source; we
make its output realistic (diurnal traffic, congestion ramps, flap precursors, tunnel decay,
policy drift) and optionally augment with a calibrated generator for ML scale.

### Environment
Build and deploy on the local workstation (18 cores / 120 GB RAM / 300 GB disk). **Everything is
built and deployed in ONE environment — the full lab, full node count, no smaller/validation
variant.** Work is committed to git. Docker present; Containerlab to be installed; build-time
egress for image pulls (air-gap is enforced at runtime, verified later).

### Decisions (confirmed)
- **NOS = FRRouting** (`quay.io/frrouting/frr`): free, light (~50–150 MB/node) → scales to a
  large topology; real OSPF/BGP/LDP/MPLS-L3VPN.
- **Data = lab-generated (primary) + calibrated synthetic (augmentation for ML scale)**, all in
  one schema.
- **Single full deployment.** Scope = Phases 1–2 + data API.

### Working principles for execution (per user)
- **Run `/ponytail full` and `/caveman` for this work** — at the start of execution and whenever
  spawning subagents, invoke the `/ponytail full` and `/caveman` skills and have subagents do the
  same, so all building follows those skills' guidance.
- **YAGNI / reuse-first.** Before writing anything, search for existing containerlab labs,
  images, and configs and adapt them. Don't over-build; no rigid prescribed topology — size and
  shape it to what the problem statement actually needs.
- **Agent strategy:** do the grunt work with **sonnet/haiku** subagents (config generation,
  Dockerfiles, compose files, scenario scripts, doc writing). Use **opus only to review** what
  sonnet produced (correctness/architecture pass), after the sonnet pass — not for first drafts.
- **Milestone gating:** after each milestone (including sub-steps within a phase), pause and ask
  the user whether to continue before moving on.

### Key technical facts (researched, sourced)
- FRR uses `kind: linux`; bind `frr.conf` + `daemons`. SNMP = net-snmp `snmpd` + FRR **AgentX**
  (`agentx` in frr.conf, `-M snmp` / `frr-snmp`); snmpd starts before FRR.
- **FRR MPLS dataplane needs host kernel modules** (`mpls_router`, `mpls_gso`, `mpls_iptunnel`)
  + `sysctl net.mpls.platform_labels` and per-iface input. **Verify at execution**; if the
  kernel can't load them, use VRF-based L3VPN emulation (same routing/telemetry/fault signals
  the AI needs — labels in the dataplane aren't what the models learn from).
- Reuse candidates: `martimy/clab_mpls_frr` (MPLS/LDP), `frr01` official lab, `upa/nante-wan`
  (open-source SD-WAN: VXLAN/DMVPN/IPSec + FRR BGP/EVPN), `ntaka329` Containerlab+pmacct IPFIX
  example, `sflow/frr` telemetry image.
- Fault injection: **`containerlab tools netem set`** (delay/jitter/loss/corruption/rate) native;
  `tc` for reorder/dup; `ip link` flap; FRR BGP flap via `vtysh`/`clear bgp`;
  `kill -9 $(pidof bgpd)` (watchfrr restarts 60–600 s).
- Flows: pmacct `pmacctd`→IPFIX→`nfacctd`. Logs: FRR syslog (BGP ADJCHANGE = info level;
  needs `bgp log-neighbor-changes` + `log syslog informational`) → rsyslog/Fluentd. Overlay:
  WireGuard/strongSwan via `exec:`. Air-gap: `docker save|xz`/`load`, `image-pull-policy: Never`.

---

## Target Architecture (sized to need, not hardcoded)

A large multi-site **enterprise SD-WAN over a provider MPLS core**, generated from a small spec
so node counts are parameters (scale up freely on 120 GB):

- **Provider core:** several **P** routers (LDP LSRs) + multiple **PE** routers (LERs running
  MP-BGP VPNv4 / L3VPN, per-customer VRFs). Core IGP = OSPF; transport = LDP; PE mesh/RR for VPNv4.
- **Customer sites homing into PEs via CE:** **branch** (small, many), **hub** (regional
  aggregation, richer QoS), **datacenter** (server farms / traffic sinks). VPN segmentation via
  multiple VRFs (e.g. CORP/VOICE/GUEST). CE–PE = eBGP/OSPF per VRF.
- **SD-WAN overlay:** WireGuard/IPSec tunnels (hub-spoke + selective spoke-spoke) with dynamic
  routing over the overlay; a **simulated SD-WAN controller** (Python) holding overlay policy,
  doing path selection, and **emitting streaming telemetry** (per-tunnel latency/loss/jitter,
  rekey events, policy state).
- **QoS:** DSCP marking + `tc` HTB/`prio` per class on CE egress.
- **Traffic:** iperf3 + a Python app-flow simulator with realistic diurnal patterns (HTTP/DNS/
  VoIP-like) so utilization/latency curves look real.

The whole topology + all per-node configs are emitted by a **generator** (Python + Jinja2) from a
single spec — chosen for reproducibility and easy scaling, not as a rigid structure. Underlay has
an `mpls` mode and a portable `vrflite` fallback selected from the kernel-capability probe.

---

## Execution Phases (each ends with a user check-in)

### Phase 0 — Bring up tooling
Start Docker; install `iproute2` + **Containerlab**; probe kernel MPLS support → choose
`mpls`/`vrflite`. **→ check in.**

### Phase 1 — Network simulation
1. **Reuse pass (sonnet):** pull/adapt existing FRR-MPLS + nante-wan SD-WAN labs rather than
   writing from scratch. **→ check in.**
2. **Node image (sonnet):** `frr-node` Dockerfile = FRR + snmpd + pmacctd + tc + wireguard, with
   ordered `start.sh`. Build + smoke-test. **→ check in.**
3. **Generator + spec (sonnet):** `topology-spec.yaml` + `generate.py` emit `clab.yml`, `frr.conf`
   (OSPF/LDP/MP-BGP-VPNv4/VRFs/CE-BGP), `daemons`, `snmpd.conf`, WireGuard, `qos.sh`. **→ check in.**
4. **SD-WAN controller + traffic-gen (sonnet)**; wire into topology. **→ check in.**
5. **Deploy the FULL topology** and verify control plane + reachability + QoS. **→ check in.**
6. **Opus review** of Phase 1 (correctness/architecture) → fix. **→ check in.**

### Phase 2 — Telemetry pipeline
1. **Stack (sonnet):** `telemetry/docker-compose.yml` — Telegraf (SNMP/AgentX + exec tunnel
   stats), VictoriaMetrics (TSDB, Prometheus API), Grafana (NOC dashboards), pmacct `nfacctd`
   (IPFIX flows), rsyslog/Fluentd→Loki (BGP/OSPF adjacency + route events). **→ check in.**
2. Normalize to one tagged time-series schema (device/interface/site/vrf, UTC). Verify metrics,
   flows, and parsed syslog events flowing on the full lab. **→ check in.**
3. **Opus review** of Phase 2 → fix. **→ check in.**

### Fault injection + ground-truth labels (the ML signal)
- `faults/orchestrator.py` schedules scenarios, calls injectors, writes a **labels timeline**
  (scenario id/type/target, severity, `t_start`/`t_impact`/`t_end`, `lead_time`) for joining.
- Injectors reuse native tools (netem CLI, `tc`, `ip link`, `vtysh`, process kill, controller
  policy-drift, rekey anomaly).
- Implement the **4 mandated scenarios** + adversarial extras so the conditions the problem
  statement names are reproducible on demand. **→ check in after the scenario set runs.**

### Data realism + synthetic augmentation
- Tune lab traffic/impairment so real captures match problem-statement shapes (congestion
  buildup, flap precursors, jitter/loss decay, policy drift).
- `synthetic/` (sonnet): calibrate to real captures, then emit large **labeled** multivariate
  time-series in the same schema for ML-scale training. **→ check in.**

### Clean data API (contract for the AI engineers)
- `dataapi/` (sonnet, FastAPI, local-only): `/metrics` `/events` `/flows` `/labels` `/topology`
  (graph JSON) `/datasets` (Parquet). `export.py` joins metrics+flows+events+labels → documented
  Parquet schemas in `dataapi/schema/`. Seed `ragcorpus/` (topology maps, runbooks, incident
  templates) for the RAG team. **→ check in.**

### Air-gap packaging & verification
- `airgap/`: `pull-and-save.sh` (`docker save|xz`), `load-offline.sh`, `image-pull-policy: Never`,
  `verify-airgap.sh` proving zero runtime egress (20% of grading). **→ check in.**

---

## Reuse-before-build (don't reinvent)
Adapt: `martimy/clab_mpls_frr`, `frr01`, `upa/nante-wan`, `ntaka329` pmacct lab, `sflow/frr`;
native `containerlab tools netem`; Telegraf SNMP plugin; VictoriaMetrics+Grafana; pmacct pipeline.

## Verification (on the full lab)
1. `containerlab inspect` → all nodes healthy at full scale.
2. Control plane: `vtysh` OSPF/BGP-VPNv4/LDP up, overlay `wg show`; cross-VRF + overlay iperf3.
3. Telemetry: metrics in VictoriaMetrics/Grafana, `snmpwalk` IF-MIB, IPFIX in nfacctd, BGP/OSPF
   adjacency events in Loki.
4. Fault loop: run each mandated scenario; telemetry shows the precursor; a matching labels row
   records `t_start`/`t_impact`/`lead_time`.
5. Data API: `/datasets` returns labeled Parquet matching the schema; `/topology` returns the graph.
6. Air-gap: `verify-airgap.sh` shows zero outbound during a runtime deploy.
7. Commit + push to a branch; open a **draft PR**.
