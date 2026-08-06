# PA-on-dashboard demo — judge runbook

Goal: a judge injects a fault from the Grafana panel; our PA pipeline predicts it;
the prediction shows as an **alert/notification on the dashboard**. Copilot bypassed.

## Architecture (copilot NOT in the path)

```
Grafana "Inject fault" panel ──▶ dataapi /faults/inject ──▶ netem on container
                                                                    │
                                              telemetry → VictoriaMetrics :8428
                                                                    │
pa_alerts service (:8002)  every 15s:
    build_windows(now)  ──▶  PA model /v1/predict/snapshot (:8001, graph v2)
                                                                    │
                              alert rule (rank=A / threshold=B) ──▶ /alerts state
                                                                    │
Grafana plugin  ◀── dataapi /pa/alerts (proxy) ◀────────────────────┘
   PaAlertsBanner: red banner + toast "PA prediction: <device> <cause> <p%> ETA"
```

## Services to start on the VM (systemd)

```bash
# 0. topology edges (once; ExecStartPre also runs it)
/root/pa-venv/bin/python /root/LAB/dataapi/topology_edges.py

# 1. PA model service (graph v2, :8001)
cp /root/LAB/noc-pa.service /etc/systemd/system/ && systemctl enable --now noc-pa

# 2. PA live-alerts bridge (:8002, mode A = rank)
cp /root/LAB/noc-pa-alerts.service /etc/systemd/system/ && systemctl enable --now noc-pa-alerts

# 3. dataapi must be up on :8000 (serves /faults/* and proxies /pa/alerts)
(cd /root/LAB/dataapi && nohup bash start.sh > /tmp/dataapi.log 2>&1 & disown)

# 4. Grafana plugin already polls dataapi :8000 — rebuild if needed:
(cd "/root/LAB/grafana ui/plugin" && npm run build)   # copilot tsc errors are pre-existing; webpack still builds
```

Health checks:
```bash
curl :8001/v1/health         # serving_mode: graph-snapshot
curl :8002/health            # mode: rank, last_ts not null
curl :8000/pa/alerts         # {alerts:[], predictions:[...], n_scored: ~256}
```

## Demo flow

1. Open the Grafana app → the **PA banner** sits at the top: "PA pipeline: monitoring N entities — no fault predicted" (green dot). Let it run ~1–2 min so the rank baseline warms.
2. Judge opens the **Inject fault** panel → picks a scenario + target (see below) → inject.
3. Within a few ticks the target's PA risk rises → banner turns red: **"PA PREDICTION — <device> <cause> <p%> ETA"**, and a toast pops.

### Reliable demo faults (strong, model-visible)
Inject **high** severity on a CE **device** (`congestion`, `tunnel_degrade`) and let it
run. The injected CE's device p_any climbs from ~0.3 baseline to ~0.85 by impact and
fires cleanly, alone.

Key facts learned from live runs:
- **Alerting is scoped to `device` entities.** Tunnel/interface entities have very noisy
  live p_any (swings ~0.3 tick-to-tick) and were the false-positive engine; devices are
  stable and well-separated. Not a ground-truth echo — a class scope.
- **Fire happens near/after impact, not at inject.** `build_windows` uses a 168×30s (84 min)
  window; a fresh fault only fills a few slots during buildup, so the device p_any ramps
  with the fault and crosses the bar around the fault's **impact** time (`t_impact` in
  `/faults/active`), typically 3–5 min after inject for a 300s buildup. Let it run.
- **A recently-faulted entity stays hot ~84 min** (its fault sits in the window). For a
  clean re-demo pick a *different* CE, or wait for the window to flush.

Rank params (env on noc-pa-alerts): `PA_RISE_MARGIN=0.15`, `PA_MIN_RISK=0.6`,
`PA_CONSEC_TICKS=2` (sustained-rise confirm kills single-tick spikes). If it under-fires,
lower `PA_MIN_RISK=0.5` or `PA_RISE_MARGIN=0.10`.

## Path A vs B (switch, no code change)

- **A (now):** `PA_ALERT_MODE=rank` — fires on the rise in an entity's PA risk vs its
  own EWMA baseline. Robust to the live train/serve FPR gap. Default.
- **B (when calibrated):** collect a healthy-lab distribution, then flip the env flag:
  ```bash
  # run while the lab is HEALTHY (no faults), ~30 min
  PA_URL=http://127.0.0.1:8001 /root/pa-venv/bin/python -m pa_alerts.calibrate --minutes 30
  # -> pa_alerts/calibration.json ; then:
  systemctl set-environment PA_ALERT_MODE=threshold   # or edit noc-pa-alerts.service
  systemctl restart noc-pa-alerts
  ```
  Threshold mode then uses the recalibrated per-entity gate.

## Why not raw conformal alert on live data
The synthetic-trained gate over-fires on live telemetry (train/serve gap) and needs
the 84-min buildup window filled. Rank mode (A) sidesteps both by watching the
*change*; recalibration (B) fixes the absolute gate for live. Both are real model
output — no ground-truth echo.

## Validated
- Live: build_windows → PA snapshot → 256 records, ~1 s (proven).
- Rank logic: ambient-high stays silent, injected spike fires (unit + live smoke).
- /alerts shape matches the banner; toast on new alert.
- Model detects faults on its own data: 76% TPR @ ~1% FPR (labeled test windows).
- **Full dry-run (2026-08-06, mode A):** `congestion high ce_branch7 300s`. 0 alerts
  through buildup; fired **pre-impact** at p_any 0.81 (~74 s before t_impact), climbed
  0.81→0.90→0.98, ETA counting down, on the `:8000/pa/alerts` dashboard feed. Only the
  injected device sustained (one collateral device was a 2-tick transient, cleared).
- **Threshold mode B not demo-ready:** even after 15-min recalibration it over-fires on
  the live lab (device calibrated_probability is non-stationary, swings ~0.5–0.8 healthy).
  Needs a 45–60 min healthy calibration to set per-entity bars above the swings. Use A.
