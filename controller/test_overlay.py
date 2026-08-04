#!/usr/bin/env python3
"""Seam test: controller fault-overlay ramp (issue #61).

Black-box on the emitted metric: register an overlay, advance ticks, and assert
the tunnel latency ramps to the CALIBRATED peak (from the shared signature table),
returns to baseline on clear, and does NOT double-count a simultaneous netem
readback (overlay authoritative). Also covers the HTTP handler validation.

Run: python3 controller/test_overlay.py   (no lab, no server)
"""
import json
import os
import sys
import threading
import urllib.request as R
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import controller as C  # noqa: E402

C.TunnelState._SKIP_NETEM = True     # hermetic: no docker exec
C.TunnelState._MEASURE_RTT = False   # no pinging

QUIET = C.PERIOD_SECONDS * (3.0 / 24.0)  # trough hour: low healthy latency


def _sweep(ctrl, site, t_from, t_to, netem=None, ticks=40):
    """Tick the site with `now` sweeping t_from->t_to; return mean tunnel latency
    at the END (so exponential smoothing has converged on the final instant)."""
    site_tuns = [t for t in ctrl.tunnels if t.site == site]
    for i in range(ticks):
        now = t_from + (t_to - t_from) * i / (ticks - 1)
        for t in site_tuns:
            t.update(now, netem=netem, overlay=ctrl._overlay.get(t.site))
    return sum(t.latency_ms for t in site_tuns) / len(site_tuns)


def _ramp_case(fault):
    """Ramp `fault` to its peak knot, then clear; assert reach-peak + return."""
    ctrl = C.Controller()
    site = ctrl.tunnels[0].site
    peak_expect = max(ctrl._sigs[fault]["lat_peak"], 0.0)

    base = _sweep(ctrl, site, QUIET, QUIET + 200.0)
    assert base < 25.0, f"[{fault}] baseline already high: {base:.1f}"

    t0 = QUIET + 300.0
    lead, dur = 50.0, 60.0
    ctrl.set_overlay(site, fault, lead_s=lead, duration=dur, t_start=t0)
    peak_now = t0 + lead + 0.3 * dur
    peak = _sweep(ctrl, site, t0, peak_now)
    # The floored target is max(lat_peak, healthy*1.15); baseline is tiny here, so
    # the peak should approach lat_peak. Require it to clear well past baseline.
    assert peak > max(base * 2, 0.6 * peak_expect), \
        f"[{fault}] did not ramp toward peak {peak_expect:.1f}: {peak:.1f}"
    assert peak <= peak_expect * 1.3 + 2.0, \
        f"[{fault}] overshot calibrated peak {peak_expect:.1f}: {peak:.1f}"

    ctrl.clear_overlay(site)
    back = _sweep(ctrl, site, peak_now, peak_now + 200.0)
    assert back < base + 5.0, f"[{fault}] no return to baseline: {back:.1f}"
    print(f"ramp[{fault}]: base={base:.1f} peak={peak:.1f} "
          f"(cal {peak_expect:.1f}) back={back:.1f} OK")


def test_ramp_absolute_and_relative_peaks():
    _ramp_case("congestion")     # real-derived absolute peak
    _ramp_case("gray_failure")   # peak scales off base_lat -> exercises the bases


def test_no_double_count_with_netem():
    """With a netem readback present, the emitted value must equal overlay-only —
    the readback addend is suppressed, so a real netem never double-counts."""
    ctrl = C.Controller()
    site = ctrl.tunnels[0].site
    t0 = QUIET + 300.0
    peak_now = t0 + 50.0 + 0.3 * 60.0

    ctrl.set_overlay(site, "congestion", lead_s=50.0, duration=60.0, t_start=t0)
    with_netem = _sweep(ctrl, site, t0, peak_now, netem=(40.0, 5.0))

    ctrl.clear_overlay(site)
    ctrl.set_overlay(site, "congestion", lead_s=50.0, duration=60.0, t_start=t0)
    no_netem = _sweep(ctrl, site, t0, peak_now, netem=(0.0, 0.0))

    # Suppressed: a 40ms netem must not shift the emitted latency by ~40ms.
    assert abs(with_netem - no_netem) < 5.0, \
        f"netem double-counted: with={with_netem:.1f} without={no_netem:.1f}"
    print(f"no-double-count: with_netem={with_netem:.1f} "
          f"without={no_netem:.1f} OK")


def test_gauge_and_gc():
    ctrl = C.Controller()
    site = ctrl.tunnels[0].site
    t0 = QUIET
    ctrl.set_overlay(site, "congestion", lead_s=50.0, duration=60.0, t_start=t0)
    assert f'sdwan_overlay_active{{site="{site}",fault_type="congestion"}} 1' \
        in ctrl.render_prometheus(), "gauge not emitted while active"
    # Past t_end the per-tick GC prunes it.
    ctrl.tick(now=t0 + 50.0 + 60.0 + 1.0)
    assert site not in ctrl._overlay, "expired overlay not pruned by GC"
    assert "sdwan_overlay_active{" not in ctrl.render_prometheus(), \
        "gauge series still emitted after prune"
    print("gauge+gc OK")


def _serve():
    ctrl = C.Controller()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), C._handler_factory(ctrl))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return ctrl, srv, f"http://127.0.0.1:{srv.server_address[1]}"


def _post(url, obj):
    try:
        r = R.urlopen(url, data=json.dumps(obj).encode(), timeout=3)
        return r.status, json.load(r)
    except R.HTTPError as e:
        return e.code, None


def test_handler_roundtrip_and_validation():
    ctrl, srv, U = _serve()
    site = ctrl.tunnels[0].site
    try:
        st, body = _post(f"{U}/fault/overlay",
                         {"site": site, "fault_type": "congestion"})
        assert st == 200 and site in ctrl._overlay, f"good POST failed: {st}"
        # lead defaulted from the signature, not a flat constant.
        assert ctrl._overlay[site]["t_impact"] == \
            ctrl._overlay[site]["t_start"] + ctrl._sigs["congestion"]["lead_s"], \
            "lead_s not defaulted from the calibrated signature"
        reg = json.load(R.urlopen(f"{U}/fault/overlay", timeout=3))
        assert site in reg, "GET registry missing the active site"

        bad = [
            {"site": "no_such_site", "fault_type": "congestion"},   # unknown site
            {"site": site, "fault_type": "nope"},                   # unknown fault
            {"site": site, "fault_type": "node_failure"},           # not tunnel_ramp
            {"site": site, "fault_type": "congestion", "lead_s": -1},   # neg lead
            {"site": site, "fault_type": "congestion", "duration": 1},  # too short
            {"site": site, "fault_type": "congestion", "severity": "x"},  # bad sev
            {"fault_type": "congestion"},                           # missing site
        ]
        for b in bad:
            st, _ = _post(f"{U}/fault/overlay", b)
            assert st == 400, f"expected 400 for {b}, got {st}"

        st, _ = _post(f"{U}/fault/overlay/clear", {"site": site})
        assert st == 200 and site not in ctrl._overlay, "clear failed"
        print("handler roundtrip + validation OK")
    finally:
        srv.shutdown()


if __name__ == "__main__":
    test_ramp_absolute_and_relative_peaks()
    test_no_double_count_with_netem()
    test_gauge_and_gc()
    test_handler_roundtrip_and_validation()
    print("test_overlay: ALL OK")
