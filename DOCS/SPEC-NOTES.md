# SPEC-NOTES — Design decisions for generate.py

## What generate.py must produce

From `topology-spec.yaml`, the generator emits:
- `clab.yml` (Containerlab topology)
- `configs/{node}/frr.conf` per node
- `configs/{node}/daemons` per node
- `configs/{node}/90-mpls.conf` per P/PE node
- `configs/{node}/snmpd.conf` per node (P, PE, CE)
- `configs/{node}/wg0.conf` per CE node
- `configs/{node}/qos.sh` per CE node

## Node taxonomy and counts

| Role | Count | FRR daemons | Notes |
|------|-------|-------------|-------|
| P  | 24 (6 POPs × 4) | ospfd, ldpd | No BGP, no VRFs. Core LSR. Multi-area OSPF (each POP = area 1–6; inter-POP backbone = area 0). |
| PE | 12 (2 per POP) | ospfd, ldpd, bgpd | MP-BGP VPNv4, 3 VRFs per PE. Dual-homed to the 2 PE-facing P in its POP. |
| CE | 34 (24 branch + 6 hub + 4 dc) | bgpd (per-VRF instance) | eBGP to PE; one `router bgp <asn> vrf vrf_<VRF>` per VRF. Kernel vrf devices bound via clab exec. |
| host | 78 (1 per site-VRF) | none (multitool image) | Traffic source/sink. branch=2 VRFs, hub/dc=3 VRFs each. |

FRR nodes: 24 P + 12 PE + 34 CE = **70**. Lab containers: 70 FRR + 78 hosts = **148**. Total including 9 telemetry/infra containers: **~157**. At 50–150 MB each — comfortable on 108 GB / 19 cores.

### Option A — per-VRF host separation (structural kernel VRF isolation)

Each site gets ONE host per VRF it serves on its OWN /24. The CE creates a
kernel `vrf` device per VRF (via clab exec: `ip link add vrf_CORP type vrf
table 10`, etc.) and binds BOTH the PE-uplink iface AND the LAN iface for that
VRF into it. Each VRF has its own FIB (routing table 10/20/30 for CORP/VOICE/GUEST).

FRR runs a separate bgpd instance per VRF (`router bgp <asn> vrf vrf_CORP`),
so each VRF process only sees and advertises routes in its own FIB. Cross-VRF
forwarding is structurally impossible — the kernel drops it, no iptables needed.

VRF table numbering: CORP=10, VOICE=20, GUEST=30 (matches rd_community last octet;
`ip route show table 10` == CORP, etc.).

## Addressing derivation (implement exactly this in generate.py)

### Loopbacks

```
p{i}.lo  = 10.255.1.{i}/32          i = 1..p_count
pe{i}.lo = 10.255.2.{i}/32          i = 1..pe_count
ce_branch{i}.lo = 10.255.3.{i}/32   i = 1..branch_count
ce_hub{i}.lo    = 10.255.4.{i}/32   i = 1..hub_count
ce_dc{i}.lo     = 10.255.5.{i}/32   i = 1..dc_count
```

All loopbacks go into OSPF area 0 on P/PE nodes. CE loopbacks are NOT in OSPF (CE is not in the provider IGP).

### P-P core links (/31)

The P-P fabric is POP-structured, not a full mesh. Two tiers:

**Intra-POP links (area K, cost 10):** Within each POP, the 4 P routers form a full mesh.
C(4,2) = 6 links per POP × 6 POPs = 36 intra-POP links. All 6 are in the POP's own OSPF
area (area K, where K = POP index 1–6).

**Inter-POP backbone links (area 0, cost 100):** A ring POP1→2→3→4→5→6→1 plus 3 chords
[[1,4],[2,5],[3,6]] = 9 inter-POP adjacencies. Each adjacency is implemented as 2 redundant
parallel links (sharing one SRLG conduit), so 9 × 2 = 18 inter-POP links. All are in OSPF
area 0. Total P-P links: 36 + 18 = **54**.

Addressing: sequential /31s from 10.0.0.0 for intra-POP pairs, continuing from where
intra-POP leaves off for inter-POP pairs.

```
pair k (0-indexed): network = 10.0.0.{2k}/31
  lower-index router: 10.0.0.{2k}    (.0 of /31)
  higher-index router: 10.0.0.{2k+1} (.1 of /31)
```

Example POP1 intra-POP: pairs (p1,p2), (p1,p3), (p1,p4), (p2,p3), (p2,p4), (p3,p4)
→ 6 /31s starting at 10.0.0.0. POP2 (p5–p8) continues at the next available /31.

**ABRs = first 2 P per POP:** p1,p2 (POP1); p5,p6 (POP2); p9,p10 (POP3); p13,p14 (POP4);
p17,p18 (POP5); p21,p22 (POP6). ABRs participate in both area 0 and their POP area.
PE-facing P = last 2 per POP (e.g. p3,p4 in POP1): pure intra-area routers.

### P-PE links (/31)

Each PE is assigned to a POP by its index: `pop_k = ceil(pe_i / 2)` (pe1+pe2 → POP1,
pe3+pe4 → POP2, …, pe11+pe12 → POP6). Within its POP, each PE dual-homes to the two
PE-facing P routers (the last 2 per POP, i.e. p3+p4 in POP1, p7+p8 in POP2, etc.).
This gives every PE 2 uplinks and eliminates any single-P failure as a PE-outage.

Addressing: sequential /31s from 10.0.1.0. Each PE contributes 2 entries (primary +
secondary uplink):

```
PE{i} primary-P link (0-indexed from i=1):   network = 10.0.1.{4*(i-1)}/31
  PE side:   10.0.1.{4*(i-1)}
  P  side:   10.0.1.{4*(i-1)+1}
PE{i} secondary-P link:                       network = 10.0.1.{4*(i-1)+2}/31
  PE side:   10.0.1.{4*(i-1)+2}
  P  side:   10.0.1.{4*(i-1)+3}
```

Total P-PE links: 12 PEs × 2 uplinks = **24 links**.

### CE-PE links (/30, one per VRF)

A CE gets one eBGP peering per VRF it participates in. Each peering uses its own /30 sub-interface on the PE (VRF-bound) and a corresponding interface on the CE.

```
vrf_idx: CORP=0, VOICE=1, GUEST=2
site_linear_idx: global 0-based index across all CEs
  branch CEs: idx 0..branch_count-1
  hub    CEs: idx branch_count..branch_count+hub_count-1
  dc     CEs: idx branch_count+hub_count..total_ce-1

network = 10.1.{vrf_idx}.{site_linear_idx * 4}/30
  PE interface (VRF): .1 of the /30
  CE interface:       .2 of the /30
```

Branch CEs only get CORP and VOICE (not GUEST); skip vrf_idx=2 for branch sites.

Example (branch0, CORP): 10.1.0.0/30 → PE=10.1.0.1, CE=10.1.0.2
Example (branch0, VOICE): 10.1.1.0/30 → PE=10.1.1.1, CE=10.1.1.2
Example (hub0, CORP): 10.1.0.16/30 → PE=10.1.0.17, CE=10.1.0.18
  (hub0 has site_linear_idx=4 → 4*4=16)

### Customer LANs (/24) — Option A, one per (site, VRF)

Each (site, VRF) pair gets its own /24 so hosts in different VRFs at the same
site live on different subnets:

```
site_linear_idx k, vrf_idx v (CORP=0, VOICE=1, GUEST=2):
  octet   = k*4 + v
  network: 192.168.{octet}.0/24
  CE gw:   192.168.{octet}.1/24   (one CE LAN interface per VRF)
  host:    192.168.{octet}.10/24  (static, assigned via exec: in clab.yml)
```

Collision-free: each site owns the contiguous block [k*4 .. k*4+3]; only slots
0..2 are used (slot 3 spare), so per-site ranges never overlap. With 8 sites the
max third octet is 7*4+2 = 30.

Examples:
  branch0 CORP:  192.168.0.0/24    branch0 VOICE: 192.168.1.0/24
  hub0 (idx 4) CORP: 192.168.16.0/24  hub0 GUEST: 192.168.18.0/24

The CE's per-VRF bgpd process (`router bgp <asn> vrf vrf_<VRF>`) advertises
only its own VRF's /24 via `network` statement — no per-neighbor outbound filters
needed since each process is scoped to one VRF's FIB.

`redistribute connected` is NOT used on either CE or PE (it would leak /30 PE-CE
uplinks). PE VRF-BGP sessions learn CE LANs via eBGP; `export vpn` then propagates
them into VPNv4 automatically — no explicit PE network statements needed.

### CE BGP ASNs

```
branch CE{i} (1-based): AS = 65100 + i      → 65101..65104
hub    CE{i}:           AS = 65200 + i      → 65201..65202
dc     CE{i}:           AS = 65300 + i      → 65301..65302
```

### WireGuard overlay

```
hub CE{i}:     172.16.0.{i}/24      → 172.16.0.1, 172.16.0.2
branch CE{i}:  172.16.0.{10+i}/24  → 172.16.0.11..172.16.0.14
dc     CE{i}:  172.16.0.{20+i}/24  → 172.16.0.21..172.16.0.22
```

Each spoke peers to both hubs (two `[Peer]` entries in wg0.conf). Hubs peer to all spokes. Keys are generated via `wg genkey | tee privkey | wg pubkey > pubkey` at generation time; pubkeys are cross-injected into peer configs.

## FRR config conventions (from martimy/clab_mpls_frr reference)

### P router (LSR — no BGP, no VRFs)

Multi-area OSPF: loopbacks go into area 0 (so all router-IDs are reachable via the backbone);
each link uses the area and cost determined by the generator from topology-meta.json
(intra-POP links → area K / cost 10; inter-POP backbone links → area 0 / cost 100).

```
frr defaults traditional
hostname p{i}
no ipv6 forwarding
!
interface lo
 ip address 10.255.1.{i}/32
 ip ospf area 0                         # loopback always in area 0
!
interface eth{k}   # one per connected link
 ip address {link_addr}/31
 ip ospf area {{link.area}}             # area K for intra-POP; area 0 for inter-POP
 ip ospf cost {{link.ospf_cost}}        # 10 for intra-POP; 100 for inter-POP
 ip ospf network point-to-point
!
router ospf
 ospf router-id 10.255.1.{i}
 passive-interface lo
!
mpls ldp
 router-id 10.255.1.{i}
 address-family ipv4
  discovery transport-address 10.255.1.{i}
  interface eth{k}
  exit
 exit-address-family
!
```

`90-mpls.conf` (sysctl): `net.mpls.conf.eth{k}.input=1` for each core interface + `net.mpls.platform_labels=1048575`.

### PE router (LER — OSPF + LDP + MP-BGP VPNv4)

Same as P for OSPF and LDP sections. Add:

```
router bgp 65000
 bgp router-id 10.255.2.{i}
 neighbor 10.255.2.{j} remote-as 65000    # for each other PE j ≠ i
 neighbor 10.255.2.{j} update-source lo
 !
 address-family ipv4 vpn
  neighbor 10.255.2.{j} activate
 exit-address-family
!
router bgp 65000 vrf CORP
 bgp router-id 10.255.2.{i}
 neighbor 10.1.0.{ce_ip} remote-as {ce_as}
 neighbor 10.1.0.{ce_ip} activate
 !
 address-family ipv4 unicast
  neighbor 10.1.0.{ce_ip} activate
  redistribute connected
  label vpn export auto
  rd vpn export 65000:10
  rt vpn both 65000:10
  export vpn
  import vpn
 exit-address-family
!
# Repeat for VOICE (65000:20) and GUEST (65000:30)
```

CE-facing interfaces on PE: bind to VRF with `ip vrf forwarding CORP` (or `vrf CORP` block at top of frr.conf). Use sub-interfaces or separate ethX per VRF.

### CE router (per-VRF bgpd instances + kernel VRF devices)

CEs use structural VRF isolation: one kernel `vrf` device per VRF, with both the
PE-uplink iface and LAN iface bound into it. FRR runs one bgpd per VRF:

```
# clab exec: creates VRF devices (CORP=table 10, VOICE=20, GUEST=30)
ip link add vrf_CORP type vrf table 10
ip link set vrf_CORP up
ip link set eth1 vrf vrf_CORP   # PE uplink for CORP
ip link set eth2 vrf vrf_CORP   # LAN for CORP
ip link add vrf_VOICE type vrf table 20
ip link set vrf_VOICE up
ip link set eth3 vrf vrf_VOICE
ip link set eth4 vrf vrf_VOICE

# frr.conf: one bgpd process per VRF
router bgp {ce_as} vrf vrf_CORP
 bgp router-id 10.255.{type_offset}.{i}
 neighbor {pe_corp_ip} remote-as 65000
 !
 address-family ipv4 unicast
  neighbor {pe_corp_ip} activate
  network 192.168.{k*4+0}.0/24
 exit-address-family
exit
!
router bgp {ce_as} vrf vrf_VOICE
 bgp router-id 10.255.{type_offset}.{i}
 neighbor {pe_voice_ip} remote-as 65000
 !
 address-family ipv4 unicast
  neighbor {pe_voice_ip} activate
  network 192.168.{k*4+1}.0/24
 exit-address-family
exit
```

Each VRF process only sees its own FIB → cross-VRF forwarding is structurally
impossible. No per-neighbor outbound filters or iptables rules needed.

### daemons file

- P nodes:  `ospfd=yes, ldpd=yes, bgpd=no`
- PE nodes: `ospfd=yes, ldpd=yes, bgpd=yes`
- CE nodes: `bgpd=yes, ospfd=no, ldpd=no`

All other daemons: `no`. `vtysh_enable=yes` everywhere.

### agentx (SNMP)

Add to frr.conf on all SNMP-instrumented nodes (PE, CE):
```
agentx
```
Requires snmpd running with `master agentx` before FRR starts. start.sh order: snmpd → FRR.

## PE-PE BGP: full-mesh vs RR decision

At `pe_count=12`: C(12,2) = 66 iBGP sessions as full-mesh is impractical. Route-reflector
mode is mandatory and auto-enabled by `generate.py` when `pe_count > 5`.

Current configuration: `route_reflector: true`, `rr_nodes: ["pe1","pe2"]`. pe1 and pe2 serve
as RR servers (they peer to each other as standard iBGP). pe3–pe12 are RR clients, each
peering only to pe1 and pe2 — resulting in 12 × 2 = 24 iBGP sessions total.

Full-mesh iBGP is still used when `pe_count ≤ 5` and `route_reflector: false`.

## Link addressing /30 vs /31

Used /31 for P-P and P-PE core links (RFC 3021; FRR supports it natively; saves addresses and removes broadcast domain). Used /30 for CE-PE links because some CE implementations use the .3 address for secondary purposes and /30 is more universally understood for operator-facing peering segments.

## Clab topology structure

In `clab.yml`:
- All FRR nodes: `kind: linux`, `image: {frr_image}`, binds for `frr.conf`, `daemons`, `90-mpls.conf` (P/PE only).
- Host containers: `kind: linux`, `image: {host_image}`, `exec:` to assign IP + default route.
- Links: explicitly listed as `endpoints: ["nodeA:ethX", "nodeB:ethY"]`. Generator must track which interface index each node has used to assign the next `eth{n}`.

Interface assignment rule: eth0 = first link added, eth1 = second, etc. Generator maintains a counter per node.

## Site-to-PE attachment map (generated, not hardcoded)

```python
def pe_for_site(site_linear_idx, pe_count):
    return (site_linear_idx % pe_count) + 1  # 1-based PE index
```

This distributes CEs evenly. With 34 CEs and 12 PEs: PE1 gets sites 0,12,24; PE2 gets 1,13,25; and so on — each PE serves ~2–3 CEs.

## Per-site baseline netem on CE eth0

Each CE deploy exec block applies a baseline `netem` qdisc to `eth0` (the mgmt/transport veth — the interface through which WireGuard tunnels run and over which NOC telemetry travels):

```
tc qdisc replace dev eth0 root netem delay <d>ms <j>ms loss <l>%
```

Values are set by the `site_netem(site_type, idx)` helper in `generate.py`, which is the **single source of truth** for per-site geography impairment:

| site_type | delay (d) | jitter (j) | loss (l) |
|-----------|-----------|------------|----------|
| branch    | ~41 ms    | ~5 ms      | ~0.3%    |
| hub       | ~17 ms    | ~2 ms      | ~0.3%    |
| dc        | ~12 ms    | ~1 ms      | ~0.3%    |

Bounds enforced: delay ≤ 60 ms, jitter ≤ 0.3 × delay, loss ≤ 1%. Within each tier, per-spoke spread is deterministic via the golden-ratio sequence (no two spokes share the exact same value).

**Why eth0:** this is the host-facing transport veth. Applying netem here delays both the WireGuard tunnels (overlay data plane) AND the site's telemetry transport (SNMP polls, IPFIX flows, syslog) — realistic, since NOC telemetry rides the same WAN access link. Verified at ≤1% loss: SNMP, IPFIX, and syslog all remain intact.

**Single source of truth:** `site_netem()` in `generate.py` sets the physical impairment. The controller **measures** it (ping over wg0) but does not define it. The previously-duplicated geography baseline model inside the controller has been removed.

## MPLS depth additions

### New topology-spec.yaml knobs

| Knob | Type | Effect |
|------|------|--------|
| `p_count` | int | Total P routers; structured into POPs by `pop_count` and `p_per_pop` |
| `pe_count` | int | Total PE routers; 2 per POP, auto-assigned by POP index |
| `pop_count` | int | Number of POPs (6 in current design) |
| `p_per_pop` | int | P routers per POP (4 in current design; first 2 = ABRs, last 2 = PE-facing) |
| `multi_area` | bool | Enable multi-area OSPF (area per POP + area-0 backbone); default true at pop_count > 1 |
| `igp_cost_intra` | int | OSPF link cost for intra-POP P-P links (default 10) |
| `igp_cost_inter` | int | OSPF link cost for inter-POP P-P backbone links (default 100) |
| `inter_pop_redundancy` | int | Number of parallel links per inter-POP adjacency (shared SRLG conduit) |
| `inter_pop_chords` | list[[int,int]] | Extra inter-POP adjacencies beyond the ring (e.g. `[[1,4],[2,5],[3,6]]`) |
| `pe_dual_homing` | bool | Each PE connects to two P routers (primary + secondary PE-facing P in its POP) |
| `bfd_core` | bool | Enables BFD on all P-PE and P-P core links (FRR `bfd` stanza per interface) |
| `hub_hub_wg` | bool | Adds a direct WireGuard peering between hub CEs (hub1↔hub2) for spoke-to-spoke fast-path |
| `route_reflector` | bool | Enables RR mode: two PEs act as route-reflectors; remaining PEs are clients |
| `rr_nodes` | list[str] | Which PE nodes serve as RRs when `route_reflector: true` (e.g. `["pe1","pe2"]`) |

`route_reflector` is auto-enabled by generate.py when `pe_count > 5`; set it explicitly to force RR mode at any scale.

### MPLS telemetry sidecar

`noc-ldp-metrics` (container `172.20.20.58`) is a lightweight exporter that polls P and PE
nodes via vtysh JSON and pushes Prometheus-format metrics to VictoriaMetrics at:

```
POST http://172.20.20.50:8428/api/v1/import/prometheus
```

**Original metrics:** `mpls_ldp_session_state{device,neighbor}`, `mpls_ldp_session_uptime_seconds`,
`mpls_label_table_entries{device}` (now extended to cover all 12 PE).

**New metrics added in Phase 6 (MPLS core observability):**

| Metric | Labels | Scope | Interpretation |
|--------|--------|-------|----------------|
| `ospf_neighbor_state` | `{device,peer}` | P+PE (~156 series) | 1=Full, 0=not; drops reveal node/link/POP faults |
| `ospf_spf_last_duration_ms` | `{device}` | P+PE | SPF compute time; elevated during area_flap |
| `ospf_spf_last_executed_ms` | `{device}` | P+PE | Msec-since-boot of last SPF run; jumps on each reconvergence |
| `mpls_lsp_count` | `{device}` | P+PE | Installed MPLS forwarding entries (~107/node under normal operation) |
| `bgp_peer_established` | `{device}` | PE only | Established iBGP/VPNv4 peers (RR pe1/pe2 = 22; client PEs = 4) |

These metrics map directly to the new fault scenarios: `ospf_area_flap` → spikes in
`ospf_spf_last_duration_ms`; `p_node_failure` / `srlg_cut` / `pop_isolation` → drops in
`ospf_neighbor_state`; `rr_failure` → collapse of `bgp_peer_established` on the affected RR.

**SNMP coverage extended:** Telegraf SNMP agents scaled 52 → **70** (spliced from the
generator-emitted `snmp_agents.toml`, which now includes p9–p24 and pe11–pe12).

**Grafana NOC Overview:** 7 → **11 panels** (added: OSPF Adjacency State, OSPF SPF Duration,
MPLS LSP Count, BGP Peers Established).

### Route-reflector topology

When `route_reflector: true` and `rr_nodes: ["pe1","pe2"]`:

- **pe1 and pe2** become RR servers with `cluster-id` = their own loopback address (10.255.2.1 and 10.255.2.2 respectively). They peer to each other as standard iBGP (no RR relationship between servers).
- **pe3–pe12** (all non-RR PEs) are configured as RR clients: each peers only to pe1 and pe2 (`neighbor 10.255.2.1 route-reflector-client` on the RR side). Clients have no direct iBGP sessions between themselves.
- Full-mesh iBGP is only used when `pe_count ≤ 5` and `route_reflector: false`.

## POP multi-area design decisions

### Why 6 POPs × 4 P per POP

The prior 8-router full-mesh gave every P router a one-hop path to every other P router.
That meant LSPs were trivially short, P-node faults did not cascade (traffic rerouted
within one hop), and all OSPF ran in area 0 with uniform cost=1 links. The redesign makes
the core a realistic multi-region backbone:

- **6 POPs** model geographically distinct regions. With p_per_pop=4, each POP is large
  enough for meaningful intra-region topology (6 intra-POP links, 2 ABRs, 2 PE-facing P)
  yet small enough to keep the total manageable on the lab host.
- **24 P routers** produce multi-hop cross-POP LSPs (verified: pe1→pe11 shows metric 140,
  meaning at least one inter-cost 100 hop, with an MPLS label pushed over ECMP uplinks).

### OSPF area structure

- **Area K (K=1..6):** Each POP's intra-POP links and the loopbacks of its 4 P routers.
  Cost 10 within a POP. No `area range` summarization in v1 (deferred: would require
  careful prefix allocation to avoid ambiguity during fault scenarios).
- **Area 0 (backbone):** All inter-POP links (ring + chords) and the loopbacks/inter-links
  of ABRs. Cost 100 for inter-POP links. ABRs (first 2 P per POP) are in both area 0 and
  their POP area simultaneously.
- **Loopbacks:** All P and PE loopbacks go into area 0 on P/PE nodes. This ensures that
  all router-IDs are reachable via the backbone and that LDP transport addresses resolve
  across the full mesh of POPs.

### IGP cost as the TE construct

`igp_cost_intra=10` / `igp_cost_inter=100` creates a 10× cost ratio between intra-POP
and inter-POP paths. OSPF prefers intra-POP paths for same-POP destinations and uses
inter-POP backbone only when crossing regions. With 9 inter-POP adjacencies (ring + 3
chords), ECMP over multiple inter-POP paths naturally occurs. The `path_asymmetry` fault
exploits this by shifting cost in one direction to make forward and return paths diverge.

### SRLG conduits

Each inter-POP adjacency is implemented as 2 physical links sharing one SRLG conduit
(a named group in `topology-meta.json`). A single fibre cut takes down both links together —
the `srlg_cut` fault scenario models this. SRLG conduit names: `srlg_pop{A}_{B}` where
(A,B) are the connected POP indices. Ring conduits: pop1_2, pop2_3, pop3_4, pop4_5, pop5_6,
pop6_1. Chord conduits: pop1_4, pop2_5, pop3_6.

### topology-meta.json contract

`generate.py` emits `topology/topology-meta.json` alongside `clab.yml`. This file is the
machine-readable contract between the generator, the fault orchestrator, and the dataapi.
Schema:

```json
{
  "pop_count": 6,
  "p_per_pop": 4,
  "multi_area": true,
  "pops": {"pop1": ["p1","p2","p3","p4"], ...},
  "abrs": ["p1","p2","p5","p6","p9","p10","p13","p14","p17","p18","p21","p22"],
  "pe_pop": {"pe1": "pop1", "pe2": "pop1", ...},
  "p_core_ifaces": {"p1": {"eth0": ..., "eth1": ..., ...}},
  "srlgs": {"srlg_pop1_2": ["p1:eth4", "p2:eth4", "p5:eth2", "p6:eth2"], ...},
  "inter_pop_links": [["p1","p5"], ["p1","p5"], ...],
  "pop_inter_links": {"pop1": ["p1","p2"], ...}
}
```

The fault orchestrator loads this file at startup and resolves all link sets at runtime —
no hardcoded interface names in any fault scenario. Anti-drift: SNMP `snmp_agents.toml` and
`nfacctd` `device_map.txt` are also emitted by the generator from the same topology data.

## Validation checklist for generate.py output

After generation, verify manually or via script:
1. No duplicate IP addresses anywhere in generated configs.
2. RR servers (pe1, pe2) each have exactly `pe_count - 1` = 11 iBGP neighbors. RR clients
   (pe3–pe12) each have exactly 2 neighbors (pe1, pe2). Full-mesh iBGP is absent.
3. Every CE-VRF combination has exactly one /30 link to its PE VRF interface,
   one dedicated /24 LAN, and one host container (Option A). No two (site,VRF)
   LANs share a /24. Total hosts = sum over sites of #VRFs served (=78 with 24 branch + 6 hub + 4 dc).
4. LDP is only on P and PE nodes, only on core-facing interfaces (never CE-facing).
5. All loopbacks participate in OSPF; all CE-facing interfaces on PE are VRF-bound and NOT in OSPF.
6. WireGuard: each spoke has exactly 2 `[Peer]` entries (one per hub); each hub has `(branch_count + dc_count)` `[Peer]` entries.
