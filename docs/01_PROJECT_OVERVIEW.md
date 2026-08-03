# Project Overview: Air-Gapped Predictive NOC Copilot

**ISRO BAH 2026 Competition Entry — Phases 1 & 2 (Data Infrastructure)**

**See also:** [02 Architecture Analogies](02_ARCHITECTURE_ANALOGIES.md) | [04 Usability Cheatsheet](04_USABILITY_CHEATSHEET.md) | [05 Technical Glossary](05_TECHNICAL_GLOSSARY.md)

---

## 1. What This Project Is

This project builds the data infrastructure for an autonomous, air-gapped AI system that predicts network failures before they impact users. The system is designed for government and defense networks — environments where connecting to a cloud AI service is not permitted — and is our entry for the ISRO Bharat AI Hackathon (BAH) 2026 competition.

The competition asks competitors to build a NOC (Network Operations Center) Copilot: an intelligent assistant that watches a complex enterprise network in real time, spots signs of trouble early, and tells the operator what is about to break and why — all without ever touching the internet.

What you are reading now documents **Phases 1 and 2**: everything needed to generate, collect, label, and serve the training data that the ML models will learn from. The models themselves, the offline large language model (LLM), and the operator-facing copilot interface come in Phases 3 through 6.

---

## 2. The Core Problem This Solves

### Reactive vs. Predictive Detection

A traditional NOC runs on threshold-based alerting. A router's CPU hits 95% and an alarm fires. By that point, users are already affected: voice calls are dropping, VPN sessions are timing out, critical applications are degraded. The operator is now fighting a fire instead of preventing one.

Predictive detection works differently. The idea is that almost every network failure has a precursor signature — a subtle pattern in the telemetry data that appears minutes or even tens of minutes before the actual impact. Congestion does not materialize instantly; it builds. BGP (Border Gateway Protocol, the routing protocol that controls how traffic flows between network segments) sessions flap before they fully die. Tunnel latency creeps upward before packet loss appears. If a model can recognize those early signatures, the operator gets lead time: enough time to reroute traffic, page an on-call engineer, or trigger an automated remediation before a single user is affected.

This project provides the ground truth needed to train those models. Every fault that the system injects into the simulated network is timestamped with both when the fault begins (`t_start`) and when the telemetry metrics first cross the impact threshold (`t_impact`). The gap between those two timestamps — `lead_time_s` — is what the ML models are trained to predict.

### Why Air-Gap Matters

An air-gapped network is one with no connection to the public internet. Classified government networks, defense installations, critical infrastructure control systems, and many regulated financial environments all operate this way. The standard playbook for adding AI to any workflow — call an API, send data to a cloud model, get a response — does not apply here.

Every component of this system must run locally, on hardware that the operator controls, with zero outbound network dependency. This includes the models, the inference runtime, the vector database for retrieval, and the telemetry storage. The air-gap constraint is graded explicitly in the competition rubric (20% of the total score) and verified with an automated test that monitors network traffic during a runtime deployment to prove zero public egress.

---

## 3. What Was Built (Phases 1 and 2)

Think of this as building a synthetic but realistic training environment, equivalent to what a flight simulator is to a pilot training program. Rather than waiting for real network faults to happen and hoping someone is collecting the right data at the right time, we built a complete simulated network, inject controlled faults into it, and record every signal with millisecond precision.

Six major components were built:

**A simulated network of 148 lab containers** (~159 including telemetry and infrastructure services). Seventy of these are FRR (FRRouting — an open-source network operating system) router containers — 24 P-core, 12 PE, and 34 CE — running real routing protocols. Seventy-eight are host containers representing end-user machines at branch offices, regional hubs, and data centers. Together they form a realistic enterprise-grade network that generates genuine SNMP (Simple Network Management Protocol) counters, routing protocol events, and flow records — the same data a real NOC would see.

**A full telemetry pipeline.** Every metric from every container is collected, normalized, and stored. Interface utilization (bytes in/out per second) goes through SNMP into Telegraf into VictoriaMetrics (a time-series database compatible with Prometheus). Routing events (BGP session changes, OSPF adjacency updates) go through syslog into Promtail into Loki (a log aggregation system). Flow records (which source IP talked to which destination, how many bytes) go through IPFIX (a standard network flow protocol) into nfacctd. All signals share a common `device` label as the join key.

**A fault injection system with ground-truth labels.** Twenty-one named fault scenarios can be injected into any target device on demand or in a randomized campaign. Each injection writes a JSON label record containing the scenario type, target device, severity level, `t_start`, `t_impact`, and `lead_time_s`. These label files are the supervision signal for model training.

**A FastAPI data API.** A local HTTP server at port 8000 gives the ML team a clean, versioned interface to all of the above. They can query raw metrics, retrieve log events, download flow records, read fault labels, inspect the network topology as a graph, and — most importantly — download a pre-joined, labeled Parquet file that combines all four signal types into a single DataFrame ready for model training.

**A Kafka streaming layer for two independent consumers.** The Parquet path above is a batch interface — it answers "give me the last hour." The streaming layer answers "tell me as it happens." One producer (`streaming/bridge.py`) reads the already-running sources and publishes to four topics: `noc.metrics` (the same 59-column rows, labels stripped), `noc.events` (discrete routing events at exact timestamps, not 30-second buckets), `noc.faults` (ground-truth labels) and `noc.topology` (the graph plus the controller's live path choices). Two consumer *groups* then read it independently — the predictive-analysis pipeline replays from the earliest offset to build fixed-length feature windows, while the copilot pipeline starts at the latest offset and maintains a rolling natural-language incident brief. Because Kafka tracks committed offsets per group, each gets a full copy and neither can slow or block the other. Records are keyed by `device`, which turns Kafka's per-partition ordering into a per-device ordering guarantee. See `streaming/README.md`.

**A synthetic dataset and air-gap packaging.** Because 148 lab containers running for a few hours produce limited data at ML scale, a calibrated synthetic generator extends the real captures. Row count is `entities_per_tick × ticks`, linear in `--days` only (`--scale` only changes fault-episode density); at the current 899 entities/tick (661 interface + 168 tunnel + 70 device, `synthetic/generate.py:613-615`), `--days 7 --scale 3` (defaults `--days 2 --scale 1`; `synthetic/generate.py:667-669`) produces roughly 18.1 million rows. Two files are shipped, both `--days 1 --step 30 --scale 3.0` at 2,589,120 rows each: seed 42 for training and seed 7 as an episode-disjoint holdout (`DATASETS.md`). Everything else in `synthetic/output/` lacked `seed` + `calibrated_from` file metadata and was deleted — `check.py` now refuses an unattributable file. The entire software stack is packaged for offline deployment: Docker images are saved as compressed archives, and an automated verifier confirms that a full deployment produces zero outbound traffic to public IP addresses.

---

## 4. Network Architecture Overview

### The Three-Tier Hierarchy

Enterprise networks are typically organized into three layers, and this simulation faithfully reproduces all three.

**Provider core (P and PE routers).** The backbone of the network consists of 24 P routers (Provider core switches) organized into 6 regional Points of Presence (POPs) of 4 routers each (p1–p24), and 12 PE routers (Provider Edge — the boundary between the carrier core and the customer). Within each POP the four P routers form a full mesh (C(4,2)=6 intra-POP links) in that POP's own OSPF area (areas 1 through 6), with an IGP link cost of 10. Across POPs, a ring (POP1-2-3-4-5-6-1) plus three chords ([1,4], [2,5], [3,6]) creates 9 inter-POP adjacencies, each realized by two redundant parallel links at OSPF cost 100 in OSPF area 0 (the backbone area). Every inter-POP parallel link pair shares a single SRLG conduit, so a conduit failure drops both links atomically — faithfully modelling a fibre-cut in production. The first two P routers in each POP (e.g., p1 and p2 in POP1) act as Area Border Routers (ABRs), participating in both area 0 and the POP's local area; the last two (e.g., p3, p4) are area-internal and serve as the PE attachment points. The 12 PE routers (pe1–pe12, two per POP) each dual-home to the two PE-facing P routers in their POP. BFD (Bidirectional Forwarding Detection) runs at 300ms detect intervals on all core links, with bfdd active on every P and PE node. MP-BGP uses a route-reflector design: pe1 and pe2 act as route reflectors; pe3–pe12 are RR clients. This avoids a C(12,2)=66-session full mesh, using 21 sessions instead.

**Customer Edge routers (CE routers).** These sit at each customer location: 24 branch offices (small, single uplink), 6 regional hubs (larger, higher capacity), and 4 data center sites (server farms). Each CE connects to exactly one PE.

**Host containers.** Behind every CE router are one or more host containers representing the actual end-user machines — PCs, phones, servers — on that site's local network. Each host is isolated in its own VRF (see below), so a CORP-network PC and a VOICE-network phone at the same branch office cannot talk to each other at the IP layer, exactly as in a real enterprise deployment.

### MPLS: The Highway System

MPLS (Multiprotocol Label Switching) is the forwarding technology used in the provider core. Rather than making a routing decision at every hop based on the destination IP address, MPLS assigns each packet a short numeric label at the ingress PE and swaps that label at each P router until the packet reaches the egress PE. Think of it as a highway system with pre-assigned lanes: once a truck enters the highway and is assigned lane 3, every interchange simply reads "lane 3" and directs it forward, without re-examining the cargo manifest at every junction.

This makes the core fast and deterministic. The routing protocols that make MPLS work here are OSPF (Open Shortest Path First — an interior gateway protocol that builds a map of the core network) and LDP (Label Distribution Protocol — which assigns the actual MPLS label values to each path). PE routers also run MP-BGP (Multiprotocol BGP, specifically the VPNv4 address family) to exchange customer route information across the core.

### WireGuard SD-WAN Overlay: The Secure Second Road

On top of the MPLS underlay, the network runs a WireGuard-based SD-WAN (Software-Defined Wide Area Network) overlay. WireGuard is a modern, lightweight VPN (Virtual Private Network) protocol. Every branch and data center CE establishes an encrypted WireGuard tunnel to every one of the 6 hub CEs (a full spoke-to-hub mesh, not round-robin), giving the network 168 spoke-hub tunnels. Adjacent hub pairs (hub1↔hub2, hub3↔hub4, hub5↔hub6) also get direct hub-hub WireGuard links for resilience, for 171 tunnels total. The SD-WAN controller — a Python process — renders per-tunnel latency, jitter, and packet loss every 5 seconds and selects which tunnel each traffic class should use. These series are simulated, not fully measured: a real wg0 ping supplies part of the value, the rest is a modelled congestion term plus the injected fault read back from the site's netem qdisc config rather than observed on the wire (`controller/controller.py:9-14`).

This two-layer architecture (MPLS underlay + WireGuard overlay) is the defining characteristic of modern enterprise SD-WAN and is explicitly named in the competition problem statement. The interaction between underlay failures and overlay degradation is where some of the most interesting predictive signals live.

### VRFs: Three Physically Separate Virtual Networks

At each site, traffic is divided into three VRFs (Virtual Routing and Forwarding instances). You can think of a VRF as a completely separate routing table that lives inside the same physical router. Traffic in one VRF cannot cross into another without an explicit policy — they are as isolated as if they were running on separate hardware.

The three VRFs are:

- **CORP** (Corporate): Standard business traffic — file shares, email, enterprise applications. Present at all site types. DSCP class AF31 (a QoS marking that gives it moderate priority on congested links).
- **VOICE**: VoIP (Voice over IP) and real-time communications. Highest priority (DSCP class EF, Expedited Forwarding) and guaranteed 30% of the CE uplink bandwidth. Present at all site types.
- **GUEST**: Internet-access-only traffic for visitors. Lowest priority (best-effort). Present only at hub and data center sites; branch offices do not get guest WiFi in this topology.

### Topology Diagram

```
           ┌────────────── MPLS PROVIDER CORE ─────────────────────┐
           │  24 P-routers · 6 POPs × 4 · multi-area OSPF          │
           │                                                         │
           │  Ring: POP1─POP2─POP3─POP4─POP5─POP6─POP1 (area 0)  │
           │  Chords: POP1─POP4, POP2─POP5, POP3─POP6 (area 0)    │
           │  Each inter-POP adjacency: 2 parallel links, 1 SRLG   │
           │                                                         │
           │  Within each POP (example POP1):                       │
           │    p1─p2 (ABRs, area 0+1)  p3─p4 (PE-facing, area 1) │
           │    full mesh C(4,2)=6 links, OSPF cost 10              │
           │    pe1, pe2 dual-homed to p3, p4                       │
           │  BFD 300ms · LDP on all P-P and P-PE links             │
           └────────────────────┬───────────────────────────────────┘
                                │ dual-homed P→PE (2 uplinks per PE)
           ┌────────────────────┴───────────────────────────────────┐
           │  pe1 pe2 pe3 pe4 ... pe12  (MP-BGP VPNv4 RR mesh)    │
           │  RR servers: pe1+pe2;  RR clients: pe3–pe12            │
           └────────────────────┬───────────────────────────────────┘
                                │  eBGP per VRF
        ┌───────────────────────┴────────────────────────────────┐
        │   24× ce_branch   6× ce_hub   4× ce_dc   (CE routers) │
        │   + 78 host containers (one per site+VRF combination)  │
        └────────────────────────────────────────────────────────┘
                │  WireGuard SD-WAN overlay (168 spoke-hub + 3 hub-hub
                │  = 171 tunnels; full spoke-to-all-6-hubs mesh)

  Each CE site:
  ┌─────────────────────────────────────────────────┐
  │  CE router (FRR)                                │
  │   ├─ vrf_CORP  ──► host_corp  (192.168.x.0/24) │
  │   ├─ vrf_VOICE ──► host_voice (192.168.x.1/24) │
  │   └─ vrf_GUEST ──► host_guest (hub/dc only)    │
  └─────────────────────────────────────────────────┘
  
  Telemetry flow:
  FRR routers ──SNMP──► Telegraf ──► VictoriaMetrics (PromQL) [70 SNMP agents]
  FRR syslogs ──────────────────► Loki (log queries)
  Flow records ──IPFIX──► nfacctd ──► SQLite / API
  Controller  ──────────────────► Prometheus metrics
  OSPF/BGP/MPLS ─ ldp-metrics ──► VictoriaMetrics [ospf_neighbor_state,
                                    ospf_spf_*, mpls_lsp_count,
                                    bgp_peer_established; 11 Grafana panels]
                                        │
                               FastAPI Data API :8000
                                        │
                               ML team / model training
```

### Realism Mechanisms

The core redesign was specifically motivated by making the lab's failure behavior match real carrier-grade MPLS networks. Several mechanisms work together to achieve this:

- **Multi-area OSPF fault isolation:** A fault in one POP's OSPF area triggers SPF recalculation only within that area; other POPs see only inter-area summary changes, exactly as in production.
- **IGP-cost traffic engineering:** The 10/100 cost differential (intra/inter-POP) creates preferential intra-POP paths and deterministic ECMP load sharing across inter-POP chords — a real TE construct without RSVP-TE.
- **Multi-hop cross-POP LSPs:** With 6 POPs arranged in a ring plus chords, a packet from POP1 to POP6 traverses multiple P hops, generating realistic transit telemetry — verified by a live pe1→pe11 path showing metric 140 (>100) and an MPLS label pushed in the forwarding table.
- **SRLG-correlated fibre cuts:** Each inter-POP parallel link pair shares one SRLG conduit. A single `srlg_cut` scenario drops both links atomically, replicating a physical fibre cut.
- **Sub-BFD gray failures:** The `gray_failure` scenario injects 0.5–2% loss on a backbone link without ever bringing the link down — the class of soft failures that threshold-based alerting misses entirely.
- **PE dual-homing + BFD fast reconvergence:** Each PE has two P uplinks; BFD at 300ms ensures reroute happens in under a second when a P-facing link fails.
- **Poisson chaos campaign:** The randomized fault campaign mixes edge faults, core faults (P node failures, SRLG cuts), catastrophic faults (POP isolation, core partition), and correlated faults — not just repeatable single-fault scripts.

---

## 5. Design Decisions and Why

### FRRouting (FRR) as the Network OS

The competition suggested EVE-NG or GNS3 (graphical network simulators), which would have required running full commercial router operating system images — large, licensed, and difficult to automate. FRR is the open-source routing suite that ships inside many commercial routers and runs natively in a Docker container. It implements real OSPF, BGP, LDP, and MPLS — not simplified simulations. Each FRR container uses about 50–150 MB of RAM, which is why all 148 lab containers fit comfortably on a 108 GB / 19-core machine. SNMP polling is done by net-snmp's `snmpd` (IF-MIB / standard OIDs), not by FRR itself: the FRR AgentX sub-agent is not shipped in this image (Alpine's `frr-snmp` package is ABI-mismatched with FRR 10.5.1) and `frr.conf.j2` emits no `agentx` line, so `snmpd`'s AgentX master socket has nothing attached to it (`frr-node/Dockerfile:6-10`, `frr-node/start.sh:48-51`). Telegraf gets the same interface counters and status a real NOC would poll — just from `snmpd` directly, not via an FRR routing-table subagent.

### Containerlab as the Orchestrator

Containerlab is a tool that does for network containers what Docker Compose does for application containers: it reads a YAML file describing nodes and links, creates Docker containers, wires virtual Ethernet interfaces between them, and tears everything down cleanly. The entire 148-node lab topology is defined in a single generated `clab.yml` file. Containerlab also provides the `netem` subcommand used by the fault injectors to add delay, jitter, loss, and rate limiting to any link.

### Code Generation from a Single Spec

All 148 node configurations — FRR config files, SNMP configurations, WireGuard key pairs, QoS scripts — are generated by a Python + Jinja2 generator (`generator/generate.py`) from a single `topology-spec.yaml` file. The spec file contains only the knobs: router counts, BGP AS numbers, address block bases, VRF definitions. Every IP address, every BGP neighbor statement, every MPLS label range is derived algorithmically. This means scaling the lab (for example, doubling the number of branch sites) requires changing one number in one file. It also means the topology is fully reproducible — given the same spec, you always get the same network.

### Per-VRF Host Separation

Each site has one host container per VRF rather than one shared host. This means telemetry from VOICE traffic and CORP traffic appears with separate labels from the start, without any post-processing to separate them. From the ML team's perspective, this produces cleaner training data: a fault that degrades the VOICE VRF shows up clearly in `vrf=VOICE` rows without contaminating `vrf=CORP` rows from the same device.

### VictoriaMetrics + Grafana + Loki

This stack was chosen because it is the de facto standard for cloud-native telemetry and the entire stack runs offline. VictoriaMetrics is a drop-in replacement for Prometheus with better write throughput and smaller disk footprint — important when collecting 30-second interval metrics from 70 FRR routers (SNMP covers all 70 nodes via 70 Telegraf agent entries). The Grafana NOC Overview dashboard ships 11 panels, covering interface SNMP counters, OSPF adjacency state, OSPF SPF duration, MPLS LSP count, and BGP peers established — each panel maps directly to one or more fault scenario classes. Loki stores logs as compressed, indexed streams without requiring a full-text search index per log line, keeping disk usage manageable. Grafana provides the NOC dashboard view. All three run as Docker containers defined in `telemetry/docker-compose.yml`.

### FastAPI as the ML Team Contract

Rather than giving the ML team direct database credentials and expecting them to write PromQL, the project exposes a versioned HTTP API. This has two benefits: the ML team can query data using plain HTTP requests from any language, and the interface is stable even if the underlying storage changes. The `/datasets` endpoint is the primary entry point — it returns a pre-joined, labeled Parquet file that requires no further joining or schema knowledge to use.

---

## 6. What the ML Team Gets

The ML team interacts primarily with the Data API at `http://localhost:8000`. Here is a practical summary of what is available.

**The labeled Parquet dataset** is the main deliverable. Fetch it with:

```python
import requests, pandas as pd, io

r = requests.get("http://localhost:8000/datasets", params={"build": True})
df = pd.read_parquet(io.BytesIO(r.content))
print(df.shape)          # (rows, 40)
print(df.columns.tolist())
```

The Parquet schema has 59 columns per row (`dataapi/export.py:COLUMNS`). Each row represents one 30-second time bucket for one (device, entity) pair, where entity is a network interface, a WireGuard tunnel, or the device itself:

| Column | Type | Description |
|--------|------|-------------|
| `ts` | string | UTC ISO-8601 bucket start timestamp |
| `device` | string | Node name — the join key across all signals |
| `site_type` | string | `branch`, `hub`, or `dc` |
| `vrf` | string | `CORP`, `VOICE`, or `GUEST` |
| `entity` | string | Interface name or tunnel identifier |
| `entity_type` | string | `interface` or `tunnel` |
| `if_in_octets` | float | Bytes received on interface (cumulative counter) |
| `if_out_octets` | float | Bytes sent on interface (cumulative counter) |
| `if_oper_status` | float | Interface operational status (1=up, 2=down) |
| `tunnel_latency_ms` | float | WireGuard tunnel round-trip latency — SIMULATED: measured wg0 RTT + modelled congestion term + netem delay read back from the site's uplink qdisc config, not a live measurement of the fault (`controller/controller.py:9-14,467-469`) |
| `tunnel_jitter_ms` | float | Latency variance — SIMULATED, same composition as above |
| `tunnel_loss_pct` | float | Packet loss percentage on the tunnel — SIMULATED, same composition as above |
| `tunnel_rekeys` | float | WireGuard handshake count (anomalies cluster before failures) |
| `flow_bytes` | float | Total bytes in IPFIX flow records for this bucket |
| `flow_packets` | float | Total packets in IPFIX flow records |
| `is_fault` | bool | True if a fault scenario was active at this timestamp |
| `scenario_id` | string | Unique identifier for the fault run |
| `fault_type` | string | One of the twenty-one scenario names |
| `severity` | float | ordinal magnitude 0.33 / 0.66 / 1.0 (string in `severity_label`) |
| `lead_time_s` | float | Seconds from fault injection start to `t_impact` |
| `time_to_impact_s` | list\<float\> | Seconds remaining until impact at this timestamp, one entry per concurrent fault (element 0 = primary) |

Nineteen further columns, added by the device-health + environmental feature set, are sourced from `telemetry/env-metrics.py` (docker stats + `vtysh` JSON, not SNMP) rather than the four streams above:

| Column | Description |
|--------|-------------|
| `if_in_errors`, `if_in_discards`, `if_out_errors`, `if_out_discards` | Interface error/discard counters. Only `if_out_discards` moves in a container lab — the other three are structurally 0 and reserved for real hardware |
| `q_backlog_bytes`, `q_drops` | Queue backlog and drop counters |
| `xcvr_temp_c`, `xcvr_rx_power_dbm`, `xcvr_tx_bias_ma` | Transceiver diagnostics |
| `cpu_pct`, `mem_pct` | Container CPU/memory percent (control-plane load proxy) |
| `bgp_msg_rx`, `bgp_msg_tx` | BGP messages sent/received (`vtysh show bgp summary json`) |
| `rib_routes` | RIB route count |
| `ospf_lsa_count` | OSPF link-state database entry count (`vtysh show ip ospf database json`) |
| `device_temp_c`, `device_power_watts`, `device_fan_rpm`, `device_psu_voltage_v` | Modelled chassis environmental sensors |
| `topology_id` | Which of the 12 topologies (10 train + 2 held out) the row came from — leave-one-topology-out evaluation |
| `stream` | `F` (fault-dense) or `N` (fault-free + hard-negatives) — the sampler composes fault prevalence from the two |
| `is_hard_negative` | True for near-miss rows (looks like a fault, isn't — `is_fault` stays False) |
| `is_root`, `cascade_parent_id`, `cascade_depth`, `cascade_motif_id`, `affected_entity_count` | Root/affected dual-head cascade supervision — depth 2-3, graph-adjacent propagation |
| `injection_seed` | Per-fault RNG draw, for reproducing one scenario in the air gap |

New companion Parquet files ship alongside the main table: `*_events.parquet` (templated control-plane events, exact ts), `*_topology_edges.parquet` (interval-encoded graph), `*_paths.parquet` (ordered-hop RouteNet paths — `wg_tunnel`/`ospf_spf_path` only, no MPLS dataplane on this WSL2 host). Row key is now `(stream, topology_id, device, entity, ts)`.

The `time_to_impact_s` column is the key ML target for a regression or hazard model. For classification, `is_fault` provides a binary label. For multi-class classification, `fault_type` identifies the primary fault type and `fault_types` the full concurrent set — up to 3 faults overlap on one row in the shipped data. Because the label columns are lists, select precursor rows with `export.precursor_mask(df)` rather than `time_to_impact_s > 0`.

**Raw telemetry endpoints** give the team access to the underlying signals if they need to engineer custom features:

```python
# Query a PromQL expression against VictoriaMetrics via the API
r = requests.get("http://localhost:8000/metrics", params={
    "query": 'sdwan_tunnel_latency_ms{device="ce_branch1"}',
    "start": int(time.time()) - 3600,
    "end": int(time.time()),
    "step": 30
})
# Returns time-series data in Prometheus range query format

# Fetch routing protocol events (BGP ADJCHANGE, OSPF neighbor state)
r = requests.get("http://localhost:8000/events", params={
    "device": "ce_branch1",
    "start": int(time.time()) - 3600
})

# Download the network topology as a graph (nodes + edges JSON)
r = requests.get("http://localhost:8000/topology")
# Useful for graph neural network features
```

**The synthetic dataset**, run as `python3 synthetic/generate.py --days 7 --scale 3`, produces roughly 18.1 million additional labeled rows (899 entities/tick × ticks) calibrated to match the statistical properties of the real lab captures. The two shipped files are one day each (2,589,120 rows, 59 columns) at seeds 42 and 7. It can be concatenated directly with the real data for training:

```python
real = pd.read_parquet("dataapi/datasets/noc_dataset_*.parquet")
synth = pd.read_parquet("synthetic/output/*.parquet")
combined = pd.concat([real, synth], ignore_index=True)
```

**Grafana dashboards** at `172.20.20.51:3000` (login: admin/admin) provide a live visual view of all telemetry signals — useful for sanity-checking that a fault injection produced the expected signature in the data before using those rows for training.

---

## 7. What Is Left to Build (Phases 3–6)

The data infrastructure is complete. Phases 3 through 6 build the intelligence layer on top of it.

**Phase 3 — Predictive Modelling.** Train time-series forecasting models (candidate architectures include LSTM, Temporal Fusion Transformers, and Prophet) against the labeled Parquet dataset. The evaluation criterion is not just accuracy but prediction lead time: how many seconds before `t_impact` can the model raise a confident alert? A model that fires 60 seconds early is worth far more operationally than one that fires 5 seconds early.

**Phase 4 — Offline LLM Deployment.** Select and quantize an open-source LLM (likely Mistral 7B or Phi-3) for local deployment. Package it with its runtime inside the air-gap boundary. Build a RAG (Retrieval-Augmented Generation) pipeline over local artifacts: the network topology graph, NOC runbooks, and historical incident records stored in `ragcorpus/`.

**Phase 5 — Copilot Integration.** Wire predictive model outputs into the LLM's context window via the RAG pipeline. Configure the copilot to produce structured responses: predicted fault type, confidence score, probable root cause, affected sites and services, estimated time to impact, and suggested remediation actions.

**Phase 6 — Scenario Validation.** Run the four mandated fault scenarios (congestion, BGP flap, tunnel degradation, policy drift) plus the three adversarial scenarios through the complete stack, end to end. Measure and report prediction lead time, copilot explanation quality, and remediation accuracy.

This document covers only Phases 1 and 2. The data API is the contract that allows the ML team and the NOC infrastructure team to work in parallel from this point forward.

---

## 8. Quick Start

Bring up the entire environment — network lab, telemetry stack, and data API — with these three commands:

```bash
# 1. Deploy the full 148-container lab topology
cd /root/LAB/topology
sudo containerlab deploy -t clab.yml

# 2. Start the telemetry pipeline (VictoriaMetrics, Grafana, Loki, Telegraf, nfacctd)
cd /root/LAB/telemetry
docker compose up -d

# 3. Start the data API for the ML team
cd /root/LAB/dataapi
uvicorn app:app --host 127.0.0.1 --port 8000
```

After that:

- **Grafana NOC dashboards:** `http://172.20.20.51:3000` (admin/admin)
- **Data API root:** `http://localhost:8000`
- **Download a labeled dataset:** `GET http://localhost:8000/datasets?build=true`
- **Run a fault scenario:** `python3 /root/LAB/faults/orchestrator.py --scenario congestion --target ce_branch1 --severity high`
- **Verify air-gap compliance:** `bash /root/LAB/airgap/verify-airgap.sh` (expected: 14/14 PASS)

To tear down the network topology when done:

```bash
cd /root/LAB/topology && sudo containerlab destroy -t clab.yml
```

---

## Key File Index

| File | Purpose |
|------|---------|
| `/root/LAB/topology-spec.yaml` | Single declarative spec controlling the entire network scale |
| `/root/LAB/generator/generate.py` | Jinja2 generator: spec → all 148 node configs; emits topology-meta.json |
| `/root/LAB/topology/clab.yml` | Generated Containerlab topology file |
| `/root/LAB/controller/controller.py` | SD-WAN path selection + Prometheus metrics |
| `/root/LAB/trafficgen/trafficgen.py` | Diurnal traffic simulation |
| `/root/LAB/faults/orchestrator.py` | Fault injection scheduler + ground-truth label writer |
| `/root/LAB/faults/injectors.py` | Fault primitives: netem, BGP flap, policy drift, MultiLinkFault, OspfCostShift |
| `/root/LAB/dataapi/app.py` | FastAPI endpoints — ML team's primary interface |
| `/root/LAB/dataapi/export.py` | Joins all signals into labeled Parquet |
| `/root/LAB/dataapi/schema/dataset.schema.json` | JSON Schema for the Parquet format |
| `/root/LAB/synthetic/generate.py` | Synthetic data generator (`--days 7 --scale 3` → ~18.1M rows at 899 entities/tick; `--seed` for an episode-disjoint holdout) |
| `/root/LAB/telemetry/docker-compose.yml` | VictoriaMetrics/Grafana/Loki/Telegraf stack |
| `/root/LAB/airgap/verify-airgap.sh` | Air-gap compliance verifier |
| `/root/LAB/problem_statement.md` | Original competition problem statement |
| `/root/LAB/PLAN.md` | Full build plan for all six phases |

---

**Navigation:** [02 Architecture Analogies](02_ARCHITECTURE_ANALOGIES.md) →
