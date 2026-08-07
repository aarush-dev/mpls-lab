"""pa_alerts/calibrate.py — collect a HEALTHY-lab prediction distribution from the
live PA model and recalibrate the alert threshold (path B).

The synthetic-trained conformal gate over-fires on live telemetry (train/serve
gap). This samples the live model on a fault-free lab for a while, then sets a
threshold so the live-healthy false-alert rate matches the budget:

  threshold_global      = the (1 - target_fpr) quantile of ALL healthy
                          calibrated_probability samples.
  threshold_per_entity  = the same quantile per entity, so an ambiently-noisy
                          entity gets a higher bar (kills its standing false alerts)
                          while a quiet entity keeps a sensitive one.

Writes pa_alerts/calibration.json, consumed by service.py in PA_ALERT_MODE=threshold.
Run it while the lab is HEALTHY (no injected faults):

  PA_URL=http://127.0.0.1:8001 /root/pa-venv/bin/python -m pa_alerts.calibrate --minutes 30
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import defaultdict

import httpx
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from copilot.window.vocab import CHANNELS, entity_type_code, site_type_code
from copilot.window.build import build_windows

PA_URL = os.environ.get("PA_URL", "http://127.0.0.1:8001").rstrip("/")
TOPOLOGY_ID = os.environ.get("PA_TOPOLOGY_ID", "live_lab")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "calibration.json")


def _now_iso():
    from datetime import datetime, timezone
    return datetime.fromtimestamp((int(time.time()) // 30) * 30,
                                  tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _score():
    now = _now_iso()
    wins = build_windows(now, CHANNELS, etc_of=entity_type_code, stc_of=site_type_code)
    ents = [{"entity_id": e, "device": w["device"], "entity_type": w["entity_type"],
             "vrf": w["vrf"], "window": w["window"], "entity_type_code": w["etc"],
             "site_type_code": w["stc"]} for e, w in wins.items()]
    r = httpx.post(f"{PA_URL}/v1/predict/snapshot",
                   json={"topology_id": TOPOLOGY_ID, "window_end_ts": now, "entities": ents},
                   timeout=60)
    r.raise_for_status()
    return r.json().get("records", [])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=30)
    ap.add_argument("--interval", type=int, default=20)
    ap.add_argument("--target-fpr", type=float, default=0.01)
    args = ap.parse_args()

    per = defaultdict(list)
    allv = []
    deadline = time.time() + args.minutes * 60
    ticks = 0
    while time.time() < deadline:
        try:
            for rec in _score():
                cal = (rec.get("decision") or {}).get("calibrated_probability")
                if cal is not None:
                    per[rec["entity_id"]].append(float(cal))
                    allv.append(float(cal))
            ticks += 1
            print(f"tick {ticks}: {len(allv)} samples, {len(per)} entities", flush=True)
        except Exception as e:
            print("tick err:", repr(e), flush=True)
        time.sleep(args.interval)

    q = 1.0 - args.target_fpr
    calib = {
        "collected_ticks": ticks, "n_samples": len(allv), "target_fpr": args.target_fpr,
        "threshold_global": float(np.quantile(allv, q)) if allv else 0.9,
        # per-entity only where we have enough samples to trust the quantile
        "threshold_per_entity": {e: float(np.quantile(v, q)) for e, v in per.items() if len(v) >= 10},
    }
    json.dump(calib, open(OUT, "w"), indent=2)
    print(f"wrote {OUT}: global={calib['threshold_global']:.4f}, "
          f"per_entity={len(calib['threshold_per_entity'])} entities, samples={len(allv)}")


if __name__ == "__main__":
    main()
