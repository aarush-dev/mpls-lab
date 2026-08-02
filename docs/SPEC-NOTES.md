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
