# Technical Glossary — Air-Gapped Predictive NOC Copilot

**Target Reader:** AI/ML researcher with zero networking background.  
**Format:** Each term includes a bold name, 1-sentence definition, 2–3 sentences of context and connections to other terms.

**See also:** [02 Architecture Analogies](02_ARCHITECTURE_ANALOGIES.md) — narrative explanations | [03 Technical Code Guide](03_TECHNICAL_CODE_GUIDE.md) — API details | [04 Usability Cheatsheet](04_USABILITY_CHEATSHEET.md) — step-by-step commands

---

## Group 1: The Network Layers (Basics)

**IP (Internet Protocol)**  
The fundamental addressing and routing standard for the internet and enterprise networks. Every device has an IP address (e.g., `192.168.1.5`), and routers forward IP packets across networks using these addresses. In this lab, IP runs on top of the MPLS underlay.

**Packet**  
A unit of data transmitted across a network, typically 64–1500 bytes, carrying a header (source/destination IP, port, etc.) and a payload (the actual message). Routers and switches inspect packet headers to decide where to send them next. When a fault occurs (congestion, loss), packets are dropped or delayed.

**Router**  
A specialized computer that forwards packets between networks by inspecting the destination IP address and consulting a routing table. In this lab, routers are either **P** (core), **PE** (edge), or **CE** (customer) — each type has different responsibilities. Contrast with a switch (Layer 2, MAC-based).

**Switch vs. Router**  
A **switch** forwards traffic based on MAC addresses (Layer 2, the link layer — think "local neighborhood" delivery), while a **router** forwards based on IP addresses (Layer 3, the network layer — think "city-to-city" routing). Switches are fast but can't traverse the internet; routers connect different networks. In this lab, all forwarding is routing-based, handled by FRRouting containers.

**Subnet / CIDR**  
A contiguous block of IP addresses grouped together; notated as `10.1.0.0/16` (read "10.1 slash-16"), where the `/16` means "the first 16 bits are the network part; the remaining 16 bits are for individual hosts." Subnets isolate traffic: a host in one subnet must go through a router to reach another subnet. The lab uses multiple subnets (core `/31` links, customer `/30` CE-PE links, LAN `/24` blocks) to structure the topology.

---

## Group 2: The Provider Network (MPLS Core)

**MPLS (Multi-Protocol Label Switching)**  
A high-speed forwarding technique where routers pre-compute a path through the network, assign it a short numeric **label** (4 bytes), and then forward packets based solely on that label instead of inspecting the full IP header. Think of it as a highway with pre-labeled lanes — the packet gets a ticket at entry (label) and is routed along that lane without further inspection. The benefit: speed (one label lookup is faster than a full IP routing table lookup) and the ability to engineer traffic patterns (make certain traffic always take a specific path).

**Label**  
A 4-byte identifier (0–1,048,575) assigned to a path through the MPLS core. When a packet enters an MPLS network, the edge router (PE) adds the label to its header. Core routers (P) swap the label with a new one as the packet transits, following the pre-computed path (LSP). At the far edge (another PE), the label is removed and the packet continues as a normal IP packet. Labels are distributed among routers by the LDP protocol.

**LSR (Label Switching Router) / P Router**  
A router inside the provider core that only understands MPLS labels, not IP routing. It receives a labeled packet, swaps the label for the next one in the path, and forwards it onward — blindingly fast because there's no IP lookup. In this lab, **P routers** (provider core) are LSRs; they run OSPF (to build the network topology) and LDP (to distribute labels) but do NOT run BGP.

**LER (Label Edge Router) / PE Router**  
A router at the edge of the provider network that both understands MPLS labels AND IP routing. It adds labels to packets entering the MPLS network (at the source PE) and strips them at the destination PE. It also runs MP-BGP to exchange VPN routes with other PEs. In this lab, **PE routers** (provider edge) are LERs; they are the nexus between the MPLS core and the customer VPNs.

**LDP (Label Distribution Protocol)**  
The protocol routers use to agree on which labels mean what paths. Imagine two routers need to route traffic via a pre-computed path: they must agree that "label 100 = path via node X." LDP runs between adjacent routers and builds the label mappings so that when a packet tagged with label 100 arrives, everyone knows what to do with it. In this lab, LDP runs on all core P-P and P-PE links.

**LSP (Label Switched Path)**  
The pre-computed path through the MPLS core that a packet will traverse. An LSP is built using the network topology (discovered by OSPF) and is then labeled (via LDP). Every packet that enters an LSP gets the same treatment — it takes the same route through the core. Multiple LSPs can exist in parallel (e.g., one LSP per customer VRF), each with its own label and its own path. The telemetry tracks tunnel metrics (latency, jitter, loss) per LSP. In this lab each P node carries approximately 107 MPLS forwarding entries, monitored via `mpls_lsp_count`.

**POP (Point of Presence)**  
A geographic concentration of provider core routers that represents a single network region. This lab has 6 POPs; each POP contains 4 P routers (intra-POP full mesh, OSPF area K, cost 10) and 2 PE routers (dual-homed to the 2 PE-facing P routers in that POP). POPs are interconnected by an inter-POP backbone ring plus 3 chords, all in OSPF area 0 with cost 100. POP boundaries are the key fault-isolation domain: a `pop_isolation` fault cuts an entire region, while intra-POP faults stay local.

**OSPF area**  
A subdivision of the OSPF network within which all routers share identical topology knowledge (an LSDB). Routers in different areas only see summary routes exchanged via ABRs. Area 0 (the backbone area) connects all other areas; non-backbone areas must touch area 0 through an ABR. In this lab each POP is one OSPF area (1–6) and the inter-POP backbone is area 0. Per-area OSPF isolates SPF computation: a failure inside one POP's area does not trigger full SPF recalculation in all other areas.

**ABR (Area Border Router)**  
A router that is a member of two or more OSPF areas simultaneously — it maintains an LSDB for each area and advertises summary routes between them. In this lab, the first 2 P routers per POP are ABRs: p1+p2 (POP1), p5+p6 (POP2), p9+p10 (POP3), p13+p14 (POP4), p17+p18 (POP5), p21+p22 (POP6). Each ABR runs in both area 0 (inter-POP) and its POP's area K. The remaining 2 P routers per POP (e.g. p3+p4 in POP1) are pure intra-area (PE-facing) internal routers. ABRs are the primary targets for `ospf_area_flap` and `path_asymmetry` fault scenarios.

**IGP cost / IGP metric**  
An administrative weight assigned to each link that OSPF uses when computing shortest paths (via Dijkstra's algorithm). A lower cost means a more preferred path. In this lab, intra-POP P-P links carry cost 10 and inter-POP (area-0) links carry cost 100. This ten-to-one ratio ensures traffic prefers staying within a POP if possible, and that cross-POP paths incur a measurable, engineered cost difference — making it possible to steer traffic by raising or lowering costs (`path_asymmetry` fault) and to detect routing changes via `ospf_spf_last_duration_ms`.

**SRLG (Shared Risk Link Group)**  
A set of links that share a common physical resource (e.g., the same fibre conduit), meaning a single physical failure can take down all links in the group simultaneously. In this lab, each inter-POP adjacency uses 2 parallel links sharing one SRLG conduit; the `srlg_cut` fault scenario downs both links atomically to simulate a fibre cut. SRLGs are catalogued in `topology/topology-meta.json`. Understanding SRLGs is essential for ML models: an SRLG-cut looks like correlated but simultaneous failures on otherwise-unrelated logical links, which is a very different signature from an independent two-link failure.

**ECMP (Equal-Cost Multi-Path)**  
Forwarding traffic across two or more paths that have the same OSPF total cost. When OSPF computes equal-cost paths, the router distributes load across all of them (typically per-flow hashing). In this lab, PE routers are dual-homed to two PE-facing P routers within their POP; both uplinks have the same IGP cost, so traffic from the PE naturally load-balances across both — verified by the live lab (pe1→pe11 route shows ECMP over both dual-homed uplinks). ECMP improves resilience: if one P router fails, the other still carries traffic without reconvergence.

---

## Group 3: VPNs and Routing Protocols

**VRF (Virtual Routing and Forwarding)**  
A logical router inside a single physical router. It's a separate routing table, isolated from other VRFs, so customers don't see each other's routes. In this lab, there are 3 VRFs: **CORP** (business), **VOICE** (real-time), and **GUEST** (untrusted) — each site may have 1–3 VRFs depending on service type (branches have only CORP + VOICE; hubs and datacenters have all 3). Routes in one VRF never leak into another unless explicitly imported. Think of VRFs as separate apartment buildings on the same street — mail (routes) stays within the building.

**MP-BGP (Multi-Protocol BGP)**  
An extension to BGP that carries not just IPv4 routes but also VPN routes, MPLS labels, and other address families. In this lab, MP-BGP runs between all 12 PE routers (iBGP, internal BGP — all PEs are in AS 65000, with Route Reflectors pe1+pe2 reducing the 66-session full mesh to 21 sessions) and exchanges **VPNv4** routes, the address family that carries customer routes with VPN identifiers so they can be demultiplexed into the right VRF at the destination.

**VPNv4**  
An address family (a type of route) that carries IPv4 routes WITH a VPN identifier attached. When a customer advertises a route `192.168.1.0/24` in the CORP VRF, the PE sends it as a VPNv4 route (e.g., `192.168.1.0/24 RD 65000:10`) across MP-BGP to other PEs. Other PEs import this route into their CORP VRFs and make it available to their own customers. The RD and RT ensure routes don't collide and get imported into the right place.

**RD (Route Distinguisher)**  
A 64-bit value prepended to a customer route to make it globally unique, even if multiple customers have the same IP subnet. In this lab the RD is **per-PE**, not a single shared value: `<pe_loopback>:<vrf_table>` (e.g. `10.255.2.2:10` for CORP on pe2), so that a route re-advertised from two different PEs never collides as the identical VPNv4 NLRI (`generate.py` — see "VPNv4 RD" in SPEC-NOTES.md). The **RT** is still shared per VRF (`65000:10`/`65000:20`/`65000:30`) — RD disambiguates the route's origin, RT controls import. It's like a ZIP code that disambiguates two identical street addresses.

**RT (Route Target)**  
A 64-bit value that controls which VPNv4 routes get imported into which VRFs. A route is tagged with one or more RTs during export (e.g., `65000:10`); at a destination PE, only VRFs configured to import that RT will receive the route. In this lab, each VRF uses a single RT (equal to its RD), so CORP routes only import into CORP, VOICE into VOICE, etc. RTs are the "mailing list" that decides who gets the route.

**L3VPN (Layer 3 VPN)**  
A VPN service that isolates customers at the IP routing layer using VRFs, MPLS labels, and MP-BGP. It's called "Layer 3" because it works at the IP layer (Layer 3), not at Layer 2 (Ethernet). The complete chain is: CE advertises a route to its PE via eBGP → PE imports it into a VRF → PE exports it as a VPNv4 route via MP-BGP → other PE imports it into their VRF → traffic between two CEs follows the pre-computed MPLS LSP. This lab is a pure L3VPN deployment.

**BGP (Border Gateway Protocol)**  
The internet's routing protocol. BGP is used in two ways here: (1) **iBGP** between PEs (MP-BGP, for VPN routes), and (2) **eBGP** between a CE and its PE, one session per VRF (for customer routes). There is no BGP over the SD-WAN overlay — path selection between a branch CE and its hub is done by the controller reading tunnel metrics, not by a routing protocol (`generator/templates/frr.conf.j2` has no CE-to-hub `router bgp` stanza). BGP is slow to converge but can handle complex policies. The classic BGP failure mode is a "flap" — a route appears, disappears, reappears, making the network unstable.

**iBGP (internal BGP)**  
BGP peering between routers in the same Autonomous System (AS). In this lab, all 12 PEs are in AS 65000 and peer with each other via iBGP for MP-BGP VPNv4 routes. iBGP requires either a full mesh (every router peers with every other) or a route reflector (a central hub that all others peer to). This lab uses Route Reflectors: pe1+pe2 act as RR servers (cluster-id = their loopback); pe3–pe12 are RR clients that peer only with pe1+pe2 — 21 sessions instead of 66 (C(12,2)) in a full mesh.

**eBGP (external BGP)**  
BGP peering between routers in different ASes. In this lab, each CE is in its own private AS (branch CEs in 65101–65116, hubs in 65201–65204, DCs in 65301–65304) and peers via eBGP with its PE (which is in AS 65000). eBGP allows the customer to advertise routes to the provider and receive routes from the provider, but with natural firewalling — the AS boundary prevents accidental or malicious route injection.

**OSPF (Open Shortest Path First)**  
A fast link-state routing protocol used inside the provider core to discover the network topology and compute the shortest path between any two routers. All P and PE routers run OSPF; the lab uses **multi-area OSPF** — each POP is an independent OSPF area (areas 1–6), and the inter-POP backbone is area 0. Area Border Routers (ABRs) sit at the edge of each POP and advertise inter-area reachability summaries into area 0. Per-link IGP costs differentiate intra-POP links (cost 10) from inter-POP links (cost 100), giving the network an engineered traffic matrix. OSPF builds a complete map of the core and tells LDP how to build LSPs. Unlike BGP, OSPF converges in seconds. See also: **ABR**, **OSPF area**, **IGP cost**.

**CE Router (Customer Edge)**  
The customer's router that connects to the provider network. It's the first router inside the customer's network. In this lab, every site (branch, hub, DC) has one or more CE routers (actually, logical CE functions inside FRRouting containers). Each CE runs eBGP with its PE, advertising the customer LAN prefixes and receiving the provider's VPN routes. A CE is where QoS (DSCP marking, traffic shaping) is typically applied.

**AS (Autonomous System)**  
A network under a single administration, identified by a unique 16- or 32-bit number (ASN). All routers in an AS follow the same routing policy. In this lab, the provider is AS 65000 (all P and PE routers), and each customer (branch, hub, DC) is its own private AS. The AS boundary is where routing policies and external connectivity are managed.

---

## Group 4: SD-WAN Overlay

**SD-WAN (Software-Defined WAN)**  
A network architecture where a centralized software controller (instead of distributed router protocols) decides which traffic takes which path and can dynamically reroute based on real-time metrics (latency, loss, jitter, cost). In this lab, the **controller** (a Python service) monitors the live tunnel metrics from all CE nodes and makes routing decisions to maximize performance.

**WireGuard**  
A modern, minimal VPN tunnel protocol that is simpler and faster than IPSec. It uses Elliptic Curve Diffie–Hellman for key exchange and ChaCha20-Poly1305 for encryption. In this lab, WireGuard tunnels form the **SD-WAN overlay** (the "virtual" network of tunnels) on top of the MPLS underlay (the physical network). WireGuard is stateless and connectionless, making it ideal for unreliable networks.

**Tunnel**  
An encrypted point-to-point connection between two nodes (typically two CEs or a branch CE and a hub CE) over any underlying network. The tunnel encapsulates the original packet inside a new outer header so the original packet is invisible to the underlying network; only the tunnel endpoints can see it. In this lab, WireGuard tunnels are the overlay; MPLS LSPs are the underlay.

**Hub-Spoke**  
A network topology where remote sites (spokes) only communicate with each other through a central site (hub), not directly. In this lab, all 28 branch+DC CEs are spokes and all 6 hub CEs are concentrators; every spoke WireGuard-peers to **all 6 hubs** (`generator/generate.py:471-486`), giving 28 x 6 = 168 spoke-hub tunnels, plus 3 direct hub-hub tunnels (adjacent pairs, when `hub_hub_wg: true`) for spoke-to-spoke fast-path. A branch user accessing a datacenter sends traffic through a hub, which then forwards it to the DC — the controller's `VRF_PREFERRED_HUB` policy picks which hub is primary per VRF (`controller/controller.py:47-49`). Hub-spoke simplifies security (you can monitor all traffic at the hub) and reduces tunnel count versus a full n*(n-1)/2 mesh.

**Path Selection**  
The controller's real-time decision about which tunnel to use for a given packet. Inputs: latency, jitter, loss, cost, policy. Outputs: "send this traffic via tunnel A (the hub-primary) instead of tunnel B (the backup)." In this lab, the controller runs an exponential moving average (EMA) of tunnel metrics every second and adjusts path assignments; Telegraf collects the controller's decision stream as Prometheus metrics.

**Overlay Network**  
A network built ON TOP of another network. In this lab, the WireGuard overlay (layer 4: tunnels) sits on top of the MPLS underlay (layer 3: LSPs) which sits on top of IP (layer 3, but lower-layer). Traffic enters the overlay tunnel at a branch CE, travels through the tunnel (encapsulated), arrives at a hub CE, and exits the tunnel as a normal IP packet. The beauty: the overlay is decoupled from the underlay, so you can reroute the overlay without changing the underlay.

**Underlay Network**  
The physical (or logical) network that carries overlay traffic. In this lab, the underlay is the MPLS core (P and PE routers, OSPF + LDP + MP-BGP + LSPs + L3VPN). The underlay is stable and controlled by the provider (you can't change it), so the overlay uses the underlay's connectivity to reach the far end. If the underlay fails, the overlay fails — but if the overlay is misconfigured, the underlay is unaffected.

**Controller**  
A centralized software agent that gathers telemetry from all sites (tunnel metrics, interface counters, controller events) and makes SD-WAN routing decisions. In this lab, the controller (Python service on `172.20.20.56:9362`) reads tunnel latency/loss/jitter from all CE nodes, computes EMA, and publishes a stream of path-selection decisions as Prometheus metrics. A human NOC operator or an automated policy engine can use these decisions to know what the network is doing and why.

---

## Group 5: QoS (Quality of Service)

**QoS (Quality of Service)**  
The technology for prioritizing certain traffic types over others so that critical applications (VoIP, video conferencing) get low latency and sufficient bandwidth even when the network is congested. Without QoS, a large file transfer could starve a VoIP call. In this lab QoS is classified structurally, not by packet inspection: each CE-PE uplink carries exactly one VRF (see **VRF-aware CE**), so the uplink interface itself is the classifier — no DSCP marking or filter rules exist. **HTB** shapes each uplink to its VRF's rate/ceil. In this lab, VOICE is highest priority, CORP is medium, GUEST is lowest (`generator/generate.py:600-611`, `generator/templates/qos.sh.j2`).

**DSCP (Differentiated Services Code Point)**  
A 6-bit field in the IP header that, on real networks, marks a packet's priority class (EF=46 voice, AF31=26 business, BE=0 best-effort). **Not used for classification in this lab.** The DSCP label is carried only as a naming/rate convention in `qos.sh` comments and the spec's `qos.classes[].dscp` field — nothing marks it on the wire, and no `tc` filter or iptables rule matches on it. Classification is structural: one VRF per uplink interface, so the interface *is* the class (`generator/generate.py:602-604`: "no DSCP u32 filters: nothing in the lab marks DSCP, so a tos-match tree would put 100% of traffic in the default class anyway").

**EF (Expedited Forwarding)**  
DSCP code 46, used as the VOICE class label only (not marked on packets — see **DSCP**). In this lab, the VOICE uplink is HTB-shaped to rate 300000kbit / ceil 400000kbit / prio 1 against a 1 Gbit root (`topology/configs/ce_hub1/qos.sh:20-28`).

**AF31 (Assured Forwarding, class 3, drop precedence 1)**  
DSCP code 26, used as the CORP class label only (not marked on packets — see **DSCP**). In this lab, the CORP uplink is HTB-shaped to rate 500000kbit / ceil 700000kbit / prio 2 against a 1 Gbit root (`topology/configs/ce_hub1/qos.sh:9-17`).

**BE (Best Effort)**  
DSCP code 0, used as the GUEST class label only (not marked on packets — see **DSCP**). In this lab, the GUEST uplink (hub/dc sites only; branches have no GUEST VRF) is HTB-shaped to rate 200000kbit / ceil 250000kbit / prio 3 against a 1 Gbit root (`topology/configs/ce_hub1/qos.sh:29-37`).

**HTB (Hierarchical Token Bucket)**  
A Linux queuing discipline (qdisc) that enforces rate limits and priorities in a tree structure. In this lab, each CE-PE uplink gets its own root HTB (rate = ceil = the uplink's total, e.g. 1 Gbit) with a single child class for that uplink's VRF, set as the `htb default` — every packet on the interface falls into that one class, since no other VRF's traffic can appear on it. `fq_codel` is attached under the class as AQM (chosen over `sfq`, which is not reliably loaded in WSL2/clab containers). See `generator/templates/qos.sh.j2:12-17`.

**tc (traffic control)**  
The Linux command-line tool that configures the kernel's packet scheduling and QoS. `tc qdisc add` creates a queue discipline; `tc class add` creates a class under it. In this lab, every CE node has a `qos.sh` script (rendered from `qos.sh.j2`) that runs one `tc qdisc add ... htb default <classid>` + one `tc class add` per uplink — one uplink per VRF, so no `tc filter` and no DSCP/iptables marking are needed.

---

## Group 6: Telemetry (Metrics, Logs, Flows)

**Telemetry**  
The automated collection and transmission of performance data from the network to a central monitoring system. Rather than logging into each router to check status (manual, slow, error-prone), telemetry systems emit a continuous stream of counters, events, and flow records. In this lab, telemetry flows from FRR containers → Telegraf/Promtail/nfacctd agents → VictoriaMetrics/Loki/nfacctd storage → Grafana dashboards and the data API.

**SNMP (Simple Network Management Protocol)**  
The standard protocol for polling device statistics. An SNMP **agent** (e.g., `snmpd` on each FRR node) publishes a tree of variables (interface counters, routing table size, CPU usage, etc.), and an **SNMP manager** (Telegraf, in this lab) periodically polls the agent with queries like "give me the byte-count on interface eth0." SNMP is simple but chatty (many small requests); it's ideal for low-frequency polling (every 30 seconds).

**MIB (Management Information Base)**  
The dictionary of SNMP variables. The standard MIB for network interfaces is **IF-MIB**, which defines variables like `ifName`, `ifHCInOctets` (high-capacity byte counter), `ifHCOutOctets` (egress byte counter), `ifSpeed`, etc. Each variable has a unique **OID** (Object ID). In this lab, Telegraf polls `ifHCInOctets` and `ifHCOutOctets` on each CE's uplink to build time-series of traffic volume.

**OID (Object Identifier)**  
A globally unique address of an SNMP variable, typically written in dot notation (e.g., `1.3.6.1.2.1.2.2.1.1` = `ifIndex`). When Telegraf issues an SNMP query, it references variables by OID. OIDs are hierarchical; the IF-MIB tree starts at `1.3.6.1.2.1.2` (interfaces), and each interface is numbered sequentially. Don't memorize OIDs; the Telegraf SNMP plugin translates human-readable names (e.g., `ifHCInOctets`) to OIDs automatically.

**IPFIX / NetFlow**  
Standards for recording network flows (who sent packets to whom, how many bytes, how long, which port, etc.). **IPFIX** is the newer standard; **NetFlow** (v5, v9) is older but still common. A flow record captures: source IP, destination IP, source port, destination port, protocol, start time, end time, byte count, packet count. The benefit: you can answer questions like "which hosts are consuming the most bandwidth?" without logging into every router. In this lab, **nfacctd** (a NetFlow/IPFIX collector) receives flow records from all CE and PE nodes.

**Syslog**  
A standard protocol for router log messages (event records, not counters). When something important happens — a BGP adjacency comes up or goes down, a link flaps, a configuration change is made — the router sends a syslog message. In this lab, FRR containers emit syslog events (BGP ADJCHANGE, OSPF neighbor changes, etc.) which are collected by **Promtail** and stored in **Loki** for searching and analysis. Syslog is event-driven (messages only when something changes), unlike SNMP which is polled continuously.

**Telegraf**  
A lightweight metrics collection agent. Telegraf connects to SNMP agents, Prometheus endpoints, APIs, and other sources and polls them on a schedule, then exports the results to time-series databases (VictoriaMetrics, Prometheus, InfluxDB). In this lab, Telegraf runs in a container on the telemetry network and polls: (1) SNMP on all CE/PE/P nodes for interface counters, (2) the controller's Prometheus endpoint for tunnel metrics and path-selection decisions.

**VictoriaMetrics**  
An open-source, fast time-series database (TSDB) compatible with Prometheus. It stores metrics (data points tagged with labels, indexed by time) and supports **PromQL** queries. In this lab, VictoriaMetrics stores millions of time-series points (one per metric per device per 30-second interval, over 30 days of retention). Unlike traditional SQL databases, VictoriaMetrics is optimized for write-heavy workloads and time-series queries. The data API and Grafana dashboards query it to visualize trends, detect anomalies, etc.

**PromQL (Prometheus Query Language)**  
The query language for VictoriaMetrics and Prometheus. PromQL is specialized for time-series; a query like `max(sdwan_tunnel_latency_ms{device="ce_branch1"})` returns the maximum tunnel latency for a specific device over a time range. The fault orchestrator uses PromQL queries (e.g., to detect when congestion threshold is crossed) to derive `t_impact`. PromQL supports aggregation, filtering, math, and more.

**Loki**  
A log aggregation system (log TSDB) by Grafana. Unlike traditional log systems that index every word, Loki only indexes labels (e.g., `device`, `level`, `service`) and stores log lines as strings. This makes it fast and cheap at scale. In this lab, syslog events from FRR nodes are sent to Loki by Promtail; operators can search for "BGP adjacency changes on device pe1" without scanning gigabytes of raw logs.

**Promtail**  
The agent that ships logs to Loki. Promtail watches log files (e.g., syslog streams from FRR), parses them, attaches labels (e.g., `device=ce_branch1`), and sends them to Loki. In this lab, Promtail listens on syslog port 1514 (both TCP and UDP), receives BGP/OSPF adjacency events, and tags them with the originating device name for later correlation.

**Grafana**  
A visualization and dashboarding tool. Grafana can query Prometheus/VictoriaMetrics (for metrics) and Loki (for logs), then display them as graphs, heatmaps, tables, and alerts. In this lab, Grafana hosts a single "NOC Overview" dashboard (`telemetry/grafana/dashboards/noc-overview.json`) with 11 panels: interface RX/TX throughput, SD-WAN tunnel latency/loss, MPLS LDP session state, BGP VRF prefix count per PE, controller drift, OSPF adjacency state, OSPF SPF duration, MPLS LSP count, and BGP peers established. All 11 panels were audited against actual emitters in this repair pass — all 11 are live. There is no separate topology-map dashboard. Operators use Grafana to spot problems and verify that faults are injected correctly.

**nfacctd**  
A daemon (part of the pmacct suite) that collects NetFlow/IPFIX records from network devices and processes them (aggregates, filters, exports). In this lab, nfacctd listens on port 2055 (UDP) for IPFIX records from all CE and PE nodes, aggregates them by source/destination/port/VRF, and exports summaries to VictoriaMetrics or other backends. This lets the data API answer questions like "how much traffic crossed VRF CORP in the last hour?" without per-packet inspection.

**Time-Series**  
Data indexed by time. Each point is a (timestamp, value) pair. A time-series can have multiple dimensions (labels): `latency_ms{device="ce_branch1", tunnel="hub1"} = [t1: 45.2, t2: 46.1, t3: 48.5, ...]`. Time-series are the natural format for network metrics (counters over time), and they compress well (delta-of-delta encoding). VictoriaMetrics and Prometheus both store time-series; Loki stores event-series (logs with timestamps and labels).

**Join Key**  
The common identifier used to link data from different sources. In this lab, the universal join key is **device** (the node name: e.g., `ce_branch1`). All telemetry (SNMP metrics, syslog events, flow records, controller decisions) is labeled with `device`. The fault labels also include `device`, so the ML team can join the label timeline with the telemetry metrics to know which time windows had faults on which devices.

---

## Group 7: Containerlab & Lab Infrastructure

**Containerlab**  
A tool that orchestrates Docker containers as network nodes and wires them together with virtual Ethernet cables (veths). Rather than running routers on physical hardware, Containerlab runs them as lightweight containers, making it easy to create large topologies on a single machine. Containerlab reads a YAML file (`clab.yml`) that describes the nodes, links, and images, then uses Docker to spin them up and `ip link` to connect them. In this lab, all 148 lab containers (24 P + 12 PE + 34 CE + 78 hosts) are Containerlab containers; a further ~11 telemetry/infra containers bring the total to ~159.

**Docker**  
A container runtime — a lightweight virtualization technology. Each container is an isolated Linux user space with its own filesystem, processes, and network namespace. A Docker image is a template (like a frozen VM disk image) that can be instantiated into many containers. In this lab, the FRR image (`frr-node:0.1`, based on FRR 10.x) is used for 70 router containers (24 P + 12 PE + 34 CE); the multitool image is used for 78 host containers. Containers are much lighter than VMs (seconds to boot, MB of RAM per container).

**FRRouting (FRR)**  
An open-source routing suite implementing OSPF, BGP, LDP, MPLS, VRF, and a dozen other protocols. FRR runs in user space (not kernel space like traditional routers) and manages the kernel's routing table via netlink. In this lab, FRR is the NOS (network operating system) for all P, PE, and CE routers. Configuration is via files (`frr.conf`, `daemons`) or the `vtysh` CLI.

**veth (Virtual Ethernet Pair)**  
A Linux kernel construct: two virtual network interfaces that are crossed-connected (traffic sent on one appears as input on the other). Containerlab uses veths to connect containers: one end of the veth is inside a container (e.g., `eth0` on a router), the other end is on the host or in a bridge. When a packet is transmitted on a container's interface, it travels through the veth to the other side (typically another container's interface), making the containers appear directly connected.

**netem (Network Emulator)**  
A Linux kernel module that adds network impairments (delay, jitter, packet loss, rate limiting, reordering, duplication) to a network interface. `netem` is applied as a queueing discipline (qdisc) under `tc`; e.g., `tc qdisc add dev eth0 root netem delay 10ms loss 1%` adds 10 ms of delay and 1% loss to eth0. Containerlab has a native command (`containerlab tools netem set`) that applies netem to links between containers. In this lab, netem serves two purposes: (1) **per-site baseline impairment** — every CE node has a permanent netem on `eth0` (the transport interface toward its PE) that emulates realistic propagation delay: branch ~41 ms, hub ~17 ms, DC ~12 ms, each with small jitter and ≤1% loss; (2) **fault injection** — the orchestrator adds further netem impairment on top of the baseline to model congestion, asymmetric loss, or brownout. See also: **measured RTT**, **propagation delay**.

**measured RTT (vs. modelled RTT)**  
Tunnel latency has three layers, and the controller's HELP text for `sdwan_tunnel_latency_ms`/`_jitter_ms`/`_loss_pct` states plainly that the emitted series is **SIMULATED**, not a raw measurement (`controller/controller.py:8-13`). Layer 1 is real: a background pool pings the WireGuard peer (`ping -I wg0 <peer_ip>`) roughly every 45 s and caches avg/jitter/loss — this is gated behind `MEASURE_RTT` (on only in the deployed container; off, falling back to a 1 ms floor, in `--selftest`/no-lab runs). Layer 2 is a modelled M/M/1 queueing term for congestion on top of the cached ping. Layer 3 is the fault term, which is **not** a measurement either: since the wg0 ping never traverses the impaired `eth1` uplink, the controller reads the fault's netem impairment back out of the qdisc **config** and folds it in (`_read_netem`, `controller.py:150-181`). To verify the raw ping layer: `docker exec clab-sdwan_mpls_noc-ce_branch1 ping -c5 -I wg0 172.16.0.1`. See also: **netem**, **jitter**.

**jitter**  
Variation in packet delay. If successive packets from A to B take 40 ms, 43 ms, 38 ms, 41 ms, the jitter is ~±3 ms. Jitter matters most for real-time traffic (VoIP, video): the receiving buffer must absorb it, and excessive jitter causes audible glitches. In this lab, per-site netem adds a small fixed jitter component (±2–5 ms) on `eth0` to prevent perfectly uniform RTT that would look artificial to ML models. The `tunnel_jitter_ms` metric in the dataset captures jitter on a per-tunnel basis. See also: **netem**, **EF (Expedited Forwarding)**.

**propagation delay vs. queueing delay**  
Two components of end-to-end latency: **propagation delay** is the irreducible time for a signal to travel the physical distance (e.g., fiber speed-of-light); **queueing delay** is time spent waiting in router buffers because the link is busy. In this lab, the per-site netem baseline on `eth0` models propagation delay (fixed distance to the provider PE), while the congestion fault injector adds queueing delay on top. The distinction matters for ML models: propagation delay is constant and site-type-specific (predictable); queueing delay is the fault signal (variable, correlated with traffic load). Confirm site-type tiers in VictoriaMetrics: `avg by (site_type)(sdwan_tunnel_latency_ms)` → branch > hub > dc.

**VRF-aware CE**  
A CE (customer edge) router configured with multiple VRFs (CORP, VOICE, GUEST). Each VRF is a separate routing table; traffic in one VRF cannot leak into another unless explicitly imported. In this lab, every CE is VRF-aware and has interfaces in multiple VRFs. This requires: (1) each CE-PE link to be part of a VRF, (2) each LAN behind the CE to be part of a VRF, (3) eBGP peering per VRF, (4) QoS rules per VRF. The benefit: perfect customer isolation and the ability to offer tiered services (CORP, VOICE, GUEST).

---

## Group 8: Fault Injection & ML Labels

**Fault Injection**  
The deliberate introduction of network problems (congestion, packet loss, routing failures) into a live system to generate labeled training data for ML models. Without faults, a network is boring (no anomalies to learn from); by injecting faults, you create realistic labeled examples that tell the model "at this time window on this device, this fault was active." The challenge: the faults must be realistic (gradual congestion buildup, not a hard crash) and the ground truth must be accurate.

**Ground Truth**  
The definitive, oracle answer to a question. In this lab, ground truth for faults is: what was injected, when did it start, when did the user feel impact, how long did it last? The orchestrator records this in the **labels timeline** (labels.jsonl) with precise timestamps. This is the "label" that the ML team trains on — the answer key against which the model's predictions are scored. Ground truth is only as good as the injector; if the injector is buggy, all labels downstream are wrong.

**Label Timeline**  
A series of JSON objects (one per injected fault), each recording: `scenario_id`, `type` (congestion/bgp_flap/etc.), `target` (always a dict with a `device` key, plus scenario-specific keys like `interface`/`neighbor`/`links` — never a bare string), `severity` (`null` for the 7 scenarios where severity has no effect — see **impact_method**), `t_start` (when injected), `t_impact` (when observable), `t_end` (when reverted), `lead_time_s`, `dry_run` (bool, present on every row), `error` (present on failure), and metadata (PromQL query, baseline/impact values). Written by `faults/orchestrator.py`. The labels timeline is the contract between the fault system and the ML team: "we injected a congestion fault on ce_branch1 at 2026-06-21T10:30:00Z, and the telemetry showed impact 48 seconds later." The ML team trains models to predict fault impact using only the telemetry before `t_impact`.

**t_start**  
The timestamp (UTC ISO-8601) when the fault injector applied the fault. This is always precise (to the second) because the orchestrator records it immediately. However, the fault may not be observable in telemetry for a few seconds (due to EMA smoothing, metric aggregation, or the fault taking time to propagate). See `lead_time`.

**t_impact**  
The timestamp when the fault became observable in telemetry, and one of five `impact_method` values in the label row (`faults/orchestrator._resolve_impact`). Every row of the dataset carries `impact_methods` — a list, one entry per concurrent episode — so a model can weight a label by how it was established:
- `vm_threshold` — a real PromQL poll against VictoriaMetrics crossed a defined threshold.
- `ramp_derived` — no probe crossing, but the impairment ramped, so `t_impact` is the point at which it reaches the VRF's SLA objective (`faults/leadpriors.THETA_SLA`: VOICE 150 ms / 1.0%, CORP 250 ms / 2.0%, GUEST 400 ms / 5.0%). A tunnel carries every VRF of its site, so the strictest objective present is the one that counts. When both this and `vm_threshold` are available the label records `t_impact_ramp` alongside, so the two can be compared.
- `modelled` — a fixed delay after `t_start`, used where no clean impact metric exists (BGP flap, node kill, and three scenarios — `hub_spoke_congest`, `bgp_cascade`, `brownout` — that lost their probes in this repair pass and no longer claim `vm_threshold`).
- `modelled_fallback` — a `vm_threshold` scenario whose probe returned *some* data during the window but never crossed the threshold, so the delay-based estimate was used instead.
- `probe_unavailable` — a `vm_threshold` scenario whose probe returned nothing for the entire window (VictoriaMetrics had no matching series at all).

**lead_time_s**  
The time delta `t_impact - t_start`, in seconds. **Drawn per episode from a per-fault-type prior** (`faults/leadpriors.LEAD_BUCKETS`: 4–10 buckets for control-plane events, 10–40 for congestion, 20–80 for `gray_failure`, lognormal with p10/p90 on the endpoints), shared by the synthetic generator and the live orchestrator. This is the "warning window" for the ML model: "we injected a fault at time T; the user felt impact at T + lead_time. Can your model predict the impact by time T + lead_time?"

It is a **prior, not a measurement**: the previous value came from the median lead of a 24.5-minute capture, which is too short to estimate one — it produced ~2 s leads that a 4-bucket safety floor then clamped to exactly 120 s on every episode, so `lead_time_s` shipped with CV 0.03 and no hazard model could learn a distribution from it. The floor is still there as a net (a lead shorter than a few buckets gives the model no usable precursor window) but now fires on well under 1% of draws, and the generator warns above 5%.

**Multi-label row**  
A dataset row carries EVERY fault episode whose window overlaps it, not just the most severe one: `fault_types`, `severities`, `scenario_ids`, `impact_methods` and `time_to_impact_s` are index-aligned lists with the primary (highest severity) at element 0, and `n_concurrent` is their length. Up to 3 faults overlap on one row in the shipped data. The legacy scalar columns (`fault_type`, `severity`, `scenario_id`) hold the primary, so a reader written against the original 21-column order still works. Concurrency exists because the fault campaign's lock keys on the RESOURCE a scenario mutates (interface / tunnel / VRF / process) rather than the whole device — two netem installs on one interface still exclude each other, but a VRF policy drift and an interface impairment can run together.

**Ordinal severity**  
`severity` is a float — 0.33 / 0.66 / 1.0 — so a loss function can treat it as a magnitude; `severity_label` keeps the `low`/`medium`/`high` string. Null in both when the scenario's injector ignores severity (link-set and process-kill faults), because the column must not carry a value the fault never used.

**Precursor**  
A signal that appears BEFORE the fault becomes user-visible. For congestion, the precursor is latency and jitter gradually climbing (before the threshold is crossed). For BGP flaps, the precursor might be the control-plane CPU spike (before the adjacency flaps). The ML model's job is to detect precursors and predict impact before `t_impact`. The lead time is the width of the precursor window.

**Scenario**  
A complete fault episode: inject → wait/perturb → observe → revert. The orchestrator runs scenarios on demand (CLI: `python3 orchestrator.py --scenario congestion --target ce_branch1`). There are 21 named scenarios in two tiers: 12 edge/transient faults (congestion, bgp_flap, tunnel_degrade, policy_drift, node_failure, asymmetric_loss, brownout, plus adversarial extras) and 9 MPLS-core/catastrophic/correlated faults (p_node_failure, pop_isolation, core_partition, srlg_cut, core_congestion, ospf_area_flap, path_asymmetry, rr_failure, gray_failure). Core link-sets are resolved at runtime from `topology-meta.json`. The scenario is idempotent; running it twice in a row should be safe (injectors have clean `revert()` methods).

**is_fault**  
A boolean label column in the dataset. For each time bucket (e.g., 30-second windows, covering `[ts, ts+step)`), `is_fault = True` if that interval **overlaps** the `[t_start, t_end]` window of at least one scenario, `False` otherwise (`dataapi/export.py:185-188`). This is an interval-overlap join, not the single-bucket-instant containment check it used to be (`ts in [t_start, t_end]`) — the old check silently produced a dataset with `is_fault.sum() == 0` whenever no bucket boundary happened to land exactly inside a fault window. The ML team uses `is_fault` as the target variable for a binary classification problem.

---

## Group 9: ML/AI Concepts (as they relate to this project)

**Time-Series Forecasting**  
Predicting future values of a metric (e.g., latency in 5 minutes). In this lab, a time-series forecasting model might predict "tunnel latency will exceed 100 ms in the next 5 minutes" based on historical latency data. The precursor window (e.g., latency climbing) gives the model signal to learn from. Time-series models typically use LSTM, Transformer, or statistical methods (ARIMA); this project's data API provides all time-series for training.

**Anomaly Detection**  
Detecting unusual patterns in data without a predefined "anomaly" threshold. For example, "BGP session churn is normally 0–5 flaps per hour; seeing 50 flaps in 10 minutes is anomalous." The model learns the normal distribution from historical data, then flags deviations. In this lab, anomaly detection could be: (1) unsupervised (e.g., isolation forest on tunnel metrics) or (2) supervised (trained on the labeled faults to recognize fault signatures). The data API provides labeled time-series, so both approaches are viable.

**Lead Time**  
How far in advance a model can predict a fault. The problem statement's primary metric is maximizing lead time: "can you predict the fault 60 seconds before impact?" Lead time is a design variable: longer lead times are more useful for a NOC operator (more time to react) but harder for the model to achieve (fewer and weaker precursors further upstream). This lab's label timeline records `lead_time_s` for every injected fault, so the team can measure model lead time accuracy directly.

**RAG (Retrieval-Augmented Generation)**  
Giving an LLM access to a local knowledge base (documents, runbooks, topology diagrams, past incidents) so it can answer questions with context-aware, factual information. In this lab, the **ragcorpus/** folder contains: topology maps (as JSON), runbooks for common faults (e.g., "if BGP flaps, check the adjacency in Loki logs"), and incident templates ("this looks like congestion; check tunnel latency in Grafana"). An offline LLM can use RAG to explain: "the model predicted a congestion fault on ce_branch1; here's the telemetry evidence, here's the topology, here's a runbook for remediation."

**Air-Gapped**  
Completely isolated from the internet. An air-gapped system cannot make outbound requests (no DNS, no cloud APIs, no data exfiltration). In this lab, air-gap is a hard requirement (ISRO BAH 2026 grading: 20% of score). All docker images are pre-downloaded and saved offline. At runtime, the topology runs with `imagePullPolicy: Never` so it can't even try to fetch images. The verify-airgap script (running during deployment) proves zero outbound egress by capturing network traffic and checking for external IPs. This is why the RAG corpus and all training data are local.

**Parquet**  
An efficient, columnar file format for storing tabular data. Unlike CSV (row-oriented, verbose), Parquet compresses well and supports fast column-specific queries (e.g., "fetch all rows where is_fault=True and vrf=CORP" without scanning all columns). In this lab, the data API exports labeled time-series as Parquet files (one row per (time, device, entity, entity_type, ts-bucket) tuple, 49 columns). Every file the **synthetic** generator writes also carries mandatory file-level key/value metadata — `synthetic=true`, `generator`, `calibrated_from` (`synthetic/generate.py:657-659`) — and `synthetic/check.py` gate 0 refuses any file lacking it, so a synthetic file can never be silently mistaken for a live-lab export. Parquet is the standard format for ML training datasets; scikit-learn, pandas, and PyTorch can all read it directly.

---

## How They All Connect

The entire system forms a data pipeline: **network simulation → fault injection → telemetry collection → data export → ML training**. Here's the flow:

**Network simulation** begins with a single YAML file (`topology-spec.yaml`) specifying node counts and site types. A generator (Jinja2 + Python) emits 148 Containerlab node definitions, 70 FRR configs (multi-area OSPF/LDP/MP-BGP/VRFs), 78 host configs, ~168 SD-WAN tunnel configs, QoS rules, and `topology/topology-meta.json` (POP/ABR/SRLG metadata for the fault orchestrator). Containerlab deploys all 148 lab containers on a single Linux machine, wiring them with virtual Ethernet veths. The MPLS core (24 P + 12 PE routers running FRR) comes up with multi-area OSPF and LDP, building LSPs across the POP-structured backbone. Each site gets isolated VRFs (CORP/VOICE/GUEST), connected via CE routers. The SD-WAN controller (Python service) comes online and monitors tunnel latency/jitter/loss from all CE nodes, publishing decisions to a Prometheus endpoint.

**Traffic simulation** starts: the trafficgen service (Python + Docker) drives realistic, diurnal flows across the network using `nc` or iperf3, causing interface counters to climb and nfacctd to see flows. Latency and jitter naturally increase as load increases, simulating realistic congestion curves.

**Fault injection** adds the ground truth: the orchestrator (Python, in `faults/`) applies one of 21 fault scenarios — 12 edge/transient faults (congestion, BGP flap, tunnel degrade, policy drift, node failure, asymmetric loss, brownout, and others) plus 9 MPLS-core/catastrophic/correlated faults (p_node_failure, srlg_cut, core_congestion, ospf_area_flap, path_asymmetry, rr_failure, gray_failure, pop_isolation, core_partition). Injectors use native tools (`tc`/`netem` for delay/loss, `vtysh` for OSPF cost changes, `vtysh clear bgp` for flaps, `kill -9` for process crashes, `MultiLinkFault` for multi-link teardown, issued in sequence rather than atomically so OSPF sees the set drop staggered). Core link-sets are resolved from `topology-meta.json` at runtime. The orchestrator polls VictoriaMetrics to detect when the fault became observable in telemetry, records `t_start`, `t_impact`, `t_end`, and `lead_time`, and writes a label row to `labels.jsonl`.

**Telemetry collection** runs continuously:
- **Telegraf** polls SNMP on all 70 nodes every 30 seconds (interface counters, ARP table, BGP neighbor count) → VictoriaMetrics.
- **noc-ldp-metrics sidecar** (`ldp-metrics.sh`) queries `vtysh` JSON on each P+PE node and exports OSPF and MPLS metrics to VictoriaMetrics: `ospf_neighbor_state` (1=Full, 0=not; ~156 series), `ospf_spf_last_duration_ms`, `ospf_spf_last_executed_ms`, `mpls_lsp_count`, and `bgp_peer_established`.
- **nfacctd** receives IPFIX records from all CE/PE nodes → aggregates and exports flow summaries → VictoriaMetrics.
- **Promtail** listens on syslog port 1514, receives BGP/OSPF adjacency events from FRR → Loki.
- **Controller** publishes Prometheus metrics (tunnel latency/loss/jitter, path decisions) → VictoriaMetrics.

All signals are tagged with the `device` label (e.g., `sdwan_tunnel_latency_ms{device="ce_branch1"}`).

**Data export** (the data API, FastAPI on `:8000`) joins all signals:
1. Fetch metrics from VictoriaMetrics (time-series of ifHCInOctets, ifHCOutOctets, tunnel metrics).
2. Fetch events from Loki (BGP adjacency changes, BGP prefix deltas).
3. Fetch flows from nfacctd (aggregated bytes/packets per source/destination/port/VRF).
4. Fetch labels from `labels.jsonl` (injected faults with t_start/t_impact/lead_time).
5. Normalize all to a common schema (one row per (time, device, signal)) and join on device + time.
6. Export as labeled Parquet: 49 columns (time, device, site_type, vrf, interface, traffic counters, tunnel metrics, interface error/discard counters, queue depth, transceiver DOM readings, device CPU/memory, routing-table and LSDB size, chassis temperature/power/fan/PSU, flow stats, is_fault, fault_type, lead_time_s, severity, scenario_id).

**ML training** uses this Parquet: each row is a 30-second time bucket; features are metrics (latency, loss, jitter, traffic), events (adjacency changes), flows (top talkers); label is `is_fault` or `fault_type` or `lead_time_s`. Models can be:
- **Supervised anomaly detection:** train on labeled faults to recognize fault signatures (random forest, XGBoost, neural nets).
- **Unsupervised anomaly detection:** isolation forest or autoencoder on normal metrics to flag deviations.
- **Time-series forecasting:** LSTM/Transformer to predict fault impact N seconds in advance (maximize lead time).
- **Interpretability:** LIME/SHAP to explain which metrics triggered the prediction; RAG to supplement with topology/runbooks.

---

## Group 10: Frontend Mock Playback (`frontend/plugin`)

**absTick**  
The frontend's monotonic display clock (`AppState.absTick`, `src/state/reducer.ts`), distinct from `cursor`. `cursor` is the data index into the 152-bucket fixture tape and wraps every loop; `absTick` never wraps, it just keeps incrementing. The chart x-axis, playback clock label, and Copilot message timestamps read `absTick` so time counts up across loops instead of jumping backward ~76 minutes when `cursor` wraps. Values still come from the wrapped `cursor` window — only the displayed timestamp is monotonic.

**Synthetic telemetry fill**  
`src/data/telemetrySynth.ts` generates a deterministic, role-aware series for any device/metric the bundled fixtures don't cover, so every one of the 148 selectable topology nodes has something to chart (previously only 27 had real series; the rest showed "No telemetry"). Deterministic means seeded by `deviceId`, not `Date.now()`/`Math.random()` — same device always renders the same synthetic shape. Also used to replace previously flat/null series (interface error counters, PE transceiver metrics) with lively synthetic values, since a flat line reads as broken on a demo screen.

**Air-gap verification** ensures the pipeline is truly offline:
- All Docker images are pre-saved to `.tar.xz` files.
- At deploy time, `load-offline.sh` loads images from local storage (no registry pull).
- `verify-airgap.sh` runs the full topology and captures network traffic (tcpdump), proving zero outbound connections.
- The data API, ML pipeline, and RAG corpus are all local files (no cloud, no external APIs).

---

## Diagram: End-to-End Data Flow

```
┌─────────────────────────────────────────────────────────────────────────┐
│ NETWORK SIMULATION (Containerlab + FRR)                                 │
│  - 24 P routers (multi-area OSPF/LDP, 6 POPs × 4, area-0 backbone)     │
│  - 12 PE routers (MP-BGP VPNv4, L3VPN, dual-homed; RR: pe1+pe2)        │
│  - 112 CE + host containers (34 CE, 78 hosts; 3 VRFs: CORP/VOICE/GUEST) │
└────────────────────┬────────────────────────────────────────────────────┘
                     │
          ┌──────────▼──────────┐
          │  TRAFFIC GENERATOR  │
          │  (diurnal flows,    │
          │   nc/iperf3)        │
          └──────────┬──────────┘
                     │
          ┌──────────▼──────────────────┐
          │  FAULT INJECTOR             │
          │  (netem/tc/vtysh/kill/etc)  │
          │  writes labels.jsonl        │
          └──────────┬──────────────────┘
                     │
        ┌────────────┴────────────┬──────────────┬──────────────┐
        │                         │              │              │
    ┌───▼────┐              ┌────▼────┐    ┌───▼────┐    ┌────▼────┐
    │ SNMP   │              │ Syslog  │    │ IPFIX  │    │Controller
    │ Telegraf               Promtail  │    │nfacctd │    │Prometheus
    └───┬────┘              └────┬────┘    └───┬────┘    └────┬────┘
        │                        │             │              │
        │ (all tagged with       │             │              │
        │  device label)         │             │              │
        │                        │             │              │
    ┌───▼─────────────┐   ┌─────▼───────┐   ┌▼──────────────┐
    │ VictoriaMetrics │   │    Loki     │   │  nfacctd DB   │
    │ (TSDB)          │   │ (Log store) │   │ (Flow agg)    │
    └───┬─────────────┘   └─────┬───────┘   └┬──────────────┘
        │                       │           │
        └───────────────────────┼───────────┘
                                │
                        ┌───────▼────────┐
                        │  DATA API      │
                        │  (FastAPI)     │
                        │  JOIN on device│
                        │  + time        │
                        └───────┬────────┘
                                │
                        ┌───────▼────────────────┐
                        │ LABELED PARQUET        │
                        │ (40-col dataset)       │
                        │ - metrics              │
                        │ - events               │
                        │ - flows                │
                        │ - is_fault (LABEL)     │
                        └───────┬────────────────┘
                                │
        ┌───────────────────────┼───────────────────────┐
        │                       │                       │
    ┌───▼──────────┐    ┌──────▼────────┐   ┌─────────▼────┐
    │ ML Training  │    │ Visualization │   │ RAG Corpus   │
    │ (supervised/ │    │ (Grafana)     │   │ (topology,   │
    │  anomaly)    │    │               │   │  runbooks)   │
    └──────────────┘    └───────────────┘   └──────────────┘
```

---

## Device-Health / Environmental Terms

**DOM / DDM (Digital Optical Monitoring / Digital Diagnostic Monitoring)** — the
SFF-8472 standard that exposes five readings from an optical transceiver:
temperature, supply voltage, laser bias current, Tx power and Rx power. In the
dataset: `xcvr_temp_c`, `xcvr_rx_power_dbm`, `xcvr_tx_bias_ma`. **Modelled here** —
containers have no optics.

**Laser bias current** — the drive current a transceiver applies to its laser
diode. As the diode ages its quantum efficiency falls, so the control loop pushes
more current to hold output power constant. A steadily rising bias is the classic
weeks-ahead predictor of laser failure. When bias climbs *while* Rx power falls and
no link-down event occurs, that divergence is the signature of the `gray_failure`
scenario.

**ENTITY-SENSOR-MIB (RFC 3433)** — the standard SNMP MIB real routers use to expose
physical sensors (`entPhySensorValue`, `entPhySensorType`, `entPhySensorScale`,
`entPhySensorPrecision`). Our modelled chassis metrics mirror its shape so the
dataset is structurally comparable to real gear.

**Idle-dominated power** — network devices draw most of their maximum power at zero
load. Measured on a Cisco CRS-3: 11.07 kW idle against 12.3 kW maximum (Vishwanath
et al., IEEE JSAC 2014). Consequently `device_power_watts` is modelled as
`P_idle + (P_max - P_idle) x utilization` with `P_idle = 0.87 x P_max`, **not** as a
quantity proportional to traffic.

**Thermal mass** — a chassis heats and cools gradually, so `device_temp_c` lags
load rather than tracking it instantly (implemented as an EMA toward the target
temperature). The lag is what makes temperature a *precursor* rather than a
restatement of utilization.

**Arrhenius vs linear temperature coupling** — the Arrhenius model predicts failure
rates roughly doubling per 10–15 °C. Field data disagrees in the normal operating
range: below ~50 °C internal device temperature, errors grow **linearly** with
temperature (El-Sayed et al., SIGMETRICS 2012). `temp_failure_scale()` is therefore
linear.

**Per-POP ambient temperature** — devices in one POP share an inlet temperature,
because real racks heat together. This makes `device_temp_c` spatially correlated
across the topology graph, giving a graph-aware model a signal that no per-device
metric provides.

**Queue backlog** — bytes standing in an interface's qdisc tree
(`q_backlog_bytes`, read from `tc -s qdisc`). The queue fills *before* latency
rises and long before loss starts, so it leads `tunnel_latency_ms`. **Real**, not
modelled.

## Streaming terms

**Consumer group** — a set of Kafka consumers sharing one `group.id` and one set
of committed offsets. Two *different* groups reading one topic each receive a full,
independent copy: this is the whole mechanism behind the predictive and copilot
pipelines being decoupled (`streaming/consume.py`). Two consumers in the *same*
group would split the partitions between them instead — a common mistake that
silently halves each one's view.

**Committed offset** — the per-(group, partition) position Kafka remembers as "this
group has processed up to here." Restarting a consumer resumes there, which is why
the predictive pipeline commits only *after* a feature window is built
(`enable_auto_commit=False`): committing on receipt would lose training data on a
restart mid-window.

**Auto-offset-reset (earliest vs latest)** — where a group with no committed offset
starts. `earliest` replays all retained history (predictive: it needs history to
build windows); `latest` starts at the live tip (copilot: history is not its job).
These two are incompatible in a single group, which is the reason there are two.

**Partition key** — the record field Kafka hashes to choose a partition. Ordering
is guaranteed *within* a partition only, so keying every record by `device` turns
that into a per-device ordering guarantee. Round-robin (no key) would interleave a
device's buckets across partitions and destroy window ordering.

**Cross-topic ordering (the absence of it)** — Kafka provides no ordering between
different topics. A consumer reading metrics and fault labels from two topics can
see a label *after* the window it should have tagged; measured here as 4,000
windows built with 0 labelled. Fixed by draining labels to their end offsets before
windowing (`streaming/consume.py:drain_faults`).

**KRaft** — Kafka's built-in Raft consensus mode, replacing the external ZooKeeper
ensemble. One container instead of two, and one fewer config file to keep in the
air-gap bundle.

**Advertised listener** — the address Kafka hands a client *after* bootstrap, which
the client then connects to directly. The broker runs two of them (in-lab
`172.20.20.60:9092`, host `127.0.0.1:29092`) because a single advertised address
cannot be routable from both the clab network and the host.

**Log template / template_id** — a syslog line with its variable parts masked
(`neighbor <IP> Down after <N> seconds`) plus a stable hash of the residue. Shipping
templated events instead of raw text is 10–50× smaller and keeps log parsing out of
the training loop (`streaming/bridge.py:templatize`).

## Related Documentation

- **PLAN.md** — Architecture and execution phases (this glossary supports Phase 2).
- **topology-spec.yaml** — Single spec; generator transforms it into all node configs.
- **faults/README.md** — Fault scenarios, label schema, injector details.
- **dataapi/schema/** — Parquet schema definitions (columns, types, units).
- **telemetry/docker-compose.yml** — Telemetry stack (VictoriaMetrics, Grafana, Loki, Telegraf, nfacctd).

---

**Navigation:** ← [04 Usability Cheatsheet](04_USABILITY_CHEATSHEET.md) | [Back to Start](01_PROJECT_OVERVIEW.md)

---

*Generated for the ISRO BAH 2026 air-gapped predictive NOC copilot project. Minimum word count: 1500 words. Updated June 29, 2026 — reflects Phase 6 MPLS-core redesign (24 P / 12 PE / 148 lab containers / 21 fault scenarios).*
