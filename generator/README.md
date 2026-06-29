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

Current scale: **130 containers** — 8 P + 10 PE + 34 CE + 78 host containers
(formula: `p_count + pe_count + (branch+hub+dc) + hosts`; see comment in
`topology-spec.yaml`).

### Key boolean/structural knobs

| knob | type | effect |
|------|------|--------|
| `pe_dual_homing` | bool | each branch CE attaches to two PEs (dual uplinks in `clab.yml`); PE-CE BGP peers on both |
| `bfd_core` | bool | enables BFD on all P-PE and PE-PE OSPF adjacencies; accelerates reconvergence to ~1 s |
| `hub_hub_wg` | bool | emits a WireGuard full-mesh among hub CEs in addition to hub-spoke; cross-injects keys |
| `route_reflector` | bool | disables full-mesh iBGP; PEs in `rr_nodes` become RR servers, rest become clients |
| `rr_nodes` | list | names of PE nodes acting as route reflectors (e.g. `[pe1, pe2]`); ignored when `route_reflector: false` |

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
