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

## Note

`generate.py` only emits config. It does NOT deploy. Deploy with
`containerlab deploy -t ../topology/clab.yml` once the `frr-node:latest` image is built.
