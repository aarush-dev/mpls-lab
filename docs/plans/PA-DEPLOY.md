# Real PA (graph v2) — go-live runbook

Status: pipeline **built + verified end-to-end on live data**. Serve = best model
`reduced_graph_v2_test0.859` (Test PR-AUC 0.859, TPR@1% 58.5%, gate calibrated).

## What runs where

| Component | Env | Port | Notes |
|---|---|---|---|
| PA inference service | `/root/pa-venv` (torch 2.5.1+cpu) | 8001 | graph snapshot mode; weights + `dataapi/graph_data/topology_edges.parquet` |
| dataapi | lab env | 8080 | live; `export_df` reads VictoriaMetrics :8428 |
| copilot | system python3 (copilot deps) | — | calls PA over HTTP via `real_pa.py`; no torch needed |

Separation: copilot never imports torch — it hits the PA service over HTTP.

## Verified (live, this branch)

- `dataapi.export.export_df(now-90m, now, 30)` → 184k rows, 256 entities, 70 devices, **all 28 channels populated**.
- `build_windows(now)` → 256 live entity windows, 168×28, NaN-masked.
- `POST /v1/predict/snapshot` (real graph v2) → **256 records, 70 device-nodes, ~1 s** whole topology; per-entity risk (p_any 0.002–0.869, causes across fault types).
- `snapshot_edges("live_lab", now)` → 52+ edges resolve (units correct).
- `real_pa` shim → §3.3 record round-trips `emulate.persist()` (alert_id + device); consumers (`fault_type`/`is_abstain`/`drift_state`) read it.
- Gate correctly **silent** on a healthy lab (0 alerting).

## Go live (on the VM — the sandbox can't hold a persistent server)

```bash
# 1. topology edges (idempotent; ExecStartPre also does this)
/root/pa-venv/bin/python /root/LAB/dataapi/topology_edges.py

# 2. PA service
cp /root/LAB/noc-pa.service /etc/systemd/system/ && systemctl daemon-reload
systemctl enable --now noc-pa
curl -s :8001/v1/health | python3 -m json.tool     # serving_mode: graph-snapshot

# 3. point copilot at live dataapi + real PA, then flip the seam
#    copilot/config.yaml: emulate_pa: false   (pa_mode already snapshot, pa_url :8001)
export COPILOT_DATAAPI_URL=http://127.0.0.1:8080
#    predictor loop (system python3, copilot deps):
python3 -m copilot.emulator.predictor
```

`emulate_pa: false` is left UNSET in git (default true) — flipping it is a
deliberate production switch, not a code default. Flip it in the deploy config.

## Remaining validation (not a code gap)

- **Live-fault alert firing.** The gate stayed silent because no fault was active.
  Inject one (`POST :8080/faults/inject`), wait past its lead time, and confirm an
  entity crosses the conformal threshold → ledger row → forensic case. Whether live
  containerlab fault *signatures* trip the synthetic-trained gate is the one
  empirical unknown; if it under/over-fires, tune `pa_target_fpr` (0.02/0.05 warns
  earlier) — the model + wiring are correct.
- **entity_type/site_type codes** are best-effort (`window/vocab.py`); exact training
  vocab wasn't shipped. Minor embedding signal.

## Tickets

Closed: #99 #100 #101 #102 #103 #104 #106 #107 #108 #109 #111. Superseded: #98 #105.
Open: #110 (G5 live-fault validation), #112 (docs).
