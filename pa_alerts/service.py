"""pa_alerts — live PA prediction → dashboard alerts (demo bridge, copilot bypassed).

A judge injects a fault from the Grafana panel; this service scores the live
topology through the graph-v2 PA model every `interval` seconds and exposes the
current predictions + alerts for a Grafana panel to show. No copilot in the path.

Two detection modes (env PA_ALERT_MODE):
  rank      (A, default) — alert on the RISE in an entity's PA risk vs its own
            rolling baseline (EWMA). A freshly injected fault makes its entity's
            p_any jump; ambient-high entities don't jump. Robust to the live
            train/serve FPR noise that makes absolute thresholds unreliable.
  threshold (B) — alert when calibrated_probability >= a threshold recalibrated on
            live-healthy data (pa_alerts/calibration.json). Switch to this once B's
            calibration is collected; no code change, just the env flag.

Pipeline per tick: build_windows(now) [live VM via dataapi.export] -> POST the PA
service /v1/predict/snapshot -> per-entity records -> alert rule -> in-memory state.

Run (pa-venv, needs the PA service on :8001 + live dataapi/VM):
  PA_URL=http://127.0.0.1:8001 PA_ALERT_MODE=rank \
  /root/pa-venv/bin/uvicorn pa_alerts.service:app --host 127.0.0.1 --port 8002
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone

import httpx
from fastapi import FastAPI

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from copilot.window.vocab import CHANNELS
from copilot.window.build import build_windows

PA_URL = os.environ.get("PA_URL", "http://127.0.0.1:8001").rstrip("/")
TOPOLOGY_ID = os.environ.get("PA_TOPOLOGY_ID", "live_lab")
MODE = os.environ.get("PA_ALERT_MODE", "rank")
INTERVAL = int(os.environ.get("PA_ALERT_INTERVAL_S", "15"))
TARGET_FPR = float(os.environ.get("PA_TARGET_FPR", "0.01"))
EWMA_ALPHA = float(os.environ.get("PA_EWMA_ALPHA", "0.2"))     # baseline responsiveness
RISE_MARGIN = float(os.environ.get("PA_RISE_MARGIN", "0.25"))  # p_any rise to alert (rank mode)
MIN_RISK = float(os.environ.get("PA_MIN_RISK", "0.45"))        # floor so tiny rises don't fire
TOP_K = int(os.environ.get("PA_TOP_K_ALERTS", "6"))            # cap alerts shown (readable banner)
CALIB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "calibration.json")

app = FastAPI(title="PA live alerts")

# ---- shared state (single writer thread; GIL-atomic dict swaps) --------------
_STATE: dict = {"ts": None, "predictions": [], "alerts": [], "mode": MODE,
                "n_scored": 0, "error": None, "warm": False}
_BASELINE: dict[str, float] = {}      # entity -> EWMA of p_any (rank mode)
_CALIB: dict = {}


def _load_calib():
    global _CALIB
    if os.path.exists(CALIB_PATH):
        _CALIB = json.load(open(CALIB_PATH))


def _now_iso() -> str:
    return datetime.fromtimestamp((int(time.time()) // 30) * 30,
                                  tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _score_once() -> list[dict]:
    """Build live windows, score the whole topology in one graph pass."""
    now = _now_iso()
    wins = build_windows(now, CHANNELS)
    if not wins:
        return []
    entities = [{"entity_id": e, "device": w["device"], "entity_type": w["entity_type"],
                 "vrf": w["vrf"], "window": w["window"],
                 "entity_type_code": w["etc"], "site_type_code": w["stc"]}
                for e, w in wins.items()]
    r = httpx.post(f"{PA_URL}/v1/predict/snapshot",
                   json={"topology_id": TOPOLOGY_ID, "window_end_ts": now,
                         "entities": entities, "target_fpr": TARGET_FPR}, timeout=60)
    r.raise_for_status()
    return r.json().get("records", [])


def _row(rec: dict) -> dict:
    ft = (rec.get("risk") or {}).get("fault_types") or []
    return {"entity_id": rec.get("entity_id"), "device": rec.get("device") or rec.get("entity_id"),
            "cause": ft[0]["cause"] if ft else None,
            "p_any": round(rec["risk"]["p_any_fault_in_horizon"], 4),
            "calibrated_probability": rec["decision"].get("calibrated_probability"),
            "time_to_impact_s": (ft[0].get("time_to_impact", {}) or {}).get("median_s") if ft else None}


def _alerts_rank(rows: list[dict]) -> list[dict]:
    """A: flag entities whose p_any rose sharply above their own EWMA baseline.

    Two demo-critical properties:
      - baseline of an ALERTING entity is FROZEN (not updated), so a sustained fault
        stays flagged instead of the EWMA chasing the fault and clearing the alert.
      - only the top TOP_K rises are returned, so the banner stays readable.
    """
    alerting_ids = set()
    out = []
    for r in rows:
        e = r["entity_id"]; risk = r["p_any"]
        base = _BASELINE.get(e)
        if base is not None and risk >= MIN_RISK and (risk - base) >= RISE_MARGIN:
            out.append({**r, "baseline": round(base, 4), "rise": round(risk - base, 4)})
            alerting_ids.add(e)
    # update EWMA only for NON-alerting entities (a fault's own spike must not erode
    # its baseline, or the alert self-clears while the fault is still live)
    for r in rows:
        e = r["entity_id"]
        if e in alerting_ids:
            _BASELINE.setdefault(e, r["p_any"])   # seed once if unseen; never chase up
            continue
        _BASELINE[e] = r["p_any"] if e not in _BASELINE else \
            (1 - EWMA_ALPHA) * _BASELINE[e] + EWMA_ALPHA * r["p_any"]
    out.sort(key=lambda a: -a["rise"])
    return out[:TOP_K]


def _alerts_threshold(rows: list[dict]) -> list[dict]:
    """B: recalibrated absolute gate. Per-entity threshold if calibrated, else global."""
    thr_global = _CALIB.get("threshold_global", 0.9)
    per = _CALIB.get("threshold_per_entity", {})
    out = []
    for r in rows:
        cal = r.get("calibrated_probability") or 0.0
        thr = per.get(r["entity_id"], thr_global)
        if cal >= thr:
            out.append({**r, "threshold": round(thr, 4)})
    out.sort(key=lambda a: -(a.get("calibrated_probability") or 0))
    return out


def _loop():
    _load_calib()
    while True:
        try:
            recs = _score_once()
            rows = [_row(r) for r in recs]
            rows.sort(key=lambda x: -x["p_any"])
            alerts = _alerts_threshold(rows) if MODE == "threshold" else _alerts_rank(rows)
            _STATE.update({"ts": _now_iso(), "predictions": rows[:20], "alerts": alerts,
                           "n_scored": len(rows), "error": None,
                           "warm": len(_BASELINE) > 0 or MODE == "threshold"})
        except Exception as e:  # keep the demo loop alive
            _STATE["error"] = repr(e)
        time.sleep(INTERVAL)


@app.on_event("startup")
def _start():
    threading.Thread(target=_loop, daemon=True).start()


@app.get("/health")
def health():
    return {"status": "ok", "mode": MODE, "pa_url": PA_URL, "interval_s": INTERVAL,
            "calibrated": bool(_CALIB), "last_ts": _STATE["ts"], "n_scored": _STATE["n_scored"],
            "error": _STATE["error"]}


@app.get("/alerts")
def alerts():
    """Current PA alerts for the dashboard. `predictions` = full ranked risk table."""
    return {"ts": _STATE["ts"], "mode": MODE, "warm": _STATE["warm"],
            "alerts": _STATE["alerts"], "predictions": _STATE["predictions"],
            "n_scored": _STATE["n_scored"]}
