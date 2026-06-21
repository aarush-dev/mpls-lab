# Traffic generator

Phase 1.4 component. Drives a **diurnal** load across the lab's CE/host pairs so
the lab's utilization/latency curves look real (the shape the ML later learns from).
Stdlib-only (plus `PyYAML` to read the spec via `topo.py`; falls back to a built-in
default so `--selftest` runs anywhere).

## Diurnal model (`diurnal.py` — shared with the controller)

A 24h cycle compressed to `DIURNAL_PERIOD` seconds (default 3600 = 1 real hour/day).
Base utilization curve in `[0,1]`: night trough (~0.10), morning ramp from ~07h,
business plateau with twin peaks (~10h, ~14h) and a **lunch dip** (~12h), evening
decay. Built from closed-form bumps — no numpy, repeatable, non-degenerate.

Per-VRF profiles modulate the base curve:

| VRF | Profile | Flow shape |
|---|---|---|
| VOICE | steady, modest swing | many tiny UDP-like flows (EF) |
| CORP  | bursty, big diurnal swing | fewer, large, spiky TCP (AF31) |
| GUEST | best-effort, evening-leaning | occasional bulk TCP (BE) |

`python3 diurnal.py --plot` prints an ASCII day curve.

## Backends

- **`nc`** (default in compose): Drives real cross-site TCP flows via BusyBox `nc`
  using `docker exec` over the mounted `/var/run/docker.sock`. For each plan row a
  one-shot listener is started on the hub/DC host; the spoke host sends bytes shaped
  to the diurnal curve. Moves real wire bytes so `ifHCInOctets`/`ifHCOutOctets` climb
  on CE nodes and `nfacctd` exports flows. 5% of plan bytes sent per tick (tunable via
  `NC_FLOW_SCALE`); 30s interval. Chosen over iperf3 because the lab host image
  (`wbitt/network-multitool:alpine-minimal`) ships BusyBox nc but NOT iperf3.
- **`sim`**: Python socket flow simulator. Opens flows at a rate set by the curve
  against a local loopback sink — no real dataplane bytes, no docker.sock needed.
  Useful for unit testing / offline runs.
- **`iperf3`**: prints the iperf3 commands it would run (dry). Upgrade path once
  hosts carry an iperf3 binary.

## Compose wiring (Phase 2.2)

Build from repo root: `docker build -t noc-trafficgen -f trafficgen/Dockerfile .`

Service `trafficgen` in `telemetry/docker-compose.yml` runs at static IP `172.20.20.57`
on the `clab` network with `/var/run/docker.sock:ro`. Environment variables:

| Var | Default | Meaning |
|---|---|---|
| `TRAFFICGEN_BACKEND` | `nc` | Backend to use |
| `CLAB_LAB` | `sdwan_mpls_noc` | Containerlab lab name |
| `NC_PORT_BASE` | `19000` | First nc listener port (each flow row uses base+idx) |
| `NC_FLOW_SCALE` | `0.05` | Fraction of plan bytes actually sent per tick |
| `DIURNAL_PERIOD` | `3600` | 24h cycle compression in seconds |

## Run

```bash
python3 trafficgen.py --plan               # one diurnal plan as JSON lines, then exit
python3 trafficgen.py --backend nc         # drive real nc flows (needs docker.sock)
python3 trafficgen.py --backend sim        # run the loopback simulator (Ctrl-C to stop)
python3 trafficgen.py --backend sim --ticks 10
python3 trafficgen.py --backend iperf3     # print the iperf3 commands it would run
python3 trafficgen.py --selftest
```

## Environment

- `DIURNAL_PERIOD` (s): 24h cycle compression (default `3600`).
- `TOPO_SPEC`: spec path (default `../topology-spec.yaml`).
- `TRAFFICGEN_BACKEND`: backend override (default `nc`).

## Output schema (per-flow-row JSON, `--plan` and sim ticks reference it)

| Field | Meaning |
|---|---|
| `site`, `site_type` | CE node + role (branch/hub/dc) |
| `vrf` | CORP / VOICE / GUEST |
| `hod`, `util` | hour-of-day (0–24), utilization (0–1) |
| `flows` | concurrent flows this tick |
| `proto` | `tcp` / `udp` |
| `dscp` | EF / AF31 / BE (matches `qos.sh`) |
| `bytes_per_flow`, `offered_bps` | nominal payload, offered load |
| `src` | source host container (`h_<suffix>`) |

## Fault hook

`build_plan(now, model, fault_scale={(site,vrf): mult})` scales any site/vrf's
offered load, so the later fault phase can visibly perturb the curve (selftest
covers this).

## Dependency note

- `nc` backend: requires docker CLI (`docker exec`) and `/var/run/docker.sock` mounted.
  Lab host image `wbitt/network-multitool:alpine-minimal` has BusyBox nc — confirmed present.
- `sim` backend: stdlib only, no docker.sock needed.
- `iperf3` backend: needs `iperf3` on the host containers — NOT present in current lab hosts.
  Upgrade: add iperf3 to the frr-node image and set `TRAFFICGEN_BACKEND=iperf3`.

## Shortcuts (`# ponytail:` in code)

- nc backend chosen over iperf3: hosts lack iperf3, BusyBox nc is lighter and sufficient
  to move counters. DSCP marking not applied by nc (BusyBox lacks `--tos`); QoS marking
  would need to happen in the CE node. Upgrade path documented above.
- NC_FLOW_SCALE=0.05 keeps traffic light; bump to 0.5+ if SNMP counter increments are
  too small to see in Grafana.
