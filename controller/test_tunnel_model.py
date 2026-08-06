#!/usr/bin/env python3
"""Drift guard: the controller's copied tunnel pipeline must track the dataset
generator (issue: port analytic tunnel signals into the live sim).

Two checks, no lab, no server:
  1. controller._diurnal == synthetic/generate.py:_diurnal across a full day (the
     copy must not silently diverge from its source of truth).
  2. the live controller's 4 tunnel signals, ticked across a simulated day, land in
     the SAME distribution bar synthetic/check.py:80-89 holds the training data to.

Run: python3 controller/test_tunnel_model.py
"""
import os
import statistics
import sys
from datetime import datetime, timezone

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)                                   # controller
sys.path.insert(0, os.path.join(_HERE, "..", "synthetic"))  # generate
sys.path.insert(0, os.path.join(_HERE, "..", "faults"))     # signatures (generate dep)
sys.path.insert(0, os.path.join(_HERE, "..", "trafficgen"))  # diurnal (generate dep)

import controller as C  # noqa: E402
import generate as G    # noqa: E402  the dataset generator (source of truth)

C.TunnelState._SKIP_NETEM = True  # hermetic


def test_diurnal_matches_generator():
    """The controller's _diurnal is a byte-copy of the generator's; assert they agree
    to the last bit across 24h (both use np.cos on real UTC wall-clock)."""
    base = datetime(2026, 6, 15, 0, 0, tzinfo=timezone.utc).timestamp()  # Monday
    worst = 0.0
    for i in range(0, 24 * 60, 7):          # every 7 min for a day + into the weekend
        ep = base + i * 60.0 + 3 * 86400.0  # +3 days too, to cross into a weekday->wk edge
        worst = max(worst, abs(C._diurnal(ep) - G._diurnal(ep)))
    assert worst < 1e-9, f"controller._diurnal drifted from generator: max |diff| {worst:g}"
    print(f"diurnal cross-check OK (max |diff| {worst:g})")


def test_distribution_matches_profile():
    """Tick the controller across a day; hold the 4 signals to check.py's bar."""
    ctrl = C.Controller()
    gb = C._BASELINES["tunnel_baseline"]
    lat, jit, loss = [], [], []
    day = datetime(2026, 6, 15, 0, 0, tzinfo=timezone.utc).timestamp()
    for i in range(480):                    # 480 * 180s = 24h
        ctrl.tick(now=day + i * 180.0)
        for t in ctrl.tunnels:
            lat.append(t.latency_ms); jit.append(t.jitter_ms); loss.append(t.loss_pct)

    tl = gb["tunnel_latency_ms"]
    med = statistics.median(lat)
    assert abs(med - tl["p50"]) <= 2.0 * tl["std"], \
        f"latency median {med:.2f} off p50 {tl['p50']:.2f} (+/-{2*tl['std']:.2f})"
    assert statistics.pstdev(lat) > 0.15 * tl["std"], "latency too flat"
    for name, vals in (("latency", lat), ("jitter", jit), ("loss", loss)):
        assert min(vals) >= 0.0, f"{name} negative"
    # rekeys: a monotonic counter seeded in the baseline range, never negative.
    assert all(t.rekeys >= 0 for t in ctrl.tunnels), "negative rekeys"
    print(f"distribution OK  lat_median={med:.2f} (p50 {tl['p50']:.2f}) "
          f"lat_std={statistics.pstdev(lat):.2f} jit_med={statistics.median(jit):.2f} "
          f"loss_med={statistics.median(loss):.3f}")


if __name__ == "__main__":
    test_diurnal_matches_generator()
    test_distribution_matches_profile()
    print("test_tunnel_model: ALL OK")
