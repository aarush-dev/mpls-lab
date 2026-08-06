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
- **Metrics**: the 4 tunnel signals (latency/jitter/loss/rekeys) are drawn
  **analytically** per tick, COPIED from the dataset generator so live telemetry sits
  in the training distribution. There is no dataplane measurement (the wg0-ping path
  was removed). Source of truth: `synthetic/generate.py:_gen_tunnels` (326-334) +
  `_diurnal` (71-82); guarded by `test_tunnel_model.py`.
  - **Per-tick draw** (`d = _diurnal(now) ∈ [0.15,1.0]`, per-site_type baseline from
    `synthetic/profile.json`): `latency = max(1, gauss(lat.mean, lat.std*0.4) + d*8) +
    eth1 netem readback`; `jitter = max(0.1, gauss(jit.mean, jit.std*0.5) + d*0.5)`;
    `loss = max(0, gauss(loss.mean, max(loss.std,0.05)*0.5)) + d*0.02 + eth1 netem`.
    No EMA — the generator draws each bucket independently.
  - **Diurnal** runs on **real UTC wall-clock** (matches the dataset), so a full cycle
    spans 24h; `DIURNAL_PERIOD` no longer drives these 4 (still drives offered load).
  - **Baselines** load from the shipped `synthetic/profile.json` (`tunnel_baseline_by_site`),
    with a baked fallback carrying the real means/stds for `--selftest`/no-file.
  - **Faults**: injection still writes `eth1` netem, read back and added to latency/loss;
    a calibrated overlay (shared `faults/signatures.py`) suppresses that readback while
    ramping so there is no double-count — see below.
- **Rekey events**: an **inert** running counter, seeded once per tunnel from the
  baseline range then bumped spontaneously at the generator's rate (rescaled to the
  tick) — matches the dataset (whose fault ramp never touches rekeys), so **not**
  loss-coupled. Emitted as JSON events + a cumulative counter metric.
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

- `DIURNAL_PERIOD` (s): compresses the offered-LOAD cycle (trafficgen). Default `3600`.
  Does NOT affect the 4 tunnel signals — their diurnal is real UTC wall-clock.
- `TOPO_SPEC`: path to the spec. Default `../topology-spec.yaml`.
- The 4 tunnel signals read `../synthetic/profile.json` (shipped into the image) for
  baselines + `step_s`; absent → the baked fallback keeps the distribution correct.

## Metric + label schema (STABLE — Phase 2 depends on this)

All metrics are `sdwan_*`. Per-tunnel metrics carry `device,tunnel,site,site_type,hub`;
policy metrics carry `device,site,site_type,vrf,hub`.

**`device`** is the universal join key: it equals the spoke/site node name (same string as
SNMP `device`, log `device`, and flow `device` labels), enabling cross-signal joins such as
`interface_ifHCInOctets * on(device) sdwan_path_active`.

All four `sdwan_tunnel_*` metrics are marked **SIMULATED** in their own Prometheus
`# HELP` text (`controller.py` `render_prometheus()`) — they are analytic draws around
the calibrated baseline (copied from the dataset generator), NOT dataplane measurements.
The netem-readback fault term is a config value read back off the qdisc. Any doc/consumer
calling them "measured telemetry" is wrong.

| Metric | Type | Labels | Meaning |
|---|---|---|---|
| `sdwan_tunnel_latency_ms` | gauge | **device**, tunnel, site, site_type, hub | SIMULATED: calibrated baseline + diurnal bump + netem delay read back from the site uplink qdisc config (ms) |
| `sdwan_tunnel_jitter_ms`  | gauge | **device**, tunnel, site, site_type, hub | SIMULATED: calibrated baseline + diurnal bump (ms) |
| `sdwan_tunnel_loss_pct`   | gauge | **device**, tunnel, site, site_type, hub | SIMULATED: calibrated baseline + diurnal bump + netem loss read back from the site uplink qdisc config (%) |
| `sdwan_tunnel_rekeys_total` | counter | **device**, tunnel, site, site_type, hub | SIMULATED: baseline-seeded running counter + spontaneous rate; inert (not loss-coupled) |
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
the overlay is dropped and the next tick's analytic draw is back at baseline. Expired
overlays (past `t_end = t_impact + duration`) are pruned in the
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

- The 4 tunnel signals are analytic draws copied from the dataset generator
  (`synthetic/generate.py`) so sim == training distribution; no ping/measurement path.
- Netem read via `docker exec ... tc` over docker.sock (was broken `ip netns exec`).
