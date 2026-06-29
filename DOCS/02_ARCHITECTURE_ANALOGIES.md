# Architecture Deep-Dive: The NOC Copilot Lab Explained with Analogies

**Target reader:** AI/ML researcher comfortable with Python and machine learning, but new to enterprise networking.

**See also:** [01 Project Overview](01_PROJECT_OVERVIEW.md) — context and motivation | [05 Technical Glossary](05_TECHNICAL_GLOSSARY.md) — term definitions

If you have ever wondered what the heck "MPLS" or "BGP VPNv4" means and why anyone would run 130 containers to simulate it — this document is for you. Every concept is introduced as something familiar first. The networking jargon comes second, as a label you can attach to the mental model you already built.

---

## The Big Picture: An ASCII Map

Before diving into individual pieces, here is the whole system in one diagram. Read top-to-bottom: physical containers on the left, telemetry pipeline on the right, ML data API at the bottom.

```
  ┌──────────────────────────────────────────────────────────────────────┐
  │                    THE 130-CONTAINER LAB (single Linux host)         │
  │                                                                      │
  │   ┌────────┐   ┌────────┐   ┌────────┐   ┌────────┐   ┌────────┐   │
  │   │  p1    │───│  p2    │───│  p3    │───│  p4    │───│p5..p8  │   │
  │   │ (MPLS  │   │ core   │   │ core   │   │ core   │   │ core)  │   │
  │   └───┬────┘   └───┬────┘   └───┬────┘   └───┬────┘   └───┬────┘   │
  │       │            │            │            │            │          │
  │   ┌───┴────────────┴────────────┴────────────┴────────────┴────┐    │
  │   │          pe1  pe2  pe3  ...  pe10  (MP-BGP VPNv4 RR mesh)  │    │
  │   └───┬──────────────────────────────────────────────────────┬─┘    │
  │       │  eBGP per VRF                                        │      │
  │  ┌────┴─────────────────────────────────────────────────┐    │      │
  │  │  24x ce_branch  6x ce_hub  4x ce_dc   (CE routers)  │    │      │
  │  │  + 78 host containers (one per site+VRF combination) │    │      │
  │  └────────────────────────────────────────────────────┬─┘    │      │
  │              WireGuard SD-WAN overlay (~168 tunnels)  │      │      │
  │                        hub-spoke topology             │      │      │
  └───────────────────────────────────────────────────────┼──────┘      │
                                                          │
  ┌───────────────── TELEMETRY PIPELINE ─────────────────▼──────────────┐
  │                                                                      │
  │  SNMP (30s) ──► Telegraf ──────────────────────────────────────────►│
  │  Syslog     ──► Promtail ──► Loki (172.20.20.54:3100)              │
  │  IPFIX/NetFlow► nfacctd   (172.20.20.53)                           │
  │  Controller ──► Prometheus ► VictoriaMetrics (172.20.20.50:8428)   │
  │                                              │                       │
  │                              Grafana (172.20.20.51:3000)            │
  │                                                                      │
  │  Fault Orchestrator ──► labels/labels.jsonl (ground truth)          │
  │                                                                      │
  └─────────────────────────────┬────────────────────────────────────────┘
                                │  JOIN on "device"
                                ▼
                    ┌─────────────────────────┐
                    │  FastAPI Data API :8000  │
                    │  /metrics /events /flows │
                    │  /labels /topology       │
                    │  /datasets  → Parquet    │
                    └─────────────────────────┘
                                │
                                ▼
                    ┌─────────────────────────┐
                    │   ML TEAM               │
                    │   21-column Parquet      │
                    │   8.89M rows             │
                    │   is_fault, lead_time_s  │
                    └─────────────────────────┘
```

Now let's walk through each layer.

---

## 1. The Whole Network as a City

Imagine a metropolitan area: a downtown core, regional hubs, suburban branch offices, and datacenters on the city outskirts.

**The city = one enterprise's network.** Traffic (data) flows between locations just like commuters flow between buildings. The streets and highways are shared infrastructure — no single company owns them. A private company rents capacity on those streets to connect its own offices.

In our lab:

- **The highway system (P routers — p1 through p8):** Eight routers that form the high-speed core. They only care about moving traffic as fast as possible. They do not know anything about which company the traffic belongs to or where it is ultimately going. They just pass packets forward.

- **The highway on-ramps (PE routers — pe1 through pe10):** Ten routers that sit at the boundary between the highway system and the customer's private road network. When your company's traffic enters here, the on-ramp stamps it with a label ("this belongs to Company A, destination downtown") and hands it to the highway.

- **The office buildings (CE routers — 24 of them):** Customer Edge routers, one per site. These are the company's own equipment — the building's front door. 16 branch offices, 4 regional hubs, 4 datacenters. Each CE connects to one PE via a private link.

- **The departments inside each building (VRFs):** Each site has separate networks for different groups of people. A visitor on the guest wifi cannot wander into the HR server. This isolation is implemented as VRFs (Virtual Routing and Forwarding) — three of them: **CORP** (staff computers), **VOICE** (IP phones), **GUEST** (visitor wifi). Even though they share the same physical hardware, they behave as completely separate networks.

```
  ce_branch1
  ├── host_branch1_CORP  ─── 192.168.0.0/24   (staff laptops)
  ├── host_branch1_VOICE ─── 192.168.1.0/24   (IP phones)
  └── (no GUEST at branch — branch offices don't have visitor lounges)

  ce_hub1
  ├── host_hub1_CORP     ─── 192.168.16.0/24
  ├── host_hub1_VOICE    ─── 192.168.17.0/24
  └── host_hub1_GUEST    ─── 192.168.18.0/24
```

The topology is declared in a single file (`topology-spec.yaml`) with plain numeric knobs:

```yaml
knobs:
  p_count:      8    # highway core routers
  pe_count:     10   # on-ramp routers (pe1+pe2 = Route Reflectors)
  branch_count: 24   # small branch offices
  hub_count:    6    # regional hubs
  dc_count:     4    # datacenters
# Total containers: 8 + 10 + 34 + 78 = 130
```

A Jinja2 generator reads those numbers and automatically derives every IP address, BGP AS number, and config file for all 130 nodes. Change one number, regenerate, redeploy — the whole city resizes.

---

## 2. MPLS: The Labeled Package Sorting Facility

Think about how a package delivery company like FedEx works. When you drop off a package, a clerk reads the address *once*, slaps a barcode on it, and from that point forward no one reads the address again. Every sorting station just scans the barcode and sends it to the correct conveyor belt. The package moves through the facility in seconds because the routing decision was made upfront.

Normal internet routing (IP routing) is the opposite: every single router along the path reads the full destination address and independently decides where to forward the packet. That is like a package that gets re-read and re-addressed at every sorting station.

**MPLS (Multi-Protocol Label Switching)** is the barcode system for networks:

1. When traffic enters the network at a PE router (the on-ramp), the PE looks at the destination and attaches a tiny **4-byte label** to the front of the packet.
2. The P routers (the sorting stations in the core) **only look at the label**. They never open the packet. They just check a local table: "label 1234 in → swap to label 5678, send out port 2." This is called **label swapping**.
3. When the packet reaches the far PE router (the destination on-ramp), the label is removed and the original packet is delivered to the customer.

```
WITHOUT MPLS (IP routing at every hop):
  Packet: [IP hdr: src=192.168.0.10, dst=10.255.5.1] [payload]
  P1 reads full IP header, looks up routing table → forward to P2
  P2 reads full IP header, looks up routing table → forward to PE4
  PE4 reads full IP header, delivers to CE

WITH MPLS (label switched path):
  PE1: [label:1024 | label:2048 | IP hdr] [payload]   ← two labels pushed (VPN label + transport label)
  P2:  [label:3001 | label:2048 | IP hdr] [payload]   ← outer label swapped, inner unchanged
  P4:  [label:2048 | IP hdr] [payload]                ← outer label popped (PHP)
  PE4: [IP hdr] [payload]                              ← inner VPN label popped, deliver to VRF
```

**How do routers know which label to assign to which destination?** That is the job of **LDP (Label Distribution Protocol)** — it is the label printing press. LDP runs between all P and PE routers in the core. Each router announces: "for packets heading toward my loopback address `10.255.2.3`, use label 4567." Every neighbor learns this and builds its own forwarding table. No human configuration needed — LDP discovers everything automatically.

From `topology-spec.yaml`:
```yaml
underlay:
  mode: mpls
  igp:  ospf
  transport: ldp    # LDP on all core P-P and P-PE links
```

---

## 3. VPNv4 / L3VPN: Private Company Intranets on Shared Highways

Here is a tricky situation: two companies (call them Acme Corp and Globex Inc.) both rent highway capacity from the same provider. They both have an office in City A and an office in City B, connected through the same highway core. How does the provider make sure Acme's traffic never gets mixed up with Globex's traffic?

The answer is **private labeled exit signs**. Each company gets its own set of exit signs with labels that only that company's trucks can read. Acme's truck follows Acme signs. Globex's truck follows Globex signs. They share the same physical highway but travel in completely separate logical lanes with no way to accidentally merge.

In networking terms, this is **MPLS L3VPN** with **MP-BGP VPNv4**:

- Each PE router maintains separate **VRF (Virtual Routing and Forwarding)** tables, one per customer (or in our case, one per service class: CORP, VOICE, GUEST).
- The VRF is like a separate routing table — a completely isolated IP address space. The same IP address can appear in CORP and VOICE without conflict, because they live in different VRFs.
- **Route Distinguisher (RD):** Every VPN route gets an extra tag prepended to make it globally unique, even if two customers use the same IP range. `65000:10` for CORP, `65000:20` for VOICE, `65000:30` for GUEST.
- **Route Target (RT):** Controls which VRFs "import" which routes — which trucks are allowed to follow which exit signs. A CORP route is only imported by CORP VRFs at other PE routers.
- **MP-BGP VPNv4:** The protocol that carries VPN routes between PE routers across the MPLS core. All 10 PE routers exchange VPNv4 prefixes via Route Reflectors: pe1+pe2 act as RR servers; pe3–pe10 are RR clients that peer only with pe1+pe2 — 17 sessions instead of 45 in a full mesh.

```
PE1 VRF CORP: knows 192.168.0.0/24 (branch1) and 192.168.4.0/24 (branch2)...
PE3 VRF CORP: advertises 192.168.16.0/24 (hub1_CORP) with RD=65000:10

PE1 receives PE3's advertisement → installs in VRF CORP table only
→ CORP traffic from branch1 can reach hub1
→ VOICE traffic from branch1 CANNOT reach hub1's CORP subnet (different VRF)
```

From the spec:
```yaml
vrfs:
  CORP:
    rd_community: "65000:10"
    dscp_class: AF31
  VOICE:
    rd_community: "65000:20"
    dscp_class: EF
  GUEST:
    rd_community: "65000:30"
    dscp_class: BE
    sites: [hub, dc]   # branches don't get GUEST — no visitor lounges
```

---

## 4. BGP: The Internet's GPS Consensus Protocol

Imagine a network of competing GPS companies — Apple Maps, Google Maps, Waze, and a dozen others. Each company knows the roads in its own territory really well. They periodically share summaries with each other: "Hey Google Maps, if you want to route people to downtown Seattle, send them through our network — we have a direct highway." Google Maps might accept this offer, or it might reject it because it has a policy against routing traffic through competitors.

That negotiation — "I can reach X, go through me" — plus the policies about what to accept and what to reject, is exactly what **BGP (Border Gateway Protocol)** does.

BGP has two flavors in our lab:

**iBGP (internal BGP):** Sessions between routers within the provider's own network (all in AS 65000). PE routers share VPNv4 routes via Route Reflectors (pe1+pe2 as RR servers, pe3–pe10 as clients) — PE1 tells PE3: "I know how to reach the CORP subnet at branch1."

**eBGP (external BGP):** Sessions between different autonomous systems. Each CE router has its own BGP AS number (branch sites: AS 65101–65116, hubs: AS 65201–65204, datacenters: AS 65301–65304). When ce_branch1 (AS 65101) wants to tell the provider's pe1 (AS 65000) about its local subnet, it sends an eBGP advertisement: "I can reach 192.168.0.0/24, come through me."

Here is a real snippet of what that looks like in FRR (the open-source router software running in each container):

```
# ce_branch1 FRR config (generated)
router bgp 65101
  neighbor 10.1.0.1 remote-as 65000        # eBGP session to pe1's CORP VRF interface
  !
  address-family ipv4 unicast
    network 192.168.0.0/24                  # advertise CORP LAN
  exit-address-family
```

The **BGP flap fault** in this lab simulates what happens when BGP sessions disconnect and reconnect rapidly — the network equivalent of a GPS company going offline and coming back, causing everyone to recalculate routes repeatedly:

```python
# from faults/orchestrator.py
def scen_bgp_flap(target, severity, duration):
    """BGP/OSPF adjacency FLAP: repeated session resets → route reconvergence churn"""
    count = max(2, int(4 * s))
    injector = inj.BgpFlap(target, count=count, gap_seconds=6.0)
```

The signature in telemetry: burst of `BGP: %ADJCHANGE` messages in Loki (the log database), accompanied by transient withdrawal and re-advertisement of prefixes.

---

## 5. OSPF: The Local Street Map

Before any of the BGP magic can happen, all the P and PE routers need to find each other. How does pe1 know how to send a packet to pe4 when they are not directly connected?

Think of OSPF as the city's internal street directory — the kind that every city employee carries. When a new road opens, every employee automatically gets an updated directory. When a road closes, the directory updates within seconds. The directory lets every employee find any other location in the city without needing a central dispatcher.

**OSPF (Open Shortest Path First)** is a link-state routing protocol. Every router:
1. Announces all its directly connected links and their costs ("I am pe1, I connect to p1 via a link costing 10, and to p3 via a link costing 10").
2. Floods these announcements to all neighbors.
3. Builds a complete map of the entire network.
4. Independently runs Dijkstra's shortest-path algorithm to find the best path to every destination.

In our lab, OSPF runs in **area 0** (the backbone area) across all P-P and P-PE links. The loopback interfaces (`10.255.x.x/32`) are advertised into OSPF — this matters because BGP sessions between PEs use these loopback addresses as stable identifiers, and OSPF is what makes those loopbacks reachable.

```
OSPF Area 0 participants:
  p1  (10.255.1.1/32)
  p2  (10.255.1.2/32)
  p3  (10.255.1.3/32)
  p4  (10.255.1.4/32)
  p5  (10.255.1.5/32)
  p6  (10.255.1.6/32)
  p7  (10.255.1.7/32)
  p8  (10.255.1.8/32)
  pe1 (10.255.2.1/32) ─── RR server; pe3–pe10 peer here (17 iBGP sessions total)
  pe2 (10.255.2.2/32) ─── RR server
  ...
```

OSPF convergence (time to re-route around a failed link) is typically under 10 seconds for small topologies like ours. It is the foundation that everything else sits on: no OSPF → no LDP reachability → no MPLS paths → no VPNv4 → nothing works.

---

## 6. SD-WAN Overlay: The Express Toll Lane on Top of the Highway

Here is a scenario: the regular MPLS highway is congested at rush hour. Wouldn't it be nice if there was a separate express lane — encrypted, monitored in real-time, with an intelligent controller that could say "traffic to branch7 is backing up on hub1, reroute through hub2"?

That is exactly what the **SD-WAN overlay** is. It is a second network that runs *on top of* the MPLS underlay. Think of it as express toll lanes built on top of existing highways, managed by a private company with its own traffic management system.

In our lab:

- **WireGuard tunnels** are the express lanes — encrypted point-to-point tunnels between sites. There are **~168 tunnels** in a hub-spoke arrangement, plus hub-hub direct links between adjacent hub pairs (hub1↔hub2, hub3↔hub4, hub5↔hub6).
- The **6 hub CEs** (ce_hub1 through ce_hub6) act as regional airports: every branch and datacenter connects through them.
- Each spoke (branch or datacenter) connects to **2 hubs** (round-robin assignment), giving redundancy.
- The **SD-WAN controller** (`controller/controller.py`) is the traffic management system. Every 5 seconds it measures latency, jitter, and packet loss on every tunnel, then decides which hub path each VRF should use.

```
Hub-spoke topology:
                    ┌──────────────┐
   ce_branch1 ─────►  ce_hub1     ├──── (MPLS core) ──── ce_dc1
   ce_branch2 ─────►  (primary)   │
   ...                            │
   ce_branch16────►  ce_hub2      ├──── (MPLS core) ──── ce_dc4
   ce_dc1    ─────►  (secondary)  │
   ...                            │
                    └──────────────┘
  Each spoke has TWO tunnels (round-robin hub assignment) for redundancy
  ~168 tunnels total = (24 branches + 4 DCs) × 2 hubs + 6 hub-hub links
```

The controller's path selection logic applies hysteresis (to avoid flapping between paths) and per-VRF preferences:

```python
# from controller/controller.py
VRF_PREFERRED_HUB = {"CORP": "ce_hub1", "VOICE": "ce_hub1", "GUEST": "ce_hub2"}

FAILOVER_LOSS_PCT     = 5.0   # switch hubs if loss exceeds 5%
FAILOVER_LATENCY_MULT = 3.0   # or if latency exceeds 3× baseline

def score(t):
    return t.loss_pct * 10.0 + t.latency_ms   # lower is better
```

The controller also models **diurnal traffic patterns** — usage rises during business hours (simulated 9am peak compressed into a 1-hour cycle), causing latency and jitter to climb naturally even without injected faults. This creates realistic background variation for the ML model to learn from.

---

## 7. QoS: The Priority Boarding System

Airlines figured out long ago that not all passengers are equal — a first-class passenger who pays five times as much should not be stuck behind a herd of economy passengers boarding simultaneously. So they invented priority boarding: first class boards first, business class second, everyone else waits.

Networks face the same problem. A voice call is extremely sensitive to delay (even 200ms makes a conversation feel like a satellite phone call), but a bulk file transfer does not care if it finishes in 3 seconds or 5 seconds. If a voice packet and a file-transfer packet arrive at the same router at the same instant and compete for the same outgoing slot, the router needs to know which one to send first.

**QoS (Quality of Service)** is the airline's priority system for packets:

- Packets get stamped with a **DSCP (Differentiated Services Code Point)** marking — a 6-bit field in the IP header. Think of it as a colored luggage tag.
  - **EF (Expedited Forwarding, DSCP 46) = First Class** — Voice packets. Always board first. Never wait behind bulk traffic.
  - **AF31 (Assured Forwarding, DSCP 26) = Business Class** — Corporate data. Gets guaranteed bandwidth, reasonable priority.
  - **BE (Best Effort, DSCP 0) = Economy** — Guest wifi traffic. Gets whatever bandwidth is left over.

- **HTB (Hierarchical Token Bucket)** queuing enforces these priorities on the CE router's uplink interface. It acts like the boarding gate agent who physically holds back economy passengers until first class has boarded:

```yaml
# from topology-spec.yaml
qos:
  mechanism: htb_dscp
  classes:
    - vrf: VOICE
      dscp: EF          # DSCP 46
      bandwidth_pct: 30  # guaranteed 30% of uplink — always available for voice
      burst_pct:     10
    - vrf: CORP
      dscp: AF31        # DSCP 26
      bandwidth_pct: 50  # 50% guaranteed for business traffic
    - vrf: GUEST
      dscp: BE          # DSCP 0
      bandwidth_pct: 20  # gets only what's left
```

This matters for fault detection: when a **congestion fault** is injected, VOICE traffic should degrade last (it has priority). The ML model that predicts faults should learn this asymmetry — CORP metrics will degrade before VOICE metrics hit threshold.

---

## 8. The Telemetry Pipeline: The Airport's Sensor Grid

A modern international airport has hundreds of sensors — gate occupancy counters, baggage belt speed monitors, security queue cameras, aircraft fuel sensors, weather stations. All of this data feeds into a central operations center. When the system notices that security queue at gate B12 has grown from 20 people to 80 people in 10 minutes, it alerts operations *before* the flight is missed so they can open additional lanes.

Our network lab has exactly the same structure. Four separate sensor systems feed into a central store:

```
┌─────────────────────────────────────────────────────────────────────┐
│                    FOUR TELEMETRY STREAMS                           │
│                                                                     │
│  STREAM 1: SNMP (interface counters — bytes in/out, link status)   │
│  Every router exposes an SNMP agent.                               │
│  Telegraf polls EVERY router EVERY 30 SECONDS                      │
│  → pushes to VictoriaMetrics (172.20.20.50:8428)                   │
│  Metric names: interface_ifHCInOctets, interface_ifHCOutOctets,    │
│                interface_ifOperStatus                               │
│                                                                     │
│  STREAM 2: Syslog (router log messages)                             │
│  Routers emit log lines: "BGP: %ADJCHANGE: neighbor X Down"        │
│  Promtail (172.20.20.55) collects and ships to Loki (172.20.20.54) │
│  → full-text searchable log database, queryable by device/time     │
│                                                                     │
│  STREAM 3: IPFIX / NetFlow (who sent how many bytes to whom)       │
│  PE and CE routers sample packet flows and export metadata         │
│  nfacctd (172.20.20.53) receives UDP datagrams on port 4739        │
│  → flow records: src_ip, dst_ip, bytes, packets, device           │
│                                                                     │
│  STREAM 4: SD-WAN Controller (per-tunnel overlay metrics)          │
│  controller.py serves Prometheus exposition at 172.20.20.56:9362   │
│  Telegraf scrapes every 5s → VictoriaMetrics                       │
│  Metrics: sdwan_tunnel_latency_ms, sdwan_tunnel_jitter_ms,         │
│           sdwan_tunnel_loss_pct, sdwan_tunnel_rekeys_total         │
└─────────────────────────────────────────────────────────────────────┘
```

**The join key:** Every single metric in every single stream is tagged with a `device` label — the router's name, like `ce_branch1` or `pe3`. This is what lets the Data API stitch all four streams together into one unified row per device per time bucket. It is the patient ID that makes a hospital record coherent.

The telemetry stack is declared in `telemetry/docker-compose.yml`:

```yaml
services:
  victoriametrics:   # time-series database (like InfluxDB but faster)
    networks:
      clab:
        ipv4_address: 172.20.20.50

  grafana:           # visualization dashboards (NOC screens)
    ipv4_address: 172.20.20.51

  telegraf:          # the collection agent — polls SNMP, scrapes Prometheus
    ipv4_address: 172.20.20.52

  nfacctd:           # flow collector (IPFIX/NetFlow → structured records)
    ipv4_address: 172.20.20.53

  loki:              # log aggregation database
    ipv4_address: 172.20.20.54

  promtail:          # log shipper (reads syslog from routers, sends to Loki)
    ipv4_address: 172.20.20.55

  controller:        # SD-WAN controller, serves tunnel metrics at :9362
    ipv4_address: 172.20.20.56
```

All services share the same Docker network as the lab containers (`172.20.20.0/24`), so they can reach the routers directly with no NAT or firewall in the way.

---

## 9. Fault Injection: The Fire Drill

Fire departments do not wait for a real fire to test their response. They run scheduled drills — controlled burns, timed evacuations — so that when a real fire breaks out, the response is practiced and the outcome is known. They also keep a log: "Drill #47, Building C, 14:32 UTC, smoke detected at 14:34 UTC, building evacuated by 14:41 UTC."

Our fault injection system is exactly that. The **fault orchestrator** (`faults/orchestrator.py`) schedules controlled "fires" in the network and writes a precise log of when each fire started and when it spread. This log is the **ground truth** that the ML model trains on.

The tool that actually breaks things is **netem** (network emulator) — a Linux kernel feature that can inject artificial delay, jitter, packet loss, and bandwidth limits onto any network interface:

```bash
# What the injector runs inside a container to create a congestion fault:
tc qdisc add dev eth1 root netem delay 56ms 14ms loss 4.2%
#                               ^^^^^^ delay  ^^^^^ loss%
# This makes the router's uplink behave as if it is suffering from congestion
```

Seven fault scenarios are implemented:

| Fault Type | What it simulates | Signature in telemetry |
|---|---|---|
| `congestion` | Progressive packet loss and latency ramp on a CE uplink | Tunnel latency climbs first, then loss follows |
| `bgp_flap` | Repeated BGP session resets | Burst of ADJCHANGE logs in Loki; prefix withdrawal/readvertisement |
| `tunnel_degrade` | WireGuard tunnel degradation + rekey clustering | tunnel_loss_pct climbs; rekeys cluster |
| `policy_drift` | SD-WAN controller misconfiguration (local-pref drop) | BGP route selection shifts, Loki soft-clear events |
| `node_failure` | BGP daemon killed (watchfrr auto-restarts it) | Brief prefix withdrawal then recovery |
| `asymmetric_loss` | Loss only on egress direction, not ingress | Loss high, latency near-normal — hard to diagnose |
| `brownout` | Hard bandwidth cap (queue builds but no netem delay) | Queueing latency climbs, loss appears late |

**The ground-truth label schema** — what the ML team trains on:

```python
# A label row in labels/labels.jsonl (one per fault scenario run):
{
    "scenario_id":    "congestion-ce_branch1-a3f2c1d0",
    "type":           "congestion",
    "target":         {"device": "ce_branch1", "interface": "eth1"},
    "severity":       "high",
    "t_start":        "2026-06-21T08:00:00Z",   # when netem was applied
    "t_impact":       "2026-06-21T08:01:23Z",   # when telemetry threshold crossed
    "t_end":          "2026-06-21T08:02:30Z",   # when fault was reverted
    "lead_time":      83.0,                      # seconds: t_impact - t_start
    "device":         "ce_branch1"               # join key to telemetry
}
```

The `lead_time` field is the prize: it tells the ML model how many seconds in advance the early warning signals appeared before the fault became user-visible. The goal is to detect faults *before* `t_impact` — during the `lead_time` window — which is why columns like `lead_time_s` and `time_to_impact_s` appear in the final dataset.

The orchestrator also supports a **campaign mode** — a Poisson-arrival process that fires faults randomly across all 24 CE nodes with realistic inter-arrival gaps, creating a realistic mix of concurrent and sequential incidents for training:

```python
# from faults/orchestrator.py
def run_campaign(total_duration, mean_gap=120, seed=None):
    """Poisson-arrival campaign: ~1 fault per 2 minutes on average.
       Seed makes runs reproducible for ML dataset versioning."""
```

---

## 10. The Data API: The Clean Lab Report

When a doctor orders a blood test, they do not read the raw output of a mass spectrometer. The lab takes that raw machine output, normalizes it against reference ranges, formats it as a standard report with patient ID, test date, flagged abnormals, and units. The doctor sees one coherent document.

The **FastAPI Data API** at `localhost:8000` does the same thing for this lab. It takes four raw telemetry streams from four different systems (VictoriaMetrics, Loki, nfacctd, and the label file), joins them all on the `device` key, and returns a clean Parquet file with 21 canonical columns.

**Available endpoints:**

| Endpoint | What it returns |
|---|---|
| `GET /metrics?query=...` | PromQL passthrough to VictoriaMetrics |
| `GET /events?device=ce_branch1` | Syslog/event rows from Loki |
| `GET /flows?device=ce_branch1` | IPFIX flow records from nfacctd |
| `GET /labels` | Ground-truth fault timeline |
| `GET /topology` | Network graph as JSON (nodes + links) |
| `GET /datasets?build=true` | Joined, labeled Parquet — the main ML input |

**The 21-column Parquet schema:**

```python
# from dataapi/export.py
COLUMNS = [
    # Identifiers (join keys)
    "ts",             # UTC timestamp (bucket-aligned, 30s steps)
    "device",         # e.g. "ce_branch1" — joins ALL streams
    "site_type",      # "branch" | "hub" | "dc"
    "vrf",            # "CORP" | "VOICE" | "GUEST"
    "entity",         # interface name or tunnel name
    "entity_type",    # "interface" | "tunnel"

    # Interface metrics (SNMP stream)
    "if_in_octets",   # bytes received on interface since last poll
    "if_out_octets",  # bytes sent
    "if_oper_status", # 1=up, 2=down

    # Tunnel metrics (SD-WAN controller stream)
    "tunnel_latency_ms",
    "tunnel_jitter_ms",
    "tunnel_loss_pct",
    "tunnel_rekeys",   # WireGuard rekey count (spikes under stress)

    # Flow metrics (IPFIX stream)
    "flow_bytes",
    "flow_packets",

    # Ground truth (fault label join)
    "is_fault",           # True/False — is this row during a fault?
    "scenario_id",        # links back to labels.jsonl
    "fault_type",         # "congestion" | "bgp_flap" | etc.
    "severity",           # "low" | "medium" | "high"
    "lead_time_s",        # seconds from fault_start to t_impact
    "time_to_impact_s",   # seconds from this row's ts to t_impact (negative = after impact)
]
```

**Reading the dataset in Python — a minimal example:**

```python
import pandas as pd
import requests

# Download the latest labeled Parquet from the Data API
response = requests.get("http://localhost:8000/datasets")
with open("/tmp/noc_dataset.parquet", "wb") as f:
    f.write(response.content)

df = pd.read_parquet("/tmp/noc_dataset.parquet")

print(df.shape)            # (rows, 21)
print(df.dtypes)
print(df["fault_type"].value_counts())

# Separate fault from healthy periods
fault_rows   = df[df["is_fault"] == True]
healthy_rows = df[df["is_fault"] == False]

# Pre-impact rows (the early warning window)
precursor_rows = df[
    (df["is_fault"] == True) &
    (df["time_to_impact_s"] > 0)   # positive = still before impact
]

print(f"Total rows:     {len(df):,}")
print(f"Fault rows:     {len(fault_rows):,}")
print(f"Precursor rows: {len(precursor_rows):,}")  # where early warnings live
```

Or if you prefer querying the raw time-series first:

```python
import requests

# PromQL query: get tunnel latency for ce_branch1 over the last hour
resp = requests.get("http://localhost:8000/metrics", params={
    "query": 'sdwan_tunnel_latency_ms{device="ce_branch1"}',
    "start": int(time.time()) - 3600,
    "end":   int(time.time()),
    "step":  30,
})
result = resp.json()["result"]   # list of {metric: {...}, values: [[ts, val], ...]}
```

The synthetic dataset (`synthetic/generate.py`) extends real captures to **8.89 million rows** with calibrated statistical models, giving the ML team enough data to train on without requiring days of lab uptime.

---

## Putting It All Together: One Packet's Journey

To close the loop, here is what happens when a staff member at `ce_branch1` sends an email to a colleague at `ce_dc1`:

1. The staff member's laptop (host_branch1_CORP, IP 192.168.0.10) sends a packet to the datacenter server (host_dc1_CORP, IP 192.168.24.10).
2. The packet arrives at **ce_branch1** (the building's front door). ce_branch1 looks at the destination, sees it is CORP traffic, marks it with DSCP AF31, and forwards it to **pe1** via an eBGP-learned route.
3. **pe1** (the highway on-ramp) looks up the destination in its **VRF CORP** table, finds a VPNv4 route learned from pe3 for the datacenter subnet. It pushes two MPLS labels onto the packet: an inner VPN label (identifies the CORP VRF at the far end) and an outer transport label (for the MPLS path to pe3).
4. The packet travels through **p2 → p4** (highway core), with each P router swapping the outer label without ever looking at the IP header or VPN contents.
5. **pe3** (the far on-ramp) pops the VPN label, looks up in its **VRF CORP** table, and forwards to **ce_dc1** via eBGP route.
6. **ce_dc1** delivers to host_dc1_CORP.

Meanwhile, the WireGuard SD-WAN overlay is running in parallel. If the MPLS path degrades, the controller detects it (via modelled tunnel metrics) and reroutes the overlay traffic through a different hub. The MPLS underlay and SD-WAN overlay are complementary: MPLS handles VPN isolation, WireGuard adds encryption and intelligent path selection.

All along this path, **Telegraf is polling SNMP counters every 30 seconds**, **routers are emitting syslog messages that Promtail ships to Loki**, **PE and CE routers are exporting IPFIX flows that nfacctd collects**, and **the SD-WAN controller is emitting per-tunnel metrics every 5 seconds**. Every signal is tagged with `device="ce_branch1"` (or whichever node it came from) so the Data API can join them all into a single row in the final Parquet.

That Parquet row, with its 21 columns and a ground-truth `is_fault` label, is what the ML model sees. The goal: learn to recognize the early-warning signatures (latency creep, rekey clustering, prefix churn) and predict `t_impact` before it arrives.

---

**Navigation:** ← [01 Project Overview](01_PROJECT_OVERVIEW.md) | [03 Technical Code Guide](03_TECHNICAL_CODE_GUIDE.md) →
