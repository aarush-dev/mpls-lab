# SD-WAN controller (simulated)

Phase 1.4 component. Holds overlay policy, does path selection over the WireGuard
hub-spoke overlay, derives per-tunnel metrics, and exposes them as **Prometheus
text exposition over HTTP** for Telegraf (Phase 2) to scrape. Stdlib-only (plus
`PyYAML` to read the spec; falls back to a built-in default spec if unavailable,
so `--selftest` runs anywhere).

## What it does

- **Model** (`topo.py`): derives hubs/spokes/tunnels/VRFs from `../topology-spec.yaml`
  using the same index arithmetic as the generator. 28 spokes (24 branch + 4 dc)
  x 6 hubs = **168 tunnels**.
- **Metrics**: each tunnel's latency/jitter/loss is built from a **measured RTT
  baseline** plus additive modelled layers:
  - **Measured RTT cache** (enabled by `MEASURE_RTT=1`): a background thread runs
    `ping -c2 -q -W1 -I wg0 <peer-wg-ip>` via `docker exec` into each spoke every
    ~45 s. Propagation delay is ~constant so one refresh per minute is enough; this
    avoids pinging all tunnels on every 5 s tick. The real physical delay comes from
    per-site baseline netem the generator sets on each CE's `eth0` (branch ~41 ms,
    hub ~17 ms, dc ~12 ms), so the controller reads TRUE RTT.
  - **Per-tick layering**: `latency = measured_avg (cached) + queue_ms (diurnal
    congestion model) + eth1 netem readback (active faults) + noise`;
    `loss = max(measured_loss, modelled_floor) + micro-burst term`;
    `jitter = (ping max−min from cache) + AR(1) walk`.
  - The invented geography baseline was removed; the generator's site netem is now
    the single source of propagation truth. Fault injection still writes to `eth1`
    and is read back via netem readback, so fault-responsiveness is preserved.
  - When `MEASURE_RTT` is unset or `0` (e.g. `--selftest`), the controller falls
    back gracefully to the diurnal congestion model alone.
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
- `MEASURE_RTT` (`0`/`1`): enable live ping measurement. Set to `1` on the `controller`
  service in `telemetry/docker-compose.yml`. Requires `/var/run/docker.sock` mounted
  (already done). Unset or `0` → graceful fallback (no pings; congestion model only).
  `--selftest` always runs with measurement off.

## Metric + label schema (STABLE — Phase 2 depends on this)

All metrics are `sdwan_*`. Per-tunnel metrics carry `device,tunnel,site,site_type,hub`;
policy metrics carry `device,site,site_type,vrf,hub`.

**`device`** is the universal join key: it equals the spoke/site node name (same string as
SNMP `device`, log `device`, and flow `device` labels), enabling cross-signal joins such as
`interface_ifHCInOctets * on(device) sdwan_path_active`.

All four `sdwan_tunnel_*` metrics are marked **SIMULATED** in their own Prometheus
`# HELP` text (`controller.py` `render_prometheus()`) — the netem-readback term is a
config value read back off the qdisc, not a dataplane measurement. Any doc/consumer
calling them "measured telemetry" is wrong; only the wg0-ping component is a real measurement.

| Metric | Type | Labels | Meaning |
|---|---|---|---|
| `sdwan_tunnel_latency_ms` | gauge | **device**, tunnel, site, site_type, hub | SIMULATED: measured wg0 RTT + modelled congestion + netem delay read back from the site uplink qdisc config (ms) |
| `sdwan_tunnel_jitter_ms`  | gauge | **device**, tunnel, site, site_type, hub | SIMULATED: measured wg0 ping max-min + modelled congestion walk (ms) |
| `sdwan_tunnel_loss_pct`   | gauge | **device**, tunnel, site, site_type, hub | SIMULATED: measured wg0 loss + modelled floor/bursts + netem loss read back from the site uplink qdisc config (%) |
| `sdwan_tunnel_rekeys_total` | counter | **device**, tunnel, site, site_type, hub | Cumulative WG rekeys |
| `sdwan_path_active`       | gauge | **device**, site, site_type, vrf, hub | `1` on the active hub for that site/vrf |
| `sdwan_path_changes_total` | counter | (none) | Cumulative path-selection changes, fabric-wide; unlabelled and RNG-driven (moves from the modelled loss micro-bursts even with no fault injected) — not usable as fault-impact evidence |
| `sdwan_overlay_active` | gauge | site, fault_type | `1` while a calibrated fault overlay is ramping this site's tunnel series (see below) |

Label values use the generator's node names (`ce_branch1`, `ce_hub1`, …); `vrf` ∈
{CORP, VOICE, GUEST}; `site_type` ∈ {branch, hub, dc}; `device` = `site` (spoke node name).

## Fault overlay (calibrated live signal — issue #61)

An in-memory `_overlay` registry (cloned from the `_drift` pattern) makes a live-injected
fault emit the **dataset generator's calibrated signature** instead of the ad-hoc
netem-readback term. While an overlay is active for a site, the `sdwan_tunnel_*` series
ramp toward the calibrated peak on `faults/signatures.prog(elapsed wall-time vs the drawn
lead)` — the *same* shared ramp math the generator uses (`signatures.tunnel_ramp_targets`
+ `loss_peak` bump), so live telemetry is in-distribution with training.

The peak/lead table is loaded from the calibration artifact **`synthetic/profile.json`**
(`fault_signatures`) — the exact table the generator consumes (`generate.py:391`), incl.
its real-derived peaks — so live == training by construction. If the profile is absent
(e.g. `--selftest`, no dataset) it falls back to `signatures.default_signatures()`.

**Authoritative:** while an overlay is active the netem readback for **that whole site** is
forced to zero, so a simultaneous real `tc` action does **not** double-count (the real netem
still installs at impact — genuine packet effect — but is not added to the metric). On clear
the series glide back to baseline through the existing exponential smoothing (no
discontinuity). Expired overlays (past `t_end = t_impact + duration`) are pruned in the
per-tick GC, in place so a concurrent POST/clear is never dropped.

Endpoints (mirroring `/fault/drift[/clear]`):

- **`POST /fault/overlay`** `{"site": ..., "fault_type": ..., "lead_s": N, "duration": N, "severity": "low|medium|high"}`
  — registers the episode (`t_impact = now + lead_s`, peak at `t_impact + 0.3*duration`).
  `lead_s` omitted → the signature's calibrated lead. HTTP 400 unless `site` is a known
  spoke, `fault_type` is a `tunnel_ramp` kind, `lead_s ≥ 0`, `duration ≥ 10s`, and
  `severity ∈ {low,medium,high}`.
- **`POST /fault/overlay/clear`** `{"site": ...}` — drops the overlay early (revert-now).
- **`GET /fault/overlay`** → JSON `{site: {fault_type, t_start, t_impact, t_end, expires}}`
  (the env-metrics sidecar reads this to drive the same ramp into optics/thermal, #59 T3).

Only `tunnel_ramp`-kind faults post an overlay; `iface_down`/backbone faults match via their
real action + control-plane events instead. Seam test: `controller/test_overlay.py`.

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
The read is hoisted per-site (once per spoke, not once per tunnel), so a tick costs
28 docker execs instead of 168 (`controller.py:435`).

The trafficgen service (`noc-trafficgen`) runs alongside at `.57`, also docker.sock-mounted,
driving real BusyBox-nc TCP flows across the MPLS/WireGuard overlay every 30 s so SNMP
counters climb. See `trafficgen/README.md` for backend details.

## Shortcuts (`# ponytail:` in code)

- RTT now measured via `docker exec ping` over wg0 (MEASURE_RTT=1). Congestion/jitter/loss
  model is kept as an additive layer on top of measured baseline.
- Netem read via `docker exec ... tc` over docker.sock (was broken `ip netns exec`).
