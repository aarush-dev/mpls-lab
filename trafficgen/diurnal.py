#!/usr/bin/env python3
"""Diurnal utilization model — single source of truth for the 24h traffic curve.

Imported by both trafficgen (drives flow rate) and the controller (congestion
inflates tunnel latency/loss), so the telemetry and the offered load move together.

Model: a 24h cycle compressed to PERIOD seconds. Utilization in [0,1]:
  - night trough  (~0.10) 00:00-06:00
  - morning ramp  up to business plateau
  - business hrs  (~0.85) with a lunch dip (~0.55) around 12:00-13:00
  - evening decay back to trough
Built from cheap smooth functions (no numpy) -> realistic, non-degenerate, repeatable.

Per-VRF shaping multiplies the base curve by a class profile:
  VOICE  small + steady (low diurnal swing; people on calls all day)
  CORP   bursty + strongly diurnal (the office curve)
  GUEST  best-effort, leans late (evening/break usage)

# ponytail: closed-form curve, not a replayed real trace. Ceiling: shape is
#   hand-tuned, not calibrated to a capture. Upgrade path: fit coefficients to a
#   real NetFlow day in the synthetic/ phase.
"""
import math

# Per-VRF (base_floor, diurnal_gain, phase_shift_hours)
# util_vrf = clamp(floor + gain * base_curve(shifted_hour))
VRF_PROFILE = {
    "VOICE": (0.35, 0.45, 0.0),   # steady, modest swing
    "CORP":  (0.05, 0.95, 0.0),   # bursty office curve, big swing
    "GUEST": (0.05, 0.70, 2.5),   # shifted later (evening lean)
}


def _bell(hour, center, width):
    """Gaussian-ish bump in [0,1] centered at `center` hours."""
    return math.exp(-((hour - center) ** 2) / (2 * width ** 2))


def base_curve(hour):
    """Base utilization in [0,1] for a given hour-of-day (0..24, wraps).

    Business plateau 09-17 with a lunch dip at 12:30; small night floor.
    """
    h = hour % 24.0
    # Plateau: bump spanning the workday (09-17), peaking late morning + mid afternoon.
    work = _bell(h, 13.5, 3.0) * 0.92    # afternoon mass
    morning = _bell(h, 10.0, 1.6) * 0.80  # morning ramp/peak
    lunch_dip = _bell(h, 12.5, 0.9) * 0.45  # carve out lunch
    night_floor = 0.10
    val = night_floor + max(work, morning) - lunch_dip
    return max(0.0, min(1.0, val))


def util(hour, vrf=None):
    """Utilization in [0,1]. If vrf given, apply that class's profile."""
    if vrf is None:
        return base_curve(hour)
    floor, gain, shift = VRF_PROFILE[vrf]
    return max(0.0, min(1.0, floor + gain * base_curve(hour + shift)))


def hour_of_cycle(t_seconds, period_seconds, start_hour=0.0):
    """Map wall-clock seconds onto a 0..24 hour-of-day, cycle compressed to period."""
    frac = (t_seconds % period_seconds) / period_seconds
    return (start_hour + frac * 24.0) % 24.0


def _selftest():
    # Curve must be non-degenerate: clear night<peak separation, lunch dip present.
    peak = max(base_curve(h) for h in [x / 4 for x in range(96)])
    trough = min(base_curve(h) for h in [x / 4 for x in range(96)])
    assert peak > 0.7, f"peak too low: {peak}"
    assert trough < 0.2, f"trough too high: {trough}"
    assert peak - trough > 0.5, f"diurnal swing degenerate: {peak - trough}"
    # Lunch dip: 12:30 lower than its 11:00 and 14:30 neighbours.
    assert base_curve(12.5) < base_curve(11.0), "no lunch dip vs morning"
    assert base_curve(12.5) < base_curve(14.5), "no lunch dip vs afternoon"
    # All VRF curves stay in [0,1] and VOICE swings less than CORP.
    swing = {}
    for v in VRF_PROFILE:
        vals = [util(h, v) for h in [x / 4 for x in range(96)]]
        assert all(0.0 <= x <= 1.0 for x in vals), f"{v} out of [0,1]"
        swing[v] = max(vals) - min(vals)
    assert swing["VOICE"] < swing["CORP"], "VOICE should swing less than CORP"
    # Cycle mapping covers a full day.
    hours = [hour_of_cycle(t, 100.0) for t in range(0, 100, 5)]
    assert max(hours) > 20 and min(hours) < 4, "cycle does not span the day"
    print("diurnal selftest OK  peak=%.2f trough=%.2f swing=%s" %
          (peak, trough, {k: round(v, 2) for k, v in swing.items()}))


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv or len(sys.argv) == 1:
        _selftest()
    if "--plot" in sys.argv:
        # ASCII curve for eyeballing.
        for h in range(24):
            bar = int(base_curve(h) * 50)
            print(f"{h:02d}h |{'#' * bar}")
