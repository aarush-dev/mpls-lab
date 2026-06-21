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

- **`sim`** (default): Python socket flow simulator. Opens flows at a rate set by
  the curve against a local loopback sink; carries the **true offered_bps** in the
  emitted JSON plan while moving only ~1/1000 of the bytes (light). Same curve
  shape, no iperf3, air-gap-trivial.
- **`iperf3`**: derives the real `iperf3` client commands (pairing, bitrate, `-u`
  for VOICE, DSCP→`--tos`) from the same plan. Currently **prints** them (dry) —
  cross-container choreography + an iperf3 binary on hosts is Phase 2 wiring.

## Run

```bash
python3 trafficgen.py --plan            # one diurnal plan as JSON lines, then exit
python3 trafficgen.py --backend sim     # run the simulator (Ctrl-C to stop)
python3 trafficgen.py --backend sim --ticks 10
python3 trafficgen.py --backend iperf3  # print the iperf3 commands it would run
python3 trafficgen.py --selftest
```

## Environment

- `DIURNAL_PERIOD` (s): 24h cycle compression (default `3600`).
- `TOPO_SPEC`: spec path (default `../topology-spec.yaml`).

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

- `sim` backend: stdlib only.
- `iperf3` backend: needs `iperf3` on the host containers (the `wbitt/network-multitool`
  image lacks it; `frr-node` has tools). Add an iperf3-capable image or install the
  binary — **container wiring is Phase 2**, not solved here.

## Shortcuts (`# ponytail:` in code)

- Default backend is `sim`: real iperf3 orchestration across 22 containers is heavier
  than the signal needs now. Upgrade: `--backend iperf3` once hosts carry the binary;
  pairing is already derived.
