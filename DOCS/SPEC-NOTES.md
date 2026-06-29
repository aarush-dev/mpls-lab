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
| P  | 3 | ospfd, ldpd | No BGP, no VRFs. Core LSR. |
| PE | 3 | ospfd, ldpd, bgpd | MP-BGP VPNv4, 3 VRFs per PE. |
| CE | 8 (4 branch + 2 hub + 2 dc) | bgpd (per-VRF instance) | eBGP to PE; one `router bgp <asn> vrf vrf_<VRF>` per VRF. Kernel vrf devices bound via clab exec. |
| host | 20 (1 per site-VRF) | none (multitool image) | Traffic source/sink. branch=2, hub/dc=3 each. |

Total: ~34 containers. At 50-150 MB each ≈ 1.7-5 GB RAM — comfortable on 108 GB.

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

Enumerate all unordered pairs of P routers; assign sequential /31s from 10.0.0.0:

```
pair k (0-indexed): network = 10.0.0.{2k}/31
  lower-index router: 10.0.0.{2k}   (.0 of /31)
  higher-index router: 10.0.0.{2k+1} (.1 of /31)
```

For 3 P routers: pairs (p1,p2), (p1,p3), (p2,p3) → /31s at 10.0.0.0, 10.0.0.2, 10.0.0.4.

### P-PE links (/31)

Assign each PE to its "primary" P via round-robin: `p_idx = (pe_idx - 1) % p_count + 1`.
Enumerate PE attachments sequentially; assign /31s from 10.0.1.0:

```
PE{i} attachment k (0-indexed from i=1): network = 10.0.1.{2*(i-1)}/31
  PE side: 10.0.1.{2*(i-1)}
  P  side: 10.0.1.{2*(i-1)+1}
```

For extra redundancy (optional): each PE could also connect to a secondary P (next in round-robin). The spec supports this but it is not required for Phase 1 — keep single uplink per PE for simplicity.

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

```
frr defaults traditional
hostname p{i}
no ipv6 forwarding
!
interface lo
 ip address 10.255.1.{i}/32
 ip ospf area 0
!
interface eth{k}   # one per connected link
 ip address {link_addr}/31
 ip ospf area 0
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

At `pe_count=3`: full-mesh = 3 iBGP sessions. This is the right call:
- No single point of failure
- No RR config complexity
- Only revisit at pe_count ≥ 6 (when full-mesh sessions = 15)

If `pe_count > 5` in future: set `route_reflector: true`, `rr_node: "pe1"` in spec, and generator makes pe1 the RR (adds `neighbor X route-reflector-client` to pe1's config; other PEs only peer to pe1).

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

This distributes CEs evenly. With 8 CEs and 3 PEs: PE1 gets sites 0,3,6; PE2 gets 1,4,7; PE3 gets 2,5.

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
| `pe_dual_homing` | bool | Each PE connects to two P routers (primary + secondary) for redundancy |
| `bfd_core` | bool | Enables BFD on all P-PE and P-P core links (FRR `bfd` stanza per interface) |
| `hub_hub_wg` | bool | Adds a direct WireGuard peering between hub CEs (hub1↔hub2) for spoke-to-spoke fast-path |
| `route_reflector` | bool | Enables RR mode: two PEs act as route-reflectors; remaining PEs are clients |
| `rr_nodes` | list[str] | Which PE nodes serve as RRs when `route_reflector: true` (e.g. `["pe1","pe2"]`) |

`route_reflector` is auto-enabled by generate.py when `pe_count > 5`; set it explicitly to force RR mode at any scale.

### MPLS telemetry sidecar

`noc-ldp-metrics` (container `172.20.20.58`) is a lightweight exporter that polls each PE's LDP state via vtysh and pushes Prometheus-format metrics to VictoriaMetrics at:

```
POST http://172.20.20.50:8428/api/v1/import/prometheus
```

Metrics emitted: `mpls_ldp_session_state{device,neighbor}`, `mpls_ldp_session_uptime_seconds`, `mpls_label_table_entries{device}`.

### Route-reflector topology

When `route_reflector: true` and `rr_nodes: ["pe1","pe2"]`:

- **pe1 and pe2** become RR servers with `cluster-id` = their own loopback address (10.255.2.1 and 10.255.2.2 respectively). They peer to each other as standard iBGP (no RR relationship between servers).
- **pe3–pe10** (all non-RR PEs) are configured as RR clients: each peers only to pe1 and pe2 (`neighbor 10.255.2.1 route-reflector-client` on the RR side). Clients have no direct iBGP sessions between themselves.
- Full-mesh iBGP is only used when `pe_count ≤ 5` and `route_reflector: false`.

## Validation checklist for generate.py output

After generation, verify manually or via script:
1. No duplicate IP addresses anywhere in generated configs.
2. Every PE has exactly `pe_count - 1` iBGP neighbors (full-mesh).
3. Every CE-VRF combination has exactly one /30 link to its PE VRF interface,
   one dedicated /24 LAN, and one host container (Option A). No two (site,VRF)
   LANs share a /24. Total hosts = sum over sites of #VRFs served (=20 default).
4. LDP is only on P and PE nodes, only on core-facing interfaces (never CE-facing).
5. All loopbacks participate in OSPF; all CE-facing interfaces on PE are VRF-bound and NOT in OSPF.
6. WireGuard: each spoke has exactly 2 `[Peer]` entries (one per hub); each hub has `(branch_count + dc_count)` `[Peer]` entries.
