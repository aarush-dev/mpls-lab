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

FRR nodes: 24 P + 12 PE + 34 CE = **70**. Lab containers: 70 FRR + 78 hosts = **148**. Total including 11 telemetry/infra containers: **~159**. At 50–150 MB each — comfortable on 108 GB / 19 cores.

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
Example (hub0, CORP): 10.1.0.96/30 → PE=10.1.0.97, CE=10.1.0.98
  (hub0 has site_linear_idx=24, after 24 branches → 24*4=96)

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
0..2 are used (slot 3 spare), so per-site ranges never overlap. With 34 sites
(24 branch + 6 hub + 4 dc, k=0..33) the max third octet is 33*4+2 = 134
(`generator/generate.py:332` asserts `lin_idx*4+2 < 256`, plenty of headroom).

Examples:
  branch0 CORP:  192.168.0.0/24    branch0 VOICE: 192.168.1.0/24
  hub0 (idx 24) CORP: 192.168.96.0/24  hub0 GUEST: 192.168.98.0/24

The CE's per-VRF bgpd process (`router bgp <asn> vrf vrf_<VRF>`) advertises
only its own VRF's /24 via `network` statement — no per-neighbor outbound filters
needed since each process is scoped to one VRF's FIB.

`redistribute connected` is NOT used on either CE or PE (it would leak /30 PE-CE
uplinks). PE VRF-BGP sessions learn CE LANs via eBGP; `export vpn` then propagates
them into VPNv4 automatically — no explicit PE network statements needed.

### CE BGP ASNs

`ce_asn_base` (`topology-spec.yaml:145-148`): branch=65101, hub=65201, dc=65301. `asn = asn_base[site_type] + (idx-1)` (`generate.py:315`):

```
branch CE{i} (1-based): AS = 65100 + i      → 65101..65124  (24 branches)
hub    CE{i}:           AS = 65200 + i      → 65201..65206  (6 hubs)
dc     CE{i}:           AS = 65300 + i      → 65301..65304  (4 dc)
```

### WireGuard overlay

`generate.py:441-459`. The dc block sits at `.101+`, not adjacent to hub/branch, so the
range stays disjoint as branch_count grows (a `.21..` dc block would have collided with
the branch spoke range once branch_count passed ~10):

```
hub CE{i}:     172.16.0.{i}/24        → 172.16.0.1..172.16.0.6      (6 hubs)
branch CE{i}:  172.16.0.{10+i}/24     → 172.16.0.11..172.16.0.34    (24 branches)
dc CE{i}:      172.16.0.{100+i}/24    → 172.16.0.101..172.16.0.104  (4 dc)
```

Every spoke (branch + dc, 28 total) peers to **all 6 hubs**, not just two — 168
spoke-hub tunnels (`generate.py:471-486`). With `hub_hub_wg: true`, adjacent hub
pairs (hub1+hub2, hub3+hub4, hub5+hub6) also get a direct hub-hub tunnel — 3 more.
Keys are generated via `wg genkey | tee privkey | wg pubkey > pubkey` at generation
time; pubkeys are cross-injected into peer configs.

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
as RR servers (they peer to each other as standard iBGP, 1 session). pe3–pe12 (10 clients)
each peer only to pe1 and pe2 (2 sessions each, 20 total) — **21 iBGP sessions total**
(`generate.py:278-295`), vs. 66 for full mesh.

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
| `bgp_peer_established` | `{device}` | PE only | Distinct established iBGP peers (a peer active in both `ipv4Unicast` and `ipv4Vpn` counts once — was double-counted before this repair pass): RR pe1/pe2 = 11; client PEs = 2 |

These metrics map directly to the new fault scenarios: `ospf_area_flap` → spikes in
`ospf_spf_last_duration_ms`; `p_node_failure` / `srlg_cut` / `pop_isolation` → drops in
`ospf_neighbor_state`; `rr_failure` → collapse of `bgp_peer_established` on the affected RR.

**SNMP coverage extended:** Telegraf SNMP agents scaled 52 → **70**
(`telemetry/telegraf/telegraf.conf:27-`, verified by count), now including p9–p24 and
pe11–pe12. This list is a hand-maintained static array, not generator-derived — there is
no `snmp_agents.toml` (that generator emission was deleted; see "topology-meta.json
contract" above).

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
no hardcoded interface names in any fault scenario. Anti-drift: `nfacctd`'s
`topology/telemetry/device_map.txt` is also emitted by the generator from the same topology
data (`generate.py:706-729`). There is no `snmp_agents.toml` — that emission was deleted.
Telegraf's SNMP agent list (`telemetry/telegraf/telegraf.conf`) is a hand-maintained static
list, not generator-derived; its own comment still refers to the deleted file as something
it "duplicates" — stale, but out of scope for this doc (it's in code, not here).

## Device-health / environmental feature set

Added 19 columns (21 -> 40). Driven by a literature review of what actually predicts
network hardware failure; sources cited inline in the real-vs-modelled table below.

### Why a third entity_type

`entity_type` was `interface | tunnel`. Whole-box signals (CPU, RIB size, chassis
sensors) are per-device, not per-entity. Broadcasting them onto every interface row
would duplicate them ~11x per device. Instead there is now an `entity_type="device"`
row per device per bucket, with `entity` set to the device name. Interface- and
tunnel-scoped columns are NULL on it, and vice versa.

### Real vs modelled — the split that matters

| Real (measured) | Source |
|---|---|
| `if_in_errors`, `if_in_discards`, `if_out_errors`, `if_out_discards` | SNMP IF-MIB, same ifTable walk already running — 4 extra OIDs, no extra round-trips |
| `q_backlog_bytes`, `q_drops` | `tc -s qdisc` on CE uplinks (HTB tree already present) |
| `cpu_pct`, `mem_pct` | one `docker stats` call for all containers |
| `bgp_msg_rx/tx`, `rib_routes`, `ospf_lsa_count` | vtysh JSON |

| Modelled (no sensor exists in a container) | Model provenance |
|---|---|
| `device_power_watts` | Vishwanath et al., IEEE JSAC 2014: CRS-3 measured 11.07 kW idle / 12.3 kW max. Power is idle-dominated, NOT proportional to load. `P = P_idle + (P_max-P_idle)*util`, `IDLE_FRAC = 0.87`. Mytton (J. Ind. Ecology 2024) makes the same argument against kWh/GB models. |
| `device_temp_c` | Ambient + linear load rise, with thermal mass (EMA lag). Failure coupling `temp_failure_scale()` is LINEAR, per El-Sayed et al. (SIGMETRICS 2012), whose field data shows errors growing linearly with temperature below ~50 °C — Arrhenius (2x per 10-15 °C) is a lab model that field data does not support in the normal operating range. |
| `device_fan_rpm`, `device_psu_voltage_v` | Companions to the above; metric shape mirrors RFC 3433 ENTITY-SENSOR-MIB, which is how real routers expose temp/fan/voltage/power over SNMP. |
| `xcvr_temp_c`, `xcvr_rx_power_dbm`, `xcvr_tx_bias_ma` | SFF-8472 DOM/DDM. Rising laser bias with falling rx power is the canonical laser end-of-life signature. |

Noise is deliberate and calibrated: `POWER_SIGMA_FRAC = 0.35`. arXiv 2602.22339 regressed
real package power on 1400+ telemetry params over 10k machines and got R² = 0.33 — real
power is only weakly predictable from load, and a deterministic `P = f(util)` would leak
the label.

### Design decisions

- **One shared model module.** `telemetry/envmodel.py` holds every modelled formula as
  pure functions and is imported by BOTH the live sidecar (`telemetry/env-metrics.py`)
  and the synthetic generator. Writing the physics twice would let live and synthetic
  drift apart silently.
- **Ambient temperature is per-POP, not per-device.** Real racks heat together. This
  makes temperature a spatially correlated feature across the topology graph — a signal
  a GNN can exploit and that no per-device metric provides. `pop_ambient_c()` is
  deterministic (golden-ratio spread, no RNG) so live and synthetic agree.
- **Chassis load is driven by FORWARDING load (the diurnal curve), not control-plane
  CPU.** First cut used `cpu/100`; container CPU sits at ~5%, which left temperature and
  power nearly constant and the features useless. A router's ASICs do the forwarding
  while its CPU idles, so the diurnal offered-load curve is the correct driver. `cpu_pct`
  remains a separate real metric in its own right.
- **One sidecar, not six.** `env-metrics.py` walks the 70 nodes once. Separate scripts
  per signal would mean separate `docker exec` storms over the same nodes.

### Synthetic fault coverage gap (fixed alongside)

`calibrate.py` only defined signatures for the 7 original edge scenarios, so the
synthetic generator could never emit any of the 14 core/catastrophic types that
`faults/orchestrator.py` implements — the dataset claimed 21 fault types and contained 7.
All 21 are now defined. This also made the optical degradation path reachable:
`gray_failure` is the scenario that drives the DOM columns.

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
6. WireGuard: each spoke has exactly `hub_count` `[Peer]` entries (one per hub, currently 6); each hub has `(branch_count + dc_count)` `[Peer]` entries plus 1 more if it's in a `hub_hub_wg` pair.

## Decisions from the 2026-07-26 repair pass (105 audit findings)

**Tunnel telemetry is labelled SIMULATED, not faked as measured.** The old HELP
text implied `sdwan_tunnel_latency_ms`/`_jitter_ms`/`_loss_pct` were straight
measurements. They aren't: only the wg0 ping layer is real, the congestion term
is an M/M/1 model, and the fault term is a netem *qdisc-config* readback, not
something the ping actually traversed (`controller/controller.py:150-181`). A
consumer trusting these as ground-truth measurements would draw the wrong
conclusion about what the model can learn from them, so the HELP strings now
say SIMULATED outright (`controller.py:8-13`) instead of leaving the gap
implicit. Honesty about provenance beats a nicer-sounding metric name.

**Three scenarios (`hub_spoke_congest`, `bgp_cascade`, `brownout`) dropped their
`vm_threshold` probes and became `impact_method: modelled`.** Their probes
threshold on metrics those scenarios don't actually move in an observable way
(`faults/orchestrator.py:267-283`, `325-342`, `348-361`) — the old code was
claiming a real measurement it couldn't produce. Downgrading to a fixed-delay
`modelled` impact is honest about what the orchestrator actually knows for
these three; a fabricated `vm_threshold` label would silently poison
`impact_method`-conditioned downstream analysis.

**The airgap verifier was rewritten because it always passed.** The old capture
ran on `eth0`, which the host's MASQUERADE rule already NATs — so a src-net
filter for container subnets can never match there, and the check was a
guaranteed no-op (`airgap/verify-airgap.sh`, "3. Runtime egress" section).
Moved to `tcpdump -i any` (bridge-side, pre-NAT) plus real failure paths for
missing tcpdump / empty pcap / zero running containers / an actual
`docker pull` during the window. A gate that cannot fail is not a gate.

**Lead time is floored at `4 * step`** (120 s at the default 30 s step) in the
synthetic generator (`synthetic/generate.py:13`), overriding whatever the
calibrated value from the real capture says. A precursor window shorter than a
few buckets gives a model no usable signal to train on regardless of what
really happened in the lab — floor it rather than ship unlearnable positives.

**Both `dataapi/datasets/` and `synthetic/output/` are a mix of stale and current
files — check each before trusting it.** 4 of the 5 `dataapi/datasets/*.parquet`
files are pre-repair 21-column exports and fail `check_dataset.py`'s schema
check; only `dataset_1785032386_1785033870_30s.parquet` passes (49 columns after
`reschema.py` re-joined its labels).
One of the 4 stale files, `dataset_1782715445_1782719045_30s.parquet`, also has
`is_fault.sum() == 0` — a relic of the pre-fix single-bucket-instant label join.
`synthetic/output/` was cleaned out: every file lacking `seed` + `calibrated_from` file metadata was deleted (14 of 16), leaving only the two shipped one-day files, and `check.py` now asserts both keys are present.
Verify column count and run the relevant `check*.py` before using any shipped
file rather than assuming freshness from the directory name.

## Streaming layer (Kafka) — decisions

**Why Kafka at all, given the Parquet path already exists.** The `/datasets`
endpoint is a batch interface: it answers "give me the last hour." Neither
downstream pipeline is batch. The predictive pipeline wants a continuously
advancing window; the copilot wants current state. Polling the data API for both
means two pollers, duplicated joins, and no replay when one falls behind.

**Two consumer GROUPS, not two topics or two producers.** The two pipelines differ
in exactly one dimension that matters — where they start reading. Predictive needs
`earliest` (replay history to fill an L-bucket window); copilot needs `latest`
(history is not its job). Those are incompatible inside one consumer group, and
Kafka already solves it: separate `group.id` values get separate committed offsets
over the same topics, so one producer feeds both and neither can block the other.
Publishing twice, or to per-consumer topics, would double the volume and let the
two views drift apart.

**Records keyed by `device`.** Kafka guarantees order within a partition only. The
predictive windower assumes a device's buckets arrive in production order, so the
key must be the thing that ordering is needed over. An unkeyed (round-robin)
producer would scatter one device's buckets across 6 partitions and silently
corrupt every window.

**Four topics, not one.** Different retention is the reason, not tidiness:
`noc.metrics` is high-volume and only replayed by one consumer (1 day),
`noc.faults` is the training target and must outlive both pipelines (30 days). One
topic would force the longest retention on the largest stream. Auto-create is
disabled on the broker so a typo cannot silently produce a 1-partition,
default-retention topic and lose the ordering design.

**`noc.events` exists because 30 s buckets hide the signal.** A BGP session reset,
hold-timer expiry and reconvergence can all complete inside one bucket, so
`bgp_msg_rx` averaged over 30 s cannot show them. Events ship with exact
timestamps. They are also templated at extraction (`bridge.templatize`) rather than
shipped raw: matched lines drop their text entirely, unmatched lines keep it so the
mask set can be tuned against real unmatched volume.

**Producer runs on the host, not in a container.** It imports `dataapi/export.py`
for the metric maps and column list, so containerising it would mean either a new
image carrying pandas/pyarrow or a second copy of the schema. Running it beside
`dataapi/start.sh` costs one dependency (`kafka-python`, pure Python — no
librdkafka to build offline) and keeps the schema single-sourced.

**Labels are drained to completion before windowing, and re-read on every start.**
Kafka has no cross-topic ordering, so a consumer subscribed to both metrics and
labels can receive a label after the window it should have tagged — measured as
4,000 windows built, 0 labelled. `drain_faults()` reads `noc.faults` to its
captured end offsets first, via `assign()` with no group: labels are small and
idempotent, so re-reading them beats tracking a cursor and guarantees a restart
never trains on a partial label set.

**Timestamps are compared as parsed epochs, never as strings.** The orchestrator
writes `2026-07-26T02:22:30Z`; a pandas-sourced value stringifies as
`2026-07-26 02:22:30+00:00`. `' ' < 'T'` in ASCII, so a string comparison in the
window/fault overlap test returns the wrong answer whenever both formats are in
play.

**The copilot's incident state is classified by recency, not by `t_end`.** The
orchestrator writes its label row exactly once, in the `finally` block at revert
(`faults/orchestrator.py:689-707`), so any record on `noc.faults` describes a fault
that has already ended. Treating "no `t_end`" as "open" would therefore report
every real incident as resolved. Recency is the honest proxy. A genuinely live
incident view needs the orchestrator to publish its existing `campaign_inject`
event — it already prints that JSON at inject time, it just never reaches a topic.
That is a known gap, not a design choice.

## Synthetic `--seed` and reproducibility

**`scenario_id` is drawn from the seeded RNG, not `uuid.uuid4()`
(`synthetic/generate.py:_sid_hex`).** The old uuid4 ids made the generator
non-reproducible: two runs at identical `--days/--step/--scale` produced different
Parquet bytes, so `DATASETS.md`'s determinism claim was false and a `--seed`-based
train/holdout split could not be reproduced from the CLI. Verified after the change:
two runs of `--days 0.05 --seed 99` are byte-identical (`md5sum`
`f49cfe79c9ba7c7cef49a2cc2adea0e7` both times), and ids stay unique (32-bit draw
behind a `{type}-{target}-` prefix).

Consequence: both committed synthetic Parquets were regenerated, because
`_sid_hex` consumes RNG draws and therefore shifts the whole downstream stream.
Fault counts moved (seed 42: 60,440 → 72,295 fault rows) — a different valid draw,
not a behaviour change. Row counts, schema and distributions are unchanged.

**`--seed` splits on episodes, not on calibration.** Both synthetic files come from
the same `profile.json`, so their distributions are shared by construction. Seed 7
shares zero `scenario_id` values with seed 42, which makes it a clean episode-level
holdout — it does not test generalisation to a different network.

## Lead-time priors, theta_SLA, and the deliberately-zero counters

Six defects found by an audit of the three shipped Parquets. Full acceptance output
in the commit body; the gate is `synthetic/verify_fixes.py`.

### `lead_time_s` is now a per-type prior, not a calibrated value

`calibrate.py` used to overwrite `lead_s` with the median `lead_time_s` of the real
capture. That capture is 24.5 minutes at 30 s resolution, which cannot estimate a
lead; it produced ~2 s for most types, and the generator's 4-bucket safety floor
then clamped **every** draw to exactly 120 s. Shipped result: `lead_time_s` CV 0.03,
9 distinct values across 668 episodes, and 98.2% of precursor rows carrying one of
four `time_to_impact_s` values. A discrete-time hazard head cannot learn a
distribution from a constant.

`faults/leadpriors.py` now owns the priors, in **buckets** so they survive a `--step`
change, drawn lognormal with p10/p90 pinned to the range:

| group | buckets | at step=30s | reasoning |
|---|---|---|---|
| `bgp_flap` `ldp_session_flap` `node_failure` `rr_failure` `p_node_failure` `ospf_area_flap` `bgp_cascade` | 4–10 | 2–5 min | control-plane churn is visible only shortly before the session actually drops |
| `congestion` `core_congestion` `hub_spoke_congest` `brownout` | 10–40 | 5–20 min | queues fill slowly; the longest genuinely-observable dataplane precursor |
| `tunnel_degrade` `asymmetric_loss` `policy_drift` `path_asymmetry` `controller_drift` | 8–30 | 4–15 min | overlay/policy degradation, medium horizon |
| `mpls_underlay_failure` `pop_isolation` `core_partition` `srlg_cut` | 6–20 | 3–10 min | some IGP/LDP warning, then the path is gone |
| `gray_failure` | 20–80 | 10–40 min | physical-layer decay — the longest horizon, and why the SFF-8472 columns exist |

The p10 of the 4–10 group is lifted to 1.15× the floor: pinning it exactly at 4
buckets makes the safety net fire on ~10% of that group, and a net catching a tenth
of all draws is shaping the distribution rather than guarding it. The generator
**warns** if the floor fires on >5% of episodes, so this regression cannot recur
silently. A measured median is kept as `lead_s_hint` only when the capture spans a
full `DIURNAL_PERIOD` — never as the value.

Both paths draw from this one module: the generator for the synthetic ramp, and
`faults/orchestrator.draw_ramp_seconds` for the live netem ramp
(`injectors.NetemImpair.ramp(total_seconds=...)`). Live leads are capped at 0.7 ×
`--duration` so a ramp cannot outlast its own fault; at the default 90 s duration
that cap binds almost always, so campaigns need a longer `--duration` to exercise
the untruncated prior.

### `t_impact` is the SLA crossing inside the ramp (`ramp_derived`)

The lead used to move only the label: the ramp itself was a fixed ~4 buckets, so the
impairment slope was identical for the shortest- and longest-lead deciles (measured
0.4129 vs 0.4121) and the lead was unpredictable from telemetry even in principle.

The synthetic ramp is now four knots — `t_start`→0, `t_impact`→`p_cross`,
`t_impact + 0.3·dur`→1 (the calibrated peak), `t_end`→0 — where `p_cross` is the
impairment fraction that breaches `theta_SLA`. So `t_impact` **is** the crossing,
the rising edge spans the drawn lead, and the fault still reaches its measured peak
during the impact phase. Capping the episode at `p_cross` instead was tried first and
left every fault at ~20% of its peak; `check.py`'s per-key ramp gate caught it.

`theta_SLA` per VRF, from `faults/leadpriors.THETA_SLA`:

| VRF | latency | loss |
|---|---|---|
| VOICE | 150 ms | 1.0 % |
| CORP | 250 ms | 2.0 % |
| GUEST | 400 ms | 5.0 % |

**Not derivable from the lab**: `generator/templates/qos.sh.j2` shapes bandwidth only
(HTB rate/ceil/prio — no latency or loss target), so these are stated policy
objectives. The GUEST loss figure is the one with an in-repo anchor: it equals the
controller's `FAILOVER_LOSS_PCT`, the point at which path selection abandons a
tunnel. A tunnel carries every VRF of its site, so the SLA that breaks first is the
strictest one present. In practice the crossing is loss-driven — the calibrated
latency peaks (40–70 ms) sit below every latency objective, so a latency-only
scenario reports `modelled`, which is the honest answer: that signature never
breaches SLA.

`vm_threshold` still wins when a live probe genuinely crosses, and the label row now
carries `t_impact_ramp` alongside, so the two methods can be compared instead of one
silently replacing the other.

### Three counters are deliberately zero

`if_in_errors`, `if_in_discards`, `if_out_errors` are emitted as **0** by the
generator, matching the lab, where veth pairs produce no CRC or input errors. They
were previously a load-dependent Poisson process, which made `if_in_errors > 0` a
perfect synthetic-row detector: a shortcut to the synthetic fault distribution for
anything trained on the mixed corpus, and three features that are zero at live
validation time. The alternative — dropping them from the model's channel list —
would have kept a generator model that is correct for real hardware, but two paths
disagreeing on a column is worse than one honest zero.

They are **reserved for real-hardware deployment**: the OIDs are polled correctly and
will populate on physical switches, at which point they are the literature's
top-ranked failure signal (ClusterRCA, arXiv 2506.20673). The fault path's discard
perturbation moved onto `if_out_discards`, which the lab does measure via
`tc -s qdisc`. `synthetic/check.py` asserts all three stay zero.

### DEFECT 5: pooled "louder under fault" gate was Simpson's-paradox bait

`check.py`'s gate for `if_out_discards`/`q_backlog_bytes`/`q_drops` rising under
fault compared the pooled mean over ALL `is_fault` rows against ALL healthy
rows. Two bugs: (1) each counter only rises for the fault kinds that actually
perturb it (`generate.py`'s `CHURN_FAULTS`/`CONGEST_FAULTS`, now hoisted to
module level as the shared source of truth) — mixing in kinds that never
touch the column dilutes the signal; (2) even filtered to the right kinds, the
pooled mean confounds composition — VRF/loopback interfaces (baseline ~5–10)
draw disproportionately more fault episodes than `eth*` (baseline ~50+), so
the pooled fault mean sat *below* the pooled healthy mean at `--scale 1.0`
(default) while every individual entity's own rate still rose. The gate now
averages the **per-entity** (fault − healthy) delta, weighting each entity
equally instead of by row count — it also tolerates one noisy entity (as few
as ~250 fault rows can land on a single interface) without going flaky, since
it checks the average delta rather than requiring every entity to individually
clear the bar.

### Multi-label labels, ordinal severity, timestamp `ts`

`export.py` emits every overlapping label instead of collapsing to the
highest-severity one: `fault_types` / `severities` / `scenario_ids` /
`impact_methods` / `time_to_impact_s` are index-aligned lists with the primary at
element 0, plus `n_concurrent`. The four legacy scalars keep their names and
positions and hold the primary, so a reader written against the original column
order still works; `fault_type_primary` / `severity_primary` / `scenario_id_primary`
are explicit aliases for readers that would rather say so. `severity` is an ordinal
float (0.33/0.66/1.0) with the string in `severity_label`, and null stays null for
scenarios whose injector ignores severity. `ts` is `timestamp[us, tz=UTC]`.

Concurrency needed the campaign lock split too: it keyed on the bare **device**, so a
device carried at most one fault at a time and `n_concurrent` was 1 everywhere —
nothing for a multi-label head to learn. `faults/orchestrator._lock_key` now keys on
the resource a scenario mutates (interface / tunnel / vrf / neighbor / process), with
`node_failure`, `rr_failure` and `bgp_cascade` device-exclusive because ProcessKill
takes the routing daemon away from anything needing `vtysh`. Two netem installs on
one interface still exclude each other. The synthetic path mirrors it with a
per-`kind` lock and a same-device, different-kind cascade.

Schema went 40 → 49 columns. The shipped real capture was re-joined in place by
`dataapi/reschema.py` (labels re-derived from `faults/labels/labels.jsonl`, metric
columns untouched), which is why its fault rows went 327 → 391.

## Copilot I1: tool registry, not per-tool branches

`copilot/tools/registry.py` is a dict (`TOOLS`: name → adapter method +
description) plus one `dispatch()`, not a branch per tool in `agent/loop.py`.
Three tools (`query_metrics`, `search_logs`, `flows`) share the same F2
mandatory-filter contract (window, device/pattern, limit ≤ `MAX_LIMIT`) with no
per-tool argument differences, so a table beats three near-identical `if` arms.
Ceiling: the day a tool needs args beyond device/pattern/limit/offset, `TOOLS`
grows a per-tool arg schema — not before, YAGNI until then.

**`/flows` window plumbing lands in I1; the freeze guard does not.** dataapi's
`/flows` gained `start`/`end` (epoch s) so a named/forensic window reaches the
flow source. The forensic end-freeze guard (forbid `end > T_snapshot`, ADR-0002)
is R3 work, enforced at the adapter — not built yet. The window itself is a bare
`(start, end)` pair, not the full `WindowContext{start,end,frozen}` (also R3).
Also note: the flow window filters on `docker logs --since/--until`, i.e. log
print time, not a per-record `stamp_updated` filter — approximate, not exact.

## Copilot A1: HttpAdapter — the shape layer that made I1–I4 true on real data

F2 shipped `ToolAdapter` + `StubAdapter` (canned rows) and **no ticket replaced the
stub** — so `/chat` 503'd and no `copilot/` code had ever called dataapi. A1 (#40) is
that stub replacement: `adapter/http.py`, riding F2's shared `serve_rows` pipeline
(extracted from the stub so validate→cap→provenance→page→frame is byte-identical on
live and canned data). Only the fetch is new.

Per ADR-0006 this is the **one** layer that knows endpoint shapes, so every mismatch is
resolved here, never pushed outward:
- **ts → epoch int, here.** `/events` emits ISO (`…Z`), `/flows` emits nfacctd
  `stamp_updated` (`YYYY-MM-DD HH:MM:SS`, naive UTC). The gate compares `start <= ts <=
  end` numerically, so pass-through would `TypeError` **inside the gate**. Parsed to int
  at the adapter; unparseable → `None` → dropped as not-provably-in-window (never a raise).
- **`/metrics` is PromQL, not device/pattern/limit.** A selector is synthesised from
  `Filters` (`device=…`, `pattern` → `__name__=~".*…"`); a result vector has no rows, so
  per-series **latest in-window sample** → one `Evidence` (device/ts from labels+sample).
- **`/events` has no `pattern`/`offset`** → both adapter-side (fetch-then-filter; `serve_rows`
  pages the filtered set), else `pattern` silently no-ops and `next_page` lies.
- **`walk_topology`** = `/topology` → shared `bfs_hops` → **one batched PromQL** over the
  walk's nodes scoped to the window → per-node status. Unknown focus → `()` (never fabricate).
- **Transport faults** (refusal / 5xx) → `AdapterError`, which `registry.dispatch` converts
  to a tool observation — not a raise out of `investigate()` that kills the SSE stream, and
  (critically) for the walk it fires **before** the empty-walk "unknown device" path, so a
  dataapi outage never makes the copilot assert a false fact.

Base URL: `cfg.dataapi_url` (default `http://127.0.0.1:8000`), env `COPILOT_DATAAPI_URL`
overrides. The `/flows` approximate-window caveat (log print time) carries over from I1 —
a relevant flow can print just outside `[start,end]` and read as out-of-window at the gate.

## Copilot I2a: LanceDB direct, embedder profile like the LLM

`copilot/retrieval/` ships the `Retriever` seam (`add` / `search → [Hit(doc,
score)]`) on **embedded LanceDB** (`LanceRetriever`), plus `make_embedder(cfg)`
swapped on `embed_profile` (`nim` | `unsloth-local`) — same profile pattern as the
LLM (ADR-0004). Per ADR-0006 the numpy-cosine/npz interim store was **rejected**;
the scalable LanceDB path is built directly. Provenance (source, node, ts) rides on
every `Doc`, so it survives into each `Hit` (the I4a gate needs it).

- **Embedder is lazy**: nim's `httpx` and local's `SentenceTransformer` load on first
  `encode`, never at construction — so the swap is one config line and the selection
  is testable air-gapped (no server, no model on disk). `HashEmbedder` is a
  deterministic test double (no deps), injected directly like `llm.ScriptedLLM`; it is
  NOT a profile and NOT a store substitute (the rejected numpy path stays rejected).
- **nim/local model ids differ** (endpoint id vs HF repo id), so the env vars are
  distinct: `COPILOT_EMBED_MODEL_NIM` / `COPILOT_EMBED_MODEL_LOCAL` (+
  `COPILOT_EMBED_BASE_URL`). These are non-secret config living in env, not
  `config.yaml`, because `config.py` is another lane's file (F0) — same "constant
  stays local to the lane" call as the adapter's `MAX_LIMIT`.
- **score = `1 − _distance`** (LanceDB cosine distance ∈ [0,2]) → cosine similarity
  ∈ [−1,1]. Real bge/gte vectors go negative; an I2b/I4a threshold must band on
  −1..1, not 0..1.
- **Deferred to I2b/later:** upsert-on-id (`add` is append-only until the S1/S2 seeder
  needs `merge_insert`); real corpus content (S1/S2 seeding).

## Copilot I2b: retrieval tools + topology-hop filter (prefilter, not post-filter)

`search_runbooks` / `search_incidents` register in `copilot/tools/registry.py`
(`RETRIEVAL_TOOLS`) over the I2a `Retriever`; the loop + `/chat` thread an optional
`retriever` through `dispatch`. Both scope the KB by provenance; incidents add the
topology-hop proximity filter (ADR-0006/0007). Wired end to end: a fault question over
`POST /chat` returns a cited runbook + a nearby past incident (`copilot.api.test_api`).

- **Hop filter PREFILTERS, it does not post-filter a top-k.** `search(query, k, source,
  nodes)` pushes both scopes into LanceDB `where(..., prefilter=True)`, so the top-k is
  taken *within* {source=incident ∧ node ∈ near}. A post-filter (search top-k globally,
  then drop far nodes) would return "no matches" whenever the k best incidents are all
  distant while a nearby one sits at rank k+1 — the exact trap I2a's source prefilter
  already avoided. Node-less incidents fall out of `node IN (...)` naturally (can't prove
  proximity). Regression test: `test_hop_filter_prefilters_rather_than_trimming_top_k`.
- **The adapter owns the `/topology` shape, not the registry** (ADR-0006: only the adapter
  knows endpoint shapes). `adapter.hops_within(focus, n)` returns the ≤n-hop node set;
  `hops_within_links` (BFS, `adapter/contract.py`) is the one place that reads the
  `{source,target}` link dict. The registry never touches raw topology JSON. I3's
  `walk_topology_graph` builds BFS + `/metrics` enrich on the same primitive. Default
  radius `DEFAULT_HOPS=2` is a registry constant (like adapter `MAX_LIMIT`) — `config.py`
  is another lane's file; lift to config only if tuned.
- **Provenance is the full triple.** Rendered hits carry `source`, `node`, AND `ts`
  (ADR-0006 wants time-range provenance; the I4a gate needs it). KB text is `sanitize()`d
  inline — incidents can embed untrusted log excerpts (ADR-0016).
- **Bad args are guidance, never a raise** (ADR-0015). Missing `query`, absent retriever,
  and non-int `k`/`hops` (incl. `null`/list → `TypeError`, not just `ValueError`) come back
  as `error: …` observation text so the model can correct. `k` is clamped to `MAX_LIMIT`
  (no paging concept for KB search, unlike the read tools' cap-and-page).
- **KB is optional at the HTTP seam.** `get_retriever` returns `None` unless
  `COPILOT_KB_URI` points at a seeded LanceDB (env, mirroring the I2a embedder vars) — a
  read-only investigation still runs; the search tools report "backend not available" until
  S1/S2 seed a corpus. `None` retriever is why the search tools guard for it.
- **Deferred:** upsert-on-id + real corpus (S1/S2); the default `/chat` KB wiring is live
  but needs a seeded `COPILOT_KB_URI` + an embedder backend (R1) to return real hits.

## Copilot I3: walk_topology_graph (BFS + /metrics enrich, KG flag)

`walk_topology_graph` = deterministic BFS on the **real** `/topology` edges from a focus
`device` (within `hops`, default `DEFAULT_HOPS=2`) + per-node live status from `/metrics`
(ADR-0007). Blast-radius / downstream. Registers in `copilot/tools/registry.py`; `dispatch`
routes it (and the I2b retrieval tools) ahead of the read-tool table since it isn't a
windowed filtered read. Wired end to end over `POST /chat` (`copilot.api.test_api`).

- **The adapter owns the topology+/metrics join** (ADR-0006: only the adapter knows endpoint
  shapes). `adapter.walk_topology(focus, n, window)` returns a `tuple[NodeState,...]`
  (`node`, `hop`, `status`); the registry never touches raw `/topology` or `/metrics` JSON.
  BFS is `bfs_hops` (`adapter/contract.py`) — one function, now shared: `hops_within_links`
  (I2b incident filter) is `set(bfs_hops(...))`, killing the earlier duplicate walk.
- **BFS is deterministic** (acceptance): `bfs_hops` assigns a node's hop AFTER the frontier
  comprehension completes, so set-iteration order can't change level assignment; the walk is
  emitted sorted by `(hop, node)`. Self-check: `test_walk_topology_bfs_is_hop_ordered_and_enriched`.
- **KG is additive, never load-bearing** (acceptance: identical with `kg_enabled` off).
  Structure + status come from real topology+metrics ALONE. The curated KG (a `{node: hint}`
  map) only APPENDS a `[kg: …]` note per node. It is honoured through the flag exactly like
  the retriever: `get_kg(cfg)` returns `None` when `cfg.kg_enabled` is off (→ walk KG-free) OR
  when no source is seeded; ON + a seeded `COPILOT_KG_URI` (JSON, env — `config.py` is another
  lane's file) → the map, threaded loop → `dispatch`. So the flag is real, not vacuous
  (`test_get_kg_respects_flag_and_source`), yet the load-bearing core never depends on it
  (`test_walk_topology_graph_identical_with_kg_off`).
- **No fabricated nodes.** An unknown focus returns `()` from the adapter (not a phantom
  `{focus: 0}`), and the tool reports `error: unknown device …: not in the topology` — never
  a made-up subgraph fed to the model.
- **Provenance + untrusted framing.** Each line is `[topo:<node>] hop <h>: <status>` — the
  `[topo:…]` id is citable (the I4a gate checks citations). `/metrics` labels are the
  untrusted side (ADR-0016), so `status` is `sanitize()`d at the adapter.
- **Bad args are guidance, never a raise** (ADR-0015). Missing `device` and non-int `hops`
  (incl. `null` → `TypeError`) come back as `error: …`. `_hops` shares the coercion with the
  incident filter.
- **Deferred:** the real HTTP adapter batches one PromQL per frontier (the stub scans canned
  rows per node and ignores `window`); "downstream of link X" is served by focusing on an
  endpoint device (link-id parsing is YAGNI until a caller needs it); health thresholds on
  `status` (raw metric summary today); a seeded curated-KG corpus (S-phase, like the KB).

## Copilot I4a: quality gate — deterministic pre-gate + citation check

Stage 1 of the ADR-0008 two-stage gate: pure code, fail-fast, no LLM. Sits between "gathered
evidence" and "allowed to answer" in the F3 loop. Lives in `copilot/agent/gate.py`; the loop
(`investigate`) runs `run_gate` on the terminal answer. Stage 2 (self-judge LLM) + the bounded
agentic retry on fail are **I4b (#14)** — not built here.

- **Structured evidence channel (`Cite`).** `dispatch` now returns `(observation, cites)` where
  each `Cite` is a content-BLIND `{id, source, device, ts}` projection unifying the three source
  shapes (adapter `Evidence.device`, retrieval `Doc.node`, topology node). The gate decides on
  provenance only — never re-parses rendered text (ADR-0006) and can't be swayed by untrusted
  evidence content (ADR-0016). `n_rows = len(cites)`; the loop accumulates cites for the gate.
- **Four deterministic checks (`pre_gate` + `tool_calls_ok`), all ADR-0008:**
  1. **tool calls succeeded** — a guidance error (ADR-0015) from any call → block (I4b retries it).
  2. **≥ N items** (`cfg.gate_min_evidence`, N=2) — thin evidence blocked.
  3. **in-window** — each *windowed* (`metrics|events|flows`) item's ts ∈ the window; a null ts on
     a windowed item **fails** (can't be proven in-window), never skips.
  4. **on-topic** — every entity named in the question has ≥1 support, AND no windowed read is on a
     device the question never named.
- **Topicality is scoped to windowed reads; KB + topo are exempt** — this honours *both* the
  ADR-0008 clause ("evidence device ∈ question entities") *and* ADR-0007 without overriding either.
  KB incidents are hop-relevant and topology walks are blast-radius neighbours (ADR-0007): both
  legitimately cover devices the question never named, so a strict per-item reverse check would
  wrongly block them. Only live telemetry reads must be about the device under investigation.
- **Citation check** (`citation_check`): every device-anchored sentence must carry a `[id]`; a
  non-empty answer must cite something; no citation may name an id absent from the gathered
  evidence (fabricated → block). Directly implements acceptance #2 (uncited claim rejected).
- **On fail the `missing[]` list IS the message** (ADR-0008). The loop emits a `gate` event
  (`{ok:false, missing:[…]}`, ADR-0009 enum, streamed by F4) and returns `cannot answer yet: …`
  instead of the answer. `stopped` stays caps-only (a gate block is not a runaway).
- **Ask-back bypasses the gate.** A clarifying question before any evidence (ADR-0005) is not an
  answer. `ponytail:` detected by a trailing `?` on a no-evidence turn — a known-ceiling heuristic
  (upgrade path: an explicit ask-back signal from the loop/model).
- **Entity extraction is a role-prefix whitelist** (`r|rr|p|pe|ce|asbr` + digits), not NER. This
  is deliberate: a looser `\w+\d+` over-matched protocol/interface tokens (`as65001`, `ge0`, `v4`)
  → each became a "required entity" no evidence could satisfy, wedging the question. `ponytail:`
  upgrade path = intersect with the adapter's topology node set.
- **Review** (Opus-5, two-axis, pre-merge): wired the missing "tool calls succeeded" check;
  implemented the dropped reverse-topicality clause (windowed-scoped, KB/topo exempt); tightened
  the entity regex to kill false-positive required-entities; failed windowed null-ts now blocks;
  moved gate composition into `gate.run_gate` (was loop-level, Feature Envy).
- **Deferred → shipped in I4b (#14), below:** the self-judge LLM call + the bounded agentic retry.

## Copilot I4b: quality gate — self-judge + ≤2 agentic retry

Stage 2 of the ADR-0008 gate + the retry loop. Reuses the F3 agent-loop machinery (ADR-0005) —
no new module: `self_judge` + the retry live in `copilot/agent/loop.py` beside `investigate`.

- **Self-judge = one LLM call over the pre-gate's survivors** (`self_judge(llm, messages, answer)`).
  Re-sends the *running transcript* (the tool-result observations, already adapter-**sanitized** per
  ADR-0016) under an auditor system prompt + the draft answer, no tools → parses one JSON verdict
  `{pass, missing[], contradictions[]}`. It judges over the actual evidence **content**, not just
  `Cite` ids — that content is what makes the CONSISTENT / `contradictions[]` check real (ids alone
  can't reveal a conflict). The content-blind `Cite` channel stays the *deterministic* gate's input;
  the LLM judge legitimately needs content and only sees the sanitized render. Only runs when stage-1
  (`run_gate`) already passed — the judge audits *survivors*, per ADR-0008.
- **Verdict rides the same `GateResult` as stage 1** (`ok`, `missing[]`), so the loop treats both
  stages uniformly. `contradictions[]` fold into `missing[]` prefixed `contradiction:` — for the
  message + retry they are both just "reasons the answer can't go out yet"; no second channel.
- **Fail-open unless the verdict is an explicit `{"pass": false}`.** Junk / non-JSON / a missing
  `pass` key → treated as pass. The deterministic stage 1 is the hard guarantee; a flaky judge must
  not wedge an otherwise-good, in-window, cited answer. `ponytail:` upgrade path = stricter parse /
  re-ask if it rubber-stamps.
- **Bounded agentic retry** (ADR-0008). On any gate/judge fail the loop appends the answer + a user
  turn naming the `missing[]` ("gather any missing evidence, or resolve the noted conflicts, then
  answer again") and re-enters the step loop, up to `cfg.gate_max_retries` (2). **`tool_errors` is
  cleared at each retry** — a retry re-issues the failed call, so each round is judged on its OWN
  errors; without this a single round-1 guidance error would block every retry to the cap (a bug
  the two-axis review caught, guarded by `test_gate_retry_recovers_from_a_failed_tool_call`).
  Evidence accumulates across retries (the `cites` list is not reset), so a retry that fetches more
  genuinely grows the survivor set. Still failing after the cap → `cannot answer yet: <missing>`
  (the `missing[]` list IS the message). Each fail emits a `gate` event `{ok:false, missing,
  retry:<n>}`; `retry` counts retries used.
- **Runaway is doubly bounded** — `gate_max_retries` caps gate re-entries, `tool_call_cap` +
  `step_cap` (ADR-0005) still cap tool calls + loop turns *across* retries (ADR-0008 nuance).
- **Ordering vs ADR.** ADR sequences pre-gate → self-judge → (on pass) citation check.
  `run_gate` already bundles the citation check into stage 1 (shipped I4a); combining it with the
  judge and retrying on any failure is behaviourally identical (block until everything passes or
  the cap trips) with a shorter diff — the citation check still gates every answer.
- **Testing (scripted judge stub).** The judge is just another `llm.chat`, so a scripted `Reply`
  with JSON content IS the judge stub. `test_agent.py` adds: `self_judge` verdict parsing
  (pass/fail/contradictions/fail-open); a judge-fail → retry → fetch-more → judge-pass → answer
  path; and a cap test (judge always fails → 2 retries → `cannot answer yet`, `retry`=[0,1,2]).
  Existing pass-through tests gain a `{"pass":true}` judge reply; the pure stage-1 block tests pin
  `gate_max_retries=0` so they stay one-shot. Same fixture edits in `api/test_api.py` (the F4 HTTP
  seam drives the loop, so its scripts must carry the judge verdict too — the minimum forced by
  the loop's new contract).

## Copilot I5: diagnostic skills loader (progressive disclosure + manual invoke)

Steers the weak model with diagnostic **method** (how to investigate), distinct from a runbook's
cited **evidence** (ADR-0012). Loader = code (this ticket); skill **content** is seeded separately
(S3), so a bare skills dir is normal — no steering yet, not a bug.

- **Loader = a dict + markdown frontmatter, no plugin registry** (`copilot/skills/loader.py`).
  `load_skills(dir)` reads every `*.md` with a `---`-fenced `{name, description}` YAML block + a
  body → `{name: Skill(name, description, body)}`. A file missing name **or** description is
  **skipped** — a half-written skill must not steer the model. `yaml` is already installed
  (config.py), so no hand-rolled parser. `catalog(skills)` renders the name+description block for
  the base prompt; empty in → empty out.
- **Progressive disclosure at the `investigate()` seam.** New optional kwargs `skills` +
  `invoke`, both default nothing → the loop is **byte-identical** when no skills are wired (every
  existing test passes unchanged; `test_no_skills_leaves_prompt_and_tools_unchanged` pins it).
  When `skills` is passed: the `catalog` (name+description **only**) is appended to the system
  prompt, and `load_skill` is advertised as a tool.
- **Body loads on demand, two paths.** (1) *Agent auto-selects by description* → calls the
  `load_skill(name)` tool; the loop intercepts it before `dispatch` and returns the skill **body**
  as the observation, **no `Cite`** (`n:0`) — a body is method, not evidence, so it never feeds
  the quality gate. (2) *Human manual invoke* → `invoke=[names]` preloads those bodies into the
  system prompt up front. Both satisfy ADR-0012's "agent-selected **or** human-invoked".
- **Bad skill name = guidance, not a raise** (`_load_skill` → `error: unknown skill …`), matching
  the ADR-0015 tool-error convention the registry already uses — the model can retry.
- **HTTP wiring mirrors `get_kg`.** So the feature is reachable, not inert: `/chat` gains a
  `get_skills` dependency (memoized `load_skills(COPILOT_SKILLS_DIR)`, env like `COPILOT_KG_URI`;
  unset → None → a skills-free run) and `ChatRequest.skills` carries the human's manual-invoke
  names → `investigate(skills=…, invoke=req.skills)`. This is the same convergence glue an
  earlier Lane-Investigation feature (ADR-0007's `get_kg`) already put in `app.py`; the loader +
  loop stay in `copilot/{agent,skills}`. Tested at both seams: `investigate()` (stub LLM+adapter,
  spec §Testing) and `POST /chat` (`test_manual_skill_invoke_over_http`).
- **Testing.** `copilot/skills/test_skills.py` (assert self-check): frontmatter parse, half-written
  skip, empty dir, catalog lists descriptions not bodies. `test_agent.py` adds four loop tests:
  descriptions-in-prompt + `load_skill` advertised, no-skills-unchanged, manual-invoke-loads-body,
  agent-loads-body-via-tool (body as observation, `n:0`), and unknown-skill-is-guidance.

## Real fault injection from the UI + Loki fix + plugin live-wiring

### rsyslog `omfwd` fix — why Loki was empty

`frr-node/rsyslog.conf` had `module(load="omfwd")`. `omfwd` (network forwarding) is an rsyslog
**builtin**, not a loadable plugin `.so` — `module(load=...)` on a builtin fails and crashed
rsyslogd at container boot. Result: zero FRR syslog ever left any of the 70 routers; Loki was
empty; `/events` always returned 0 rows. Nobody caught it because the pipeline degrades silently
(no crash downstream, just no data). Fix: delete the `module(load="omfwd")` line — the existing
`action(type="omfwd" ...)` already invokes the builtin directly, no module load needed. Rebuilt
`frr-node:0.1`, hot-patched the 70 running nodes. Verified: `node_failure` injection produced real
bgpd/zebra syslog lines in `/events`.

### Real UI-driven fault injection (`dataapi/faults_api.py`)

Grafana app plugin needs to fire faults from a browser click, not a CLI. Two options: shell out
per-request (blocks the HTTP worker for the fault's full duration) or run it in a background
thread. Chose **daemon thread + in-memory registry** — reuses `faults/orchestrator.run_scenario`
as-is (no new fault-execution path to maintain), `POST /faults/inject` returns immediately with a
`scenario_id`, `GET /faults/active` / `POST /faults/revert/{id}` read/mutate the registry.
Registry is a plain `dict` behind a `Lock` — correct only if the process is single-worker, so
`dataapi/start.sh` pins `--workers 1` (`uvicorn ... --workers 1`); a second worker would split the
registry and `/faults/active` on worker B wouldn't see an injection started on worker A.
`run_scenario` gained an optional `cancel: threading.Event` for the early-revert path; the
existing guaranteed-revert `try/finally` is untouched. Target-role validation (422) is derived
from `orchestrator.CAMPAIGN_POOLS` — no hardcoded device lists to drift from the topology.

### CORS on dataapi

Browser-origin requests (the plugin running on `localhost:3000`) get blocked by the browser
without CORS headers, even though `dataapi` itself only binds `127.0.0.1`. Added
`CORSMiddleware` allowing only `http://localhost:3000` / `http://127.0.0.1:3000`, `GET`+`POST`.
This allow-list is the only auth boundary on `/faults/*` (which runs `docker exec`) — acceptable
because this is an offline lab tool bound to loopback, not an internet-facing service.

### Plugin live-integration + live/history time model

The Grafana app plugin (`grafana ui/`) was mock-only; now wired to the real `dataapi` for
topology, 11 metric groups (adds previously-ungraphed CPU/mem, errors/discards, queue, BGP churn,
RIB/OSPF, chassis, transceiver, tunnel jitter/rekeys), per-node packet/flow table, and a
terminal-styled live log view. Added a Lab ON/OFF badge and a Live/History time control (5s live
poll) — replaces the old static replay-tape model now that the API is a live source, not a fixture.
Fault Injection page calls the real `/faults/*` routes end-to-end (inject → impact → revert →
label), not a canned response. Detail owned by the plugin's own docs under `grafana ui/`.

### ML / Copilot — still stubbed

None of the above touches the ML or Copilot pipeline (`copilot/`, synthetic generator, dataset
schema). Fault injection, telemetry, and the plugin UI are infra/observability work only — no
new model, no new retrieval behavior. Do not read this section as ML progress.

## Copilot E1 (#42): the real end-to-end run — what real gpt-oss forced

The first run with **zero doubles** (real gpt-oss-20b on NVIDIA-hosted nim + live dataapi + real
nv-embedqa KB). Standing up the real backend forced three small env-gated seam fixes and surfaced
four findings (three filed, one fixed). Harness + record: `copilot/e2e/harness.py`, `REPORT.md`.

- **`reasoning_effort` (env `COPILOT_LLM_REASONING_EFFORT`)** — gpt-oss is a reasoning model; its
  answer lands in `content`, reasoning in `reasoning_content` (ignored). The client
  (`copilot/llm/http.py`) sends `reasoning_effort` when the env is set, plain OpenAI body when not.
- **Embedder `input_type`/`truncate` (env)** — NVIDIA-hosted `nv-embedqa-e5-v5` **rejects** the
  plain OpenAI `/embeddings` body (HTTP 500); it needs `input_type` + `truncate`. `NimEmbedder`
  adds them only when the env is set (a local/plain server still gets the plain body). nv-embedqa
  is *asymmetric* (query vs passage) but our Embedder is one `encode()`, so E1 uses a single
  input_type — a symmetric approximation, fine at N=13 docs. Proper split = **#44 (RESOLVED)**:
  `Embedder.encode(texts, kind="passage")`; `LanceRetriever.add` passes `passage`, `.search`
  passes `query`. `NimEmbedder` maps kind→input_type only under `COPILOT_EMBED_INPUT_TYPE=auto`;
  a fixed value (symmetric model) is sent as-is, unset → plain body. Hash/Local ignore `kind`.
- **Citation format in `SYSTEM_PROMPT`** — the I4a gate requires `[source:id]` tokens; the prompt
  only said "cite the evidence ids". A real model needs the literal shown ("e.g. [metrics:0] …
  list each id separately, NEVER a range"). This flipped answers from gate-blocked → cited.
- **Retrieval crash on an all-None-node corpus (FIXED)** — the S1/S2 seeder sets no `node`, so a
  real corpus is `node=None` on every doc; lancedb inferred a **Null-typed** `node` column and
  `search_incidents`' `node IN (...)` prefilter crashed. The fixture set `node=` so it never
  reproduced. `store.py` now pins a pyarrow schema (`node=string, ts=int64, vector=list<f32,dim>`).
  Filed + fixed as **#46**; the seeder populating `node` so incident-by-device narrowing returns
  rows is the open S1 follow-up.
- **Filed, not patched (#42 mandate):** **#43** gpt-oss compresses citations to ranges
  (`[metrics:3-5]`, unicode hyphen) the gate rejects as fabricated; **#45** a harmony
  `<|channel|>commentary` token leaks into a tool-call name (registry safely rejects it — no crash
   — but a step is wasted).

## Dataset closing-pass confirmations (2026-08-03)

### G11 — MPLS dataplane: unavailable on this host, decision confirmed

MPLS dataplane unavailable on this WSL2 host (no `mpls_router`/`mpls_iptunnel` module, but
`platform_labels` is non-zero — see below) → dataset paths use the WireGuard/OSPF abstraction;
`paths.parquet` `path_type` ∈ {`wg_tunnel`, `ospf_spf_path`}, no `ldp_lsp`.

Evidence:
- `lsmod | grep -i mpls` → empty output (no kernel module loaded).
- `uname -r` → `6.18.33.1-microsoft-standard-WSL2`.
- `modprobe mpls_router 2>&1` and `modprobe mpls_iptunnel 2>&1` → both silent/no-op (module not
  found on this kernel, no error surfaced either — WSL2 kernel is prebuilt, no loadable module).
- `docker exec clab-sdwan_mpls_noc-p2 sysctl net.mpls.platform_labels` → `net.mpls.platform_labels
  = 1048575` (LDP/vtysh sets this label range in-container regardless of kernel module presence).
- `docker exec clab-sdwan_mpls_noc-p2 ip -M route` → returns ~120 real MPLS label routes (swap/pop
  entries, protocol 193 = FRR/LDP), proving FRR's LDP control plane populates the label table. This
  is the **control-plane** MPLS label FIB (per-namespace, kernel-independent); it does not prove a
  working **forwarding** dataplane — `mpls_iptunnel`/`mpls_router` modules stay absent, so actual
  label-switched packet forwarding through this WSL2 kernel is unverified. Decision above stands:
  dataset paths do not claim `ldp_lsp`.

### G9 — controller /metrics scrape cadence: 30s, not 10s

Controller's own Prometheus `/metrics` job is scraped at **30s**, same as SNMP — NOT 10s.
`telemetry/telegraf/telegraf.conf:7-10` sets the only interval in the file, at `[agent]` (global
default), no per-plugin override:
```
[agent]
  interval = "30s"
  flush_interval = "30s"
```
The controller job, `telemetry/telegraf/telegraf.conf` `[[inputs.prometheus]]` block (`urls =
["http://172.20.20.56:9362/metrics"]`, `metric_version = 2`), carries no `interval =` key of its
own, so it inherits the 30s global. One-line fix (not applied): add `interval = "10s"` inside that
`[[inputs.prometheus]]` block.

### G5 — 7h de-bias capture: has not run

No ≥7h real capture exists; `profile.json` is still calibrated off the same short window
DATASETS.md already documents. `synthetic/profile.json` (`source_parquet`,
`source_rows`, `step_s`): `source_parquet = dataset_1785032386_1785033870_30s.parquet`,
`source_rows = 49844`, `step_s = 30` → span = `1785033870 - 1785032386` = 1484s = **24.7 min**.
The largest real-capture parquet on disk, `dataapi/datasets/dataset_1782052929_1782060129_30s.parquet`
(645KB), spans `1782060129 - 1782052929` = 7200s = **2h** — still short of 7h, and it is not the
file `profile.json` was calibrated from anyway. `ps aux | grep -iE 'orchestrator|export.py|campaign'`
→ no matches, nothing capturing now. **G5 needs action**: no 7h capture has been run or started.

### G10 realism-gap discriminator

HistGradientBoostingClassifier trained to tell real live-lab rows from synthetic (base topology
`topo_p6_pe12_r2_base`, stream F), 28 numeric features + one-hot `entity_type`/`site_type`, classes
balanced at 131,616 each, NaN left native (not imputed). `synthetic/discriminator.py`; permutation
importance on a 25% held-out split (HistGBT has no native importances).

**AUC = 0.9999 ± 0.0000** (5-fold). Tier: **>0.95 — will NOT transfer.** Synthetic is trivially
distinguishable; a model trained on it will not generalize to the real lab.

Top-5 (AUC drop when shuffled): `q_backlog_bytes` +0.247, `entity_type_interface` +0.047,
`if_in_octets` +0.004, `tunnel_jitter_ms` +0.003, `mem_pct` +0.002. `q_backlog_bytes` alone
explains the split: real emits it on only 3,094 rows (mean 5.4, mostly zero, max 924), synthetic on
97,344 rows (mean 214, min 9, never zero) — different NaN/coverage pattern **and** magnitude.

**Fix next (top features are the fix list):** correct `q_backlog_bytes` generation — match real's
sparse per-entity coverage (most rows NaN/zero) and its low idle magnitude, not a dense 200-byte
backlog; then re-check `entity_type` pillar-coverage alignment. The top-5 are the priority order.

### G9 fix — APPLIED

The one-line fix above is now in `telemetry/telegraf/telegraf.conf`: `interval = "10s"`
added inside the controller `[[inputs.prometheus]]` block. Takes effect on the next
telegraf reload (`docker restart` of the telegraf container); SNMP + ping cadences
unchanged.

### G10 follow-up — one genuine artifact fixed; residual gap is calibration-bound (needs G5)

`q_backlog_bytes` was a real synthetic artifact: the generator emitted a *standing*
per-row occupancy (mean ~214) while real `tc -s qdisc` reports a queue only when
non-empty (~0.2% of rows). Fixed in `synthetic/generate.py`: q_backlog is now NULL
when the queue is empty and fires sparse bursts under high load (~1.3% coverage,
mean ~183, matching real); the congestion fault fills it *additively* so the
precursor survives an empty baseline. `synthetic/check.py`'s louder-under-fault gate
updated to treat NULL as 0 occupancy.

After the fix, `q_backlog_bytes` drops out of the top-5 — but **AUC stays 0.9999**,
now dominated by `q_drops`, then `if_in_octets`, `bgp_msg_rx`, `rib_routes`. Direct
distribution comparison shows these are **real-capture deficiencies, not synthetic
defects**: real `bgp_msg_rx` is populated on 17% of device rows, `rib_routes` 51%,
`q_drops` 2% — sparse because the reference is a 24-min window with partial telemetry
(VPNv4 dataplane down) and `_src: "default"` calibration; `if_in_octets` differs ~60×
because real carries large lifetime counter offsets. Hand-tuning synthetic to match a
broken reference would *degrade* realism. **The correct fix is G5**: run the ≥7h clean
capture, recalibrate `profile.json`, then re-run G10. The discriminator is doing its
job — it is flagging that the calibration source, not the generator, is the bottleneck.

### Episode-rate re-measurement (supersedes research/14's 31.8/type/day)

Measured on a fresh single-topology Stream-F slice (0.25 sim-day, base inventory):
- **scale 1.0 → 12.2 episodes/type/sim-day** (confirms the post-fix ~11.4, ~2.8× below
  the old 31.8; the lead/ramp/concurrency-lock fixes genuinely lowered density).
- **scale 3.0 → 36.6 episodes/type/sim-day.**

Simulated-day target for 800/type (lever (a) density + lever (c) topology count):
- one topology @ scale 1.0: **~66 sim-days** (the old 25-day figure is wrong).
- **@ scale 3.0 across the 10-topology training pool: ~2.2 sim-days/topology** for
  ≥800/type aggregate — round to **3 sim-days/topology/stream** for margin.
- Full-scale estimate: 10 train + 2 held-out topologies × {F, N} × 3 sim-days at
  scale 3.0 ≈ 150–200M rows total. Do NOT reuse the 25-day figure.

### Schema FROZEN at 59 columns

With G1–G9 landed, the canonical schema (`dataapi/export.py:COLUMNS`, len 59) is
frozen. New row key: `(stream, topology_id, device, entity, ts)` — Streams F and N
share timestamps (independent realities of one topology). Companion tables
(`*_events`, `*_topology_edges`, `*_paths`) versioned alongside the main Parquet.
Nothing changes without an explicit regeneration decision (air-gap transfer makes
schema churn expensive).

### DEFECT 1 fix — hard-negative label purity (2026-08-03)

`is_hard_negative` rows must never carry a real impact label. Found 3,402 rows
(across **234 distinct real-fault episodes**) that were real faults which *also*
fell inside a hard-negative perturbation window, so they carried both `is_fault`
and `is_hard_negative`. Fix (`synthetic/generate.py`): the fault wins —
`is_hard_negative = hn_mask & ~has_fault`, so a near-miss flag clears wherever a
real episode labels the row. In this generator hard negatives never compute
severity/impact themselves (they set `t_impact=None`/`no_impact`/null), so the
leak was pure overlap, not a hard-neg ramp crossing SLA — `congestion_recedes`
caps its peak at 0.7× the SLA headroom and can never breach, so **0 literal
hard-neg→fault promotions**, 234 spurious flags cleared. Regression guard added to
`synthetic/check.py`: every `is_hard_negative` row must have null
`severity_primary`, all-null `time_to_impact_s`, and `impact_methods` empty/null/
`no_impact`. `verify_hardneg_paths.py` all-pass (0 leaked rows).

### DEFECT 2 fix — paths.parquet is now path-SELECTION history, not a catalog

`paths.parquet` was a static catalog (every row spanned the full capture,
`valid_to=capture_end`, no `path_id` repeats). `build_paths` now emits
interval-encoded per-`(ce, vrf)` path history (`synthetic/topology_paths.py`),
mirroring `topology_edges`. `path_id` is stable per `(topology_id, ce, vrf)`
(hub excluded), so a tunnel's whole failover history groups by `path_id`.
Failover windows are derived from the fault ledger (the synthetic generator does
not execute `controller.py`; the ledger is the ground truth of which tunnels
degraded, i.e. exactly what the controller would react to — non-hard-negative
episodes with `kind=tunnel_ramp` or a failover-capable `fault_type`, window
`[t_impact,t_end]`). Outside windows the path sits on its preferred hub; inside,
it rotates to an alternate. Closed intervals get a real `valid_to`; the final
still-active tail is NULL. Result on the `d0.2_n12` sample: **40.3% of rows
`valid_to=NULL`** (was 0%), **all 12 topologies show ≥1 reroute**, max path-id
group 13 rows. Example: `[ce_branch1,ce_hub1]` (preferred) → `[ce_branch1,ce_hub2]`
(failover window) → `[ce_branch1,ce_hub1]` `valid_to=NULL` (recovered).

### Full-scale generation run (2026-08-04)

Ran via the memory-safe `generate_full` driver (streamed one (topology, stream)
block at a time; a process-pool variant fanned the 24 blocks across 8 workers,
biggest-first, to use the free cores). Output = a per-seed tranche directory of
Parquet part files (a dataset dir, not one file) + `events`/`topology_edges`/
`paths` companions + `manifest.json` (schema version, generator commit, per-part
SHA-256, row counts). Parameters: `scale=6.0`; train seed 42 `d=1.2`, holdout
seed 43 `d=0.6`; 12 topologies x {F, N}; `hard_neg_per_topo` 300/200.

**TRAIN tranche (seed 42): 102,926,592 rows — ALL ACCEPTANCE CHECKS PASSED**
(`verify_full_memsafe.py`, the batched equivalent of the spec's
`verify_full_generation.py`, which OOMs on 100M rows in pandas):
- all 21 fault types >= 800 primary instances; 21 types present
- 12 topologies; held_out KV = `topo_p8_pe24_r3_full,topo_p8_pe16_r2_cv`
- Stream F fault rate 12.56%, Stream N 0.000% (clean split; sampler composes the
  8-11%/0.5-1%/0.3-1% train/cal/eval prevalences from the F/N mix)
- hard negatives 89,646 (>= 4,000); concurrent-pair episodes 5,777; cascade
  episodes 6,418; lead_time_s CV 0.837; error counters all zero; vrf list-typed
- companions scale with volume: events 62,170 / edges 325,691 / paths 31,586

**HOLDOUT tranche (seed 43): 51,463,296 rows** — d0.6, ~half volume, a disjoint
within-topology episode holdout (not sized to hit 800/type on its own; the
cross-topology LOTO holdout is the 2 held_out topologies present in BOTH tranches).
Companions: events 31,164 / edges 177,211 / paths 16,338.

Measured episode yield: **~138 episodes/type per (topology × scale-day) unit**,
linear (no concurrency-lock saturation at scale 6). At scale 6 that is ~800/type
per ~0.97 simulated days across the 12 topologies — the d1.2 train run clears it
with margin. (Supersedes the old 25-sim-day-per-stream estimate.)

Tranches live under `synthetic/output/full/full_seed{42,43}/` (gitignored;
regenerate with `generate.py --full`). The manifest per tranche is the air-gap
handoff artifact — pin the training repo to those SHA-256s. **Schema stays frozen
at 59 columns; this is the last point it is open to change.**

### Live-lab realism set — tagging (never trained on)

The real captures under `dataapi/datasets/*.parquet` are the live-lab realism set.
They are distinguished from synthetic tranches structurally: synthetic files carry
`synthetic=true` in file KV metadata and live under `synthetic/output/`; real
captures carry no such KV and live under `dataapi/datasets/`. Any training-corpus
assembly MUST exclude `dataapi/datasets/` and anything without `synthetic=true`.

### G5 real capture — STARTED (recalibration is the follow-up)

The >=7h de-bias capture is running: `faults/orchestrator.py --campaign
--duration 27000 --mean-gap 120 --seed 7` (campaign `campaign-de8c5b164f6a`),
started 2026-08-03T19:12:41Z on the live 148-container lab (control plane verified
wired: p2 had 6 OSPF Full neighbours). Unattended, survives session end. Follow-up
once >=7h elapses: `export.py --start 1785784361 --end <start+27000>` over the
window, then `calibrate.py` to rebuild `profile.json` from a full diurnal cycle,
then re-run G10. Until then the G10 realism gap (AUC 0.9999) stands — this
full-scale corpus is calibration-limited and is for pipeline/structure work, not
real-lab transfer.

### Overlay signature — live faults emit the dataset signature — CODE COMPLETE, DEPLOY PENDING (GH #59)

**#60 landed** (R59-T1): the shared `faults/signatures.py` module exists and owns
`default_signatures` / `prog` / `tunnel_ramp_targets`; `calibrate.py` + `generate.py`
now import it instead of inline closures. Refactor is byte-identical — regenerating the
seed-42 sample reproduces the baseline parquet + `profile.json` sha256, golden test
`faults/test_signatures.py` pins the funcs to the pre-refactor closures.

**#61 landed** (R59-T2): controller `_overlay` registry + `/fault/overlay`(`/clear`)
endpoint, cloned from `_drift`; overlay is authoritative (netem readback suppressed
while active) and ramps `sdwan_tunnel_*` toward the calibrated peak. Trust-boundary
validation: unknown site/fault, non-`tunnel_ramp` kind, negative lead, sub-`2*STEP`
duration, or unknown severity → 400.

**#62 landed** (R59-T3): `run_scenario` is now a **buildup→impact→hold→revert** state
machine. It draws the precursor lead via `leadpriors.draw_lead_s` (floored to
[30,60]s), posts the overlay via a new `_OverlayInjector` (HTTP to the controller,
modeled on `_DriftInjector`) for `overlay`-flagged scenarios, waits the lead
(cancellable), fires the real injector `apply()` at impact, holds `duration`
(cancellable), then a guaranteed `finally` reverts the physical action AND clears the
overlay. Early-revert (`/faults/revert/{id}`) during buildup skips the physical fire
but still clears the overlay; the label row is always written with
`t_impact = t_start + lead`. Spoke-CE `tunnel_ramp` scenarios carry the `overlay` flag
(`congestion`, `tunnel_degrade`, `asymmetric_loss`, `brownout` at #62; `hub_spoke_congest`
and `controller_drift` added at #64); iface_down / control-plane / backbone faults post no
tunnel overlay. The visible ramp now lives in the controller overlay, not a netem ramp —
so `run_scenario` no longer calls `injector.ramp()` (the campaign path still does).

**#63 code-complete** (R59-T4). Env sidecar (`telemetry/env-metrics.py`) reads the
overlay registry once per `main()` (best-effort `GET /fault/overlay`, `{}` on failure)
and drives `signatures.prog` into the modelled sensors: `envmodel.fault_heat_c(ft) *
prog` → `temp_c` (temp/power/fan move under fault); new `envmodel.optic_degrade(ft) *
prog` → `optical` (`gray_failure` 1.0, `brownout` 0.5 — rx-power sags, bias climbs).
GET projection now carries `sevmul` (added to `/fault/overlay`), so a low/medium fault
ramps optics/thermal at the same severity as its tunnels; `dur = t_end - t_impact`,
`p_cross = 1.0` (controller hardcodes it). Registry is tunnel-site-keyed and site ==
device, so only spoke-CE `tunnel_ramp` faults move the env pillar — iface_down /
control-plane / backbone faults post no overlay, so core-device temp/optics stay flat
(inherited #62 scope). `signatures` import is best-effort (`None` → inert) so a stale
image never darkens the pillar.

`flow_bytes/flow_packets`: `export.build_dataset` gap-fills per (device,bucket) any
device row nfacctd did not cover (`_modelled_flow`, `trafficgen.VRF_FLOW` × per-VRF
`diurnal.util(hour,vrf) * week_scale`, ~6-min tick, deterministic; period from
`trafficgen.PERIOD_SECONDS`). This is the OFFLINE dataset path, not live telemetry.
Three by-design nulls: `if_*_errors`/`if_*_discards` (lab structural zero) and flow on
VRF-less P-router device rows (matches `generate._flow_row`). Seam
`dataapi/test_coverage_seam.py` fixtures a CE + a P router and proves the full 59-col
schema with every other env/optical/flow feature non-null. Also fixed an all-null `vrf`
column crashing `_fill_vrf` on pandas 3.

**Deploy prereq (why not DONE):** `signatures.py` is now imported by BOTH `controller.py`
and the sidecar (same `noc-controller:0.1` image). `controller/Dockerfile` gains `numpy`
+ `COPY faults/signatures.py /app/faults/`. Image must be rebuilt + re-air-gapped before
the overlay fault terms (and the controller's own `signatures` import) load on the lab.
Un-rebuilt → guard keeps telemetry alive with fault terms inert. Not yet rebuilt/verified
on a running lab.

**#64 landed** (R59-T5): the 6 formerly live-inert faults now BOTH physically fire AND
emit their calibrated overlay. **Verification status:** the parser, scenario wiring, lock
keys and label rows are unit-tested (`faults/injectors.py` `__main__`, `_lock_selftest`,
`dataapi/test_faults_api.py`); the qos template side is confirmed (`qos.sh.j2` `default
{{classid}}`). The live-VM demo (netem actually installing under `1:20`, the controller
folding brownout's delay/loss, drift+overlay coexisting on one spoke, threshold 8.0
crossing) is NOT re-run yet — no lab in this environment. Run one injection per fault and
cite it before calling the live demo done.
- **HTB default-class parent read from the live qdisc.** `NetemImpair` was hardcoded to
  splice under `1:30`, but each CE uplink's HTB default class is its VRF's classid
  (`generator/generate.py classid_for`: VOICE `0x10` / CORP `0x20` / GUEST `0x30`), so on
  a CORP uplink (`1:20`) the splice hit a non-existent class and NO netem installed —
  `congestion`/`tunnel_degrade`/`asymmetric_loss` emitted nothing. `_parse_htb` now reads
  `htb ... default 0x<n>` off `tc qdisc show` and captures the baseline leaf to restore.
- **`brownout`** gains a small delay+loss beside its rate cap (a `rate` token alone is
  invisible: `_read_netem` parses only delay/loss).
- **`hub_spoke_congest`** now targets a SPOKE directly (pool `_CE_BRANCHES + _CE_DCS`)
  and injects heavy netem on its uplink — the only place the controller can observe it
  (netem/overlay fold per spoke site, `_sites`; a hub-side cap is invisible). A spoke
  peers every hub, so this degrades all its tunnels; the calibrated peak is higher than
  plain `congestion`.
- **`controller_drift`** keeps its `_drift` failover suppression AND carries the overlay;
  both key on a spoke site, so its campaign pool moved off hubs to spokes.
- Because hub_spoke_congest now targets the spoke directly, `_lock_key` (which keys on
  `target`) still excludes a `congestion` on that same spoke uplink — no guard change needed.


Decision (spec #59, `ready-for-agent`): live injection will emit the synthetic
generator's **calibrated** per-fault signature, plus a 30–60s precursor buildup,
**on top of** the real physical action — so live telemetry is in-distribution with
the training set. Mechanism: a shared pure module `faults/signatures.py` (the
`calibrate.py` signature table + `prog`/`tunnel_ramp` math) imported by BOTH the
generator and the live controller; a controller `_overlay` registry + `/fault/overlay`
endpoint (cloned from the existing `_drift` pattern) that ramps `sdwan_tunnel_*` to
the calibrated peak (overlay authoritative — netem readback suppressed while active,
so no double-count); a buildup→impact→hold→revert state machine in
`run_scenario`; the env sidecar's hardcoded `0.0` fault args flipped on (temp/optical)
and `flow_bytes`/`flow_packets` modelled from `trafficgen.VRF_FLOW`×diurnal.

Constraint: the frozen dataset (`synthetic/output/*.parquet`, `faults/labels/labels.jsonl`)
must not change — the only generator-side edit is a byte-identical refactor to import
the shared module. Accepted divergence: the live lead is floored to 30–60s for demo
visibility, out-of-distribution for the naturally-fast faults (bgp/ldp/node ≈1–5s).
Live→feature builder is unchanged: `dataapi/export.build_dataset()` already maps every
VM/Loki series to the 59-col `export.COLUMNS` the model trains on. The 6 formerly
live-inert faults were fixed in #64 (see the #64 block above).
