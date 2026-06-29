# Topology generator

Renders the air-gapped SD-WAN-over-MPLS NOC lab from a single spec.

## Run

```bash
python3 generate.py          # render everything to ../topology/ (idempotent; runs --check after)
python3 generate.py --check  # render + self-test only (asserts no IP collisions, files present)
```

## Deps

- Python 3
- `jinja2`, `PyYAML` (Debian: `apt-get install python3-jinja2 python3-yaml`)
- No `wg` / crypto libs needed — WireGuard keys are computed by a pure-stdlib
  X25519 (RFC 7748) in `generate.py`, so it works in the air-gap with no pip.

## Input

`../topology-spec.yaml` is the single source of truth. Change the `knobs:` counts
to rescale; every address is derived from indices per `../DOCS/SPEC-NOTES.md`
(nothing hardcoded per node).

Current scale: **148 lab containers** — 24 P + 12 PE + 34 CE + 78 host containers
(formula: `p_count + pe_count + (branch+hub+dc) + hosts`; see comment in
`topology-spec.yaml`). 9 additional telemetry/infra containers bring the full
deployment to ~157 containers total.

### Key boolean/structural knobs

| knob | type | effect |
|------|------|--------|
| `pe_dual_homing` | bool | each branch CE attaches to two PEs (dual uplinks in `clab.yml`); PE-CE BGP peers on both |
| `bfd_core` | bool | enables BFD on all P-PE and PE-PE OSPF adjacencies; accelerates reconvergence to ~1 s |
| `hub_hub_wg` | bool | emits a WireGuard full-mesh among hub CEs in addition to hub-spoke; cross-injects keys |
| `route_reflector` | bool | disables full-mesh iBGP; PEs in `rr_nodes` become RR servers, rest become clients |
| `rr_nodes` | list | names of PE nodes acting as route reflectors (e.g. `[pe1, pe2]`); ignored when `route_reflector: false` |

### Provider core / multi-area knobs

| knob | type | effect |
|------|------|--------|
| `p_count` | int | total P routers (24 = 6 POPs × 4) |
| `pe_count` | int | total PE routers (12 = 2 per POP) |
| `pop_count` | int | number of geographic POPs (6) |
| `p_per_pop` | int | P routers per POP (4); intra-POP mesh = C(4,2)=6 links, OSPF cost 10 |
| `multi_area` | bool | enables multi-area OSPF; each POP = area 1..6, inter-POP backbone = area 0 |
| `igp_cost_intra` | int | OSPF cost on intra-POP P-P links (10) |
| `igp_cost_inter` | int | OSPF cost on inter-POP backbone links (100) |
| `inter_pop_redundancy` | int | redundant parallel links per inter-POP adjacency (2); links sharing one SRLG conduit |
| `inter_pop_chords` | list | chord pairs added to the inter-POP ring beyond the base ring, e.g. `[[1,4],[2,5],[3,6]]` |

### Provider core wiring

The P-core is a POP-structured fabric, not a full mesh:

- **Intra-POP**: each POP contains 4 P routers fully meshed (6 links/POP × 6 POPs = 36 intra-POP links). These links run in the POP's dedicated OSPF area (area 1..6) at cost 10.
- **Inter-POP backbone**: a base ring connecting POP1→2→3→4→5→6→1 plus 3 chords `[1,4],[2,5],[3,6]` = 9 inter-POP adjacencies. Each adjacency is 2 redundant links (18 inter-POP links total) running in area 0 at cost 100, with both links sharing one SRLG conduit (the cost model that enables TE path control via OSPF cost).
- **ABRs**: the first 2 P routers per POP (p1,p2 in POP1; p5,p6 in POP2; etc.) are area border routers, present in area 0 and their POP area simultaneously.
- **PE-facing P**: the last 2 P routers per POP (e.g. p3,p4 in POP1) are pure intra-POP routers; they carry PE dual-homing links and do not participate in area 0.
- **PEs**: each POP has 2 PEs (12 total), dual-homed to the 2 PE-facing P routers in their POP. pe1+pe2 are route reflectors; pe3..pe12 are RR clients.
- **Core links**: 36 intra-POP + 18 inter-POP + 24 P-PE = **78 core links** total.
- `frr.conf.j2` emits `ip ospf area {{loopback_area}}` on loopbacks and `ip ospf area {{link.area}}` + `ip ospf cost {{link.ospf_cost}}` on each core interface, driven by per-link metadata from `generate.py`.

### topology-meta.json

`generate.py` emits `../topology/topology-meta.json` alongside `clab.yml`. This file is consumed by the fault orchestrator (`faults/orchestrator.py`) and `dataapi/` to resolve real node/link/SRLG identifiers at runtime — no hardcoded node names in fault logic.

| key | type | meaning |
|-----|------|---------|
| `pop_count` | int | number of POPs (6) |
| `p_per_pop` | int | P routers per POP (4) |
| `multi_area` | bool | whether multi-area OSPF is active |
| `pops` | object | per-POP node lists: `{"pop1": ["p1","p2","p3","p4"], ...}` |
| `abrs` | list | ABR node names: the first 2 P per POP |
| `pe_pop` | object | PE→POP mapping: `{"pe1": "pop1", "pe2": "pop1", ...}` |
| `p_core_ifaces` | object | per-P-node list of all core-facing interface names (used by `p_node_failure`) |
| `srlgs` | object | SRLG conduit name → list of link descriptors sharing that conduit (used by `srlg_cut`) |
| `inter_pop_links` | list | all inter-POP link descriptors (node pairs, area, cost, srlg) |
| `pop_inter_links` | object | per-POP list of links crossing into/out of that POP (used by `pop_isolation`) |

## Output (`../topology/`)

```
clab.yml                       # containerlab topology (image: frr-node:latest, pull-policy Never)
configs/<node>/frr.conf        # per-role: P=OSPF+LDP, PE=+MP-BGP VPNv4+per-VRF, CE=eBGP
configs/<node>/daemons         # enabled FRR daemons per role
configs/<node>/snmpd.conf      # IF-MIB, community 'public', AgentX master
configs/<node>/90-mpls.conf    # P/PE only: MPLS sysctls + per-core-iface input=1
configs/<ce>/qos.sh            # CE only: tc HTB DSCP classes (VOICE/CORP/GUEST)
configs/<ce>/wg0.conf          # CE only: WireGuard hub-spoke, keys cross-injected
```

VRF attach (`ip link add <vrf> type vrf table N` + `ip link set <iface> vrf <vrf>`),
MPLS sysctl reload, qos apply, and `wg-quick up` are emitted as clab `exec:` blocks
in `clab.yml`, so the deployed lab is self-contained (no post-deploy script).

## Templates

`templates/*.j2` — `clab.yml.j2`, `frr.conf.j2` (role conditionals),
`daemons.j2`, `snmpd.conf.j2`, `90-mpls.conf.j2`, `qos.sh.j2`, `wg0.conf.j2`.

## Per-site WAN baseline netem

`generate.py` owns the `site_netem(site_type, idx)` helper — the single source of truth for per-site geography impairment. It emits a `tc qdisc replace dev eth0 root netem delay <d>ms <j>ms loss <l>%` command in each CE's clab `exec:` block, applying a baseline netem to `eth0` (the transport veth). Tier defaults: branch ~41 ms / ~5 ms jitter / ~0.3% loss; hub ~17 ms; dc ~12 ms. Bounds: delay ≤ 60 ms, jitter ≤ 0.3 × delay, loss ≤ 1%. This delays both WireGuard tunnels and NOC telemetry (SNMP/IPFIX/syslog) on the same veth — matching real WAN behavior. The controller measures the resulting latency (ping over wg0) but does not define it.

## Note

`generate.py` only emits config. It does NOT deploy. Deploy with
`containerlab deploy -t ../topology/clab.yml` once the `frr-node:latest` image is built.

Output directories are created with `os.makedirs(..., exist_ok=True)` — **no
`shutil.rmtree`**. This inode-safe overwrite preserves bind-mount inodes inside
running containers, so a re-generate updates config files in-place without
requiring a container restart.
