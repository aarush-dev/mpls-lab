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
Inject **high** severity on a CE (`congestion`, `tunnel_degrade`) and let it run to
impact. Rank mode fires on the *rise*, so the target stands out even against live
noise. If it under-fires, lower the bar: `PA_RISE_MARGIN=0.10` (env on noc-pa-alerts).

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
