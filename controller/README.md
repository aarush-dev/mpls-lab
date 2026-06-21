# SD-WAN controller (simulated)

Phase 1.4 component. Holds overlay policy, does path selection over the WireGuard
hub-spoke overlay, derives per-tunnel metrics, and exposes them as **Prometheus
text exposition over HTTP** for Telegraf (Phase 2) to scrape. Stdlib-only (plus
`PyYAML` to read the spec; falls back to a built-in default spec if unavailable,
so `--selftest` runs anywhere).

## What it does

- **Model** (`topo.py`): derives hubs/spokes/tunnels/VRFs from `../topology-spec.yaml`
  using the same index arithmetic as the generator. 6 spokes x 2 hubs = **12 tunnels**.
- **Metrics**: each tunnel's latency/jitter/loss is **modelled** from a baseline RTT
  (hub1 = primary/closer ~12ms, hub2 = secondary ~22ms) + diurnal congestion
  (shared `../trafficgen/diurnal.py`) + any **live netem** delay/loss read back from
  the deployed spoke container via `tc` — so injected faults perturb the signal.
  Exponential smoothing makes the series look like real time-series, not noise.
- **Rekey events**: WireGuard rekeys ~every 2 min; under loss they cluster (handshake
  retries) — a flap precursor. Emitted as JSON events + a cumulative counter metric.
- **Path selection**: per `(site, vrf)`, score = `loss%*10 + latency_ms`; pick the
  best hub. Preference (`VRF_PREFERRED_HUB`: CORP/VOICE→hub1, GUEST→hub2) is sticky
  with hysteresis — only fail over when the active path is **degraded**
  (loss ≥ 5% OR latency ≥ 3x baseline) **and** the alternative is ≥15% better;
  recover to preference when it is healthy again. Changes emitted as JSON + a counter.

## Run

```bash
python3 controller.py                 # serve :9362 (Prometheus /metrics) + JSON events on stdout
python3 controller.py --port 9362 --interval 5
python3 controller.py --once          # print one scrape to stdout and exit
python3 controller.py --selftest      # validate exposition + path logic
```

Telegraf scrape config (Phase 2): `[[inputs.prometheus]] urls = ["http://<host>:9362/metrics"]`.

JSON event lines (stdout) for Loki/Fluentd: `{"event":"rekey",...}`,
`{"event":"path_change","reason":"degradation|recovery",...}`.

## Environment

- `DIURNAL_PERIOD` (s): 24h cycle compression. Default `3600` (1 real hour = 1 day).
- `TOPO_SPEC`: path to the spec. Default `../topology-spec.yaml`.

## Metric + label schema (STABLE — Phase 2 depends on this)

All metrics are `sdwan_*`. Per-tunnel metrics carry `device,tunnel,site,site_type,hub`;
policy metrics carry `device,site,site_type,vrf,hub`.

**`device`** is the universal join key: it equals the spoke/site node name (same string as
SNMP `device`, log `device`, and flow `device` labels), enabling cross-signal joins such as
`interface_ifHCInOctets * on(device) sdwan_path_active`.

| Metric | Type | Labels | Meaning |
|---|---|---|---|
| `sdwan_tunnel_latency_ms` | gauge | **device**, tunnel, site, site_type, hub | Modelled RTT (ms) |
| `sdwan_tunnel_jitter_ms`  | gauge | **device**, tunnel, site, site_type, hub | Modelled jitter (ms) |
| `sdwan_tunnel_loss_pct`   | gauge | **device**, tunnel, site, site_type, hub | Modelled packet loss (%) |
| `sdwan_tunnel_rekeys_total` | counter | **device**, tunnel, site, site_type, hub | Cumulative WG rekeys |
| `sdwan_path_active`       | gauge | **device**, site, site_type, vrf, hub | `1` on the active hub for that site/vrf |
| `sdwan_path_changes_total` | counter | (none) | Cumulative path-selection changes |

Label values use the generator's node names (`ce_branch1`, `ce_hub1`, …); `vrf` ∈
{CORP, VOICE, GUEST}; `site_type` ∈ {branch, hub, dc}; `device` = `site` (spoke node name).

## Deploy (Phase 2.2)

Build from the repo root (build context must include `controller/`, `trafficgen/`, and `topology-spec.yaml`):

```bash
docker build -t noc-controller -f controller/Dockerfile .
```

Add to `telemetry/docker-compose.yml` — already wired as service `controller` at static IP
`172.20.20.56` on the `clab` external network, with `/var/run/docker.sock` mounted read-only.
Telegraf at `.52` scrapes `http://172.20.20.56:9362/metrics` on its 30s interval.

Netem reads now use `docker exec clab-sdwan_mpls_noc-<node> tc qdisc show dev eth1` via the
docker.sock — no host-netns privilege needed (replaces the broken `ip netns exec` path).

The trafficgen service (`noc-trafficgen`) runs alongside at `.57`, also docker.sock-mounted,
driving real BusyBox-nc TCP flows across the MPLS/WireGuard overlay every 30 s so SNMP
counters climb. See `trafficgen/README.md` for backend details.

## Shortcuts (`# ponytail:` in code)

- Metrics modelled, not ping-measured (air-gap-safe, fault-responsive). Upgrade: real
  RTT via `docker exec ping` the WG peer IP.
- Netem read via `docker exec ... tc` over docker.sock (was broken `ip netns exec`).
