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

Five major components were built:

**A simulated network of 130 containers.** Fifty-two of these are FRR (FRRouting — an open-source network operating system) router containers running real routing protocols. Seventy-eight are host containers representing end-user machines at branch offices, regional hubs, and data centers. Together they form a realistic enterprise-grade network that generates genuine SNMP (Simple Network Management Protocol) counters, routing protocol events, and flow records — the same data a real NOC would see.

**A full telemetry pipeline.** Every metric from every container is collected, normalized, and stored. Interface utilization (bytes in/out per second) goes through SNMP into Telegraf into VictoriaMetrics (a time-series database compatible with Prometheus). Routing events (BGP session changes, OSPF adjacency updates) go through syslog into Promtail into Loki (a log aggregation system). Flow records (which source IP talked to which destination, how many bytes) go through IPFIX (a standard network flow protocol) into nfacctd. All signals share a common `device` label as the join key.

**A fault injection system with ground-truth labels.** Twelve named fault scenarios can be injected into any target device on demand or in a randomized campaign. Each injection writes a JSON label record containing the scenario type, target device, severity level, `t_start`, `t_impact`, and `lead_time_s`. These label files are the supervision signal for model training.

**A FastAPI data API.** A local HTTP server at port 8000 gives the ML team a clean, versioned interface to all of the above. They can query raw metrics, retrieve log events, download flow records, read fault labels, inspect the network topology as a graph, and — most importantly — download a pre-joined, labeled Parquet file that combines all four signal types into a single DataFrame ready for model training.

**A synthetic dataset and air-gap packaging.** Because 130 containers running for a few hours produce limited data at ML scale, a calibrated synthetic generator extends the real captures to 8.89 million rows while preserving realistic statistical properties. The entire software stack is packaged for offline deployment: Docker images are saved as compressed archives, and an automated verifier confirms that a full deployment produces zero outbound traffic to public IP addresses.

---

## 4. Network Architecture Overview

### The Three-Tier Hierarchy

Enterprise networks are typically organized into three layers, and this simulation faithfully reproduces all three.

**Provider core (P and PE routers).** The backbone of the network consists of eight P routers (P stands for Provider — these are the core switches of the carrier network) and ten PE routers (PE stands for Provider Edge — these sit at the boundary between the carrier and the customer). The P routers form a mesh connected by point-to-point links. The PE routers connect to that mesh and also connect outward to customer sites. Each PE has two P uplinks for MPLS underlay failure coverage (dual-homing). BFD (Bidirectional Forwarding Detection) runs at 300ms detect intervals on all core OSPF links, with bfdd active on every P and PE node. MP-BGP uses a route-reflector design: pe1 and pe2 act as route reflectors; pe3–pe10 are clients. This reduces the BGP session count from a 45-session full mesh to 17 sessions.

**Customer Edge routers (CE routers).** These sit at each customer location: 24 branch offices (small, single uplink), 6 regional hubs (larger, higher capacity), and 4 data center sites (server farms). Each CE connects to exactly one PE.

**Host containers.** Behind every CE router are one or more host containers representing the actual end-user machines — PCs, phones, servers — on that site's local network. Each host is isolated in its own VRF (see below), so a CORP-network PC and a VOICE-network phone at the same branch office cannot talk to each other at the IP layer, exactly as in a real enterprise deployment.

### MPLS: The Highway System

MPLS (Multiprotocol Label Switching) is the forwarding technology used in the provider core. Rather than making a routing decision at every hop based on the destination IP address, MPLS assigns each packet a short numeric label at the ingress PE and swaps that label at each P router until the packet reaches the egress PE. Think of it as a highway system with pre-assigned lanes: once a truck enters the highway and is assigned lane 3, every interchange simply reads "lane 3" and directs it forward, without re-examining the cargo manifest at every junction.

This makes the core fast and deterministic. The routing protocols that make MPLS work here are OSPF (Open Shortest Path First — an interior gateway protocol that builds a map of the core network) and LDP (Label Distribution Protocol — which assigns the actual MPLS label values to each path). PE routers also run MP-BGP (Multiprotocol BGP, specifically the VPNv4 address family) to exchange customer route information across the core.

### WireGuard SD-WAN Overlay: The Secure Second Road

On top of the MPLS underlay, the network runs a WireGuard-based SD-WAN (Software-Defined Wide Area Network) overlay. WireGuard is a modern, lightweight VPN (Virtual Private Network) protocol. Every branch and data center CE establishes encrypted WireGuard tunnels to the hub CEs, giving the network ~168 overlay tunnels. Adjacent hub pairs (hub1↔hub2, hub3↔hub4, hub5↔hub6) also get direct hub-hub WireGuard links for resilience. The SD-WAN controller — a Python process — monitors the health of each tunnel in real time, measures per-tunnel latency, jitter, and packet loss, and selects which tunnel each traffic class should use.

This two-layer architecture (MPLS underlay + WireGuard overlay) is the defining characteristic of modern enterprise SD-WAN and is explicitly named in the competition problem statement. The interaction between underlay failures and overlay degradation is where some of the most interesting predictive signals live.

### VRFs: Three Physically Separate Virtual Networks

At each site, traffic is divided into three VRFs (Virtual Routing and Forwarding instances). You can think of a VRF as a completely separate routing table that lives inside the same physical router. Traffic in one VRF cannot cross into another without an explicit policy — they are as isolated as if they were running on separate hardware.

The three VRFs are:

- **CORP** (Corporate): Standard business traffic — file shares, email, enterprise applications. Present at all site types. DSCP class AF31 (a QoS marking that gives it moderate priority on congested links).
- **VOICE**: VoIP (Voice over IP) and real-time communications. Highest priority (DSCP class EF, Expedited Forwarding) and guaranteed 30% of the CE uplink bandwidth. Present at all site types.
- **GUEST**: Internet-access-only traffic for visitors. Lowest priority (best-effort). Present only at hub and data center sites; branch offices do not get guest WiFi in this topology.

### Topology Diagram

```
                          ┌─────── MPLS PROVIDER CORE ───────┐
                          │                                   │
                   ┌──────┴──────┐                     ┌─────┴──────┐
                   │    P1-P8    │◄────── OSPF+LDP ────│  P1-P8     │
                   │  (8 P-core  │     full mesh       │  (same set)│
                   │   routers)  │     BFD 300ms       └─────┬──────┘
                   └──────┬──────┘                           │
                          │                                   │
             ┌────────────┼────────────┐                     │
             │            │            │                      │
          ┌──┴──┐      ┌──┴──┐      ┌──┴──┐   ← 10 PE routers (RR: pe1+pe2; clients: pe3-pe10)
          │ PE1 │      │ PE2 │ ...  │PE10 │
          └──┬──┘      └──┬──┘      └──┬──┘
             │            │            │
     ┌───────┼────┐  ┌────┼───┐  ┌────┼───┐
     │       │    │  │    │   │  │    │   │
  branch  branch hub  dc  hub  dc branch branch
  CE1-16   ...  CE1-4 CE1-4 ...  ...   ...  ...
     │            │
     │  WireGuard │  (hub-spoke overlay, 80 tunnels)
     └────────────┘
     
  Each CE site:
  ┌─────────────────────────────────────────────────┐
  │  CE router (FRR)                                │
  │   ├─ vrf_CORP  ──► host_corp  (192.168.x.0/24) │
  │   ├─ vrf_VOICE ──► host_voice (192.168.x.1/24) │
  │   └─ vrf_GUEST ──► host_guest (hub/dc only)    │
  └─────────────────────────────────────────────────┘
  
  Telemetry flow:
  FRR routers ──SNMP──► Telegraf ──► VictoriaMetrics (PromQL)
  FRR syslogs ──────────────────► Loki (log queries)
  Flow records ──IPFIX──► nfacctd ──► SQLite / API
  Controller  ──────────────────► Prometheus metrics
                                        │
                               FastAPI Data API :8000
                                        │
                               ML team / model training
```

---

## 5. Design Decisions and Why

### FRRouting (FRR) as the Network OS

The competition suggested EVE-NG or GNS3 (graphical network simulators), which would have required running full commercial router operating system images — large, licensed, and difficult to automate. FRR is the open-source routing suite that ships inside many commercial routers and runs natively in a Docker container. It implements real OSPF, BGP, LDP, and MPLS — not simplified simulations. Each FRR container uses about 50–150 MB of RAM, which is why 130 of them fit comfortably on a 108 GB machine. Because FRR supports AgentX (a protocol extension that lets FRR publish its routing tables over SNMP), the same SNMP polling that a real NOC uses against production routers works unchanged against these containers.

### Containerlab as the Orchestrator

Containerlab is a tool that does for network containers what Docker Compose does for application containers: it reads a YAML file describing nodes and links, creates Docker containers, wires virtual Ethernet interfaces between them, and tears everything down cleanly. The entire 130-node topology is defined in a single generated `clab.yml` file. Containerlab also provides the `netem` subcommand used by the fault injectors to add delay, jitter, loss, and rate limiting to any link.

### Code Generation from a Single Spec

All 90 node configurations — FRR config files, SNMP configurations, WireGuard key pairs, QoS scripts — are generated by a Python + Jinja2 generator (`generator/generate.py`) from a single `topology-spec.yaml` file. The spec file contains only the knobs: router counts, BGP AS numbers, address block bases, VRF definitions. Every IP address, every BGP neighbor statement, every MPLS label range is derived algorithmically. This means scaling the lab (for example, doubling the number of branch sites) requires changing one number in one file. It also means the topology is fully reproducible — given the same spec, you always get the same network.

### Per-VRF Host Separation

Each site has one host container per VRF rather than one shared host. This means telemetry from VOICE traffic and CORP traffic appears with separate labels from the start, without any post-processing to separate them. From the ML team's perspective, this produces cleaner training data: a fault that degrades the VOICE VRF shows up clearly in `vrf=VOICE` rows without contaminating `vrf=CORP` rows from the same device.

### VictoriaMetrics + Grafana + Loki

This stack was chosen because it is the de facto standard for cloud-native telemetry and the entire stack runs offline. VictoriaMetrics is a drop-in replacement for Prometheus with better write throughput and smaller disk footprint — important when collecting 30-second interval metrics from 130 nodes. Loki stores logs as compressed, indexed streams without requiring a full-text search index per log line, keeping disk usage manageable. Grafana provides the NOC dashboard view. All three run as Docker containers defined in `telemetry/docker-compose.yml`.

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
print(df.shape)          # (rows, 21)
print(df.columns.tolist())
```

The Parquet schema has 21 columns per row. Each row represents one 30-second time bucket for one (device, entity) pair, where entity is either a network interface or a WireGuard tunnel:

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
| `tunnel_latency_ms` | float | WireGuard tunnel round-trip latency in milliseconds |
| `tunnel_jitter_ms` | float | Latency variance (jitter) in milliseconds |
| `tunnel_loss_pct` | float | Packet loss percentage on the tunnel |
| `tunnel_rekeys` | float | WireGuard handshake count (anomalies cluster before failures) |
| `flow_bytes` | float | Total bytes in IPFIX flow records for this bucket |
| `flow_packets` | float | Total packets in IPFIX flow records |
| `is_fault` | bool | True if a fault scenario was active at this timestamp |
| `scenario_id` | string | Unique identifier for the fault run |
| `fault_type` | string | One of the twelve scenario names |
| `severity` | string | `low`, `medium`, or `high` |
| `lead_time_s` | float | Seconds from fault injection start to `t_impact` |
| `time_to_impact_s` | float | Seconds remaining until impact at this timestamp |

The `time_to_impact_s` column is the key ML target for a regression model. For classification, `is_fault` provides a binary label. For multi-class classification, `fault_type` identifies which of the twelve fault types is active.

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

**The synthetic dataset** provides 8.89 million additional labeled rows in the exact same schema, calibrated to match the statistical properties of the real lab captures. It can be concatenated directly with the real data for training:

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
# 1. Deploy the full 130-container network topology
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
| `/root/LAB/generator/generate.py` | Jinja2 generator: spec → all 130 node configs |
| `/root/LAB/topology/clab.yml` | Generated Containerlab topology file |
| `/root/LAB/controller/controller.py` | SD-WAN path selection + Prometheus metrics |
| `/root/LAB/trafficgen/trafficgen.py` | Diurnal traffic simulation |
| `/root/LAB/faults/orchestrator.py` | Fault injection scheduler + ground-truth label writer |
| `/root/LAB/faults/injectors.py` | Six fault primitives (netem, BGP flap, policy drift, etc.) |
| `/root/LAB/dataapi/app.py` | FastAPI endpoints — ML team's primary interface |
| `/root/LAB/dataapi/export.py` | Joins all signals into labeled Parquet |
| `/root/LAB/dataapi/schema/dataset.schema.json` | JSON Schema for the Parquet format |
| `/root/LAB/synthetic/generate.py` | 8.89M-row synthetic data generator |
| `/root/LAB/telemetry/docker-compose.yml` | VictoriaMetrics/Grafana/Loki/Telegraf stack |
| `/root/LAB/airgap/verify-airgap.sh` | Air-gap compliance verifier |
| `/root/LAB/problem_statement.md` | Original competition problem statement |
| `/root/LAB/PLAN.md` | Full build plan for all six phases |

---

**Navigation:** [02 Architecture Analogies](02_ARCHITECTURE_ANALOGIES.md) →
