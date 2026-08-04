"""Golden test: faults/signatures.py == the generator's pre-refactor closures.

Each _ref_* below is a verbatim copy of the inline closure signatures.py
replaced (calibrate._fault_signatures defaults / generate._prog / _tunnel_ramp
targets). If the shared module ever drifts from what the dataset was built with,
these asserts fail -- which is the whole point of one shared source.

Run: python3 faults/test_signatures.py   (or pytest)
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import signatures


# --- reference implementations (pre-refactor originals) ----------------------

def _ref_default_signatures(base_lat, base_loss, base_jit):
    return {
        "congestion":     {"lat_peak": 60.0, "loss_peak": 3.0, "jit_peak": 8.0, "lead_s": 50.0, "kind": "tunnel_ramp"},
        "bgp_flap":       {"lat_peak": base_lat, "loss_peak": 0.3, "jit_peak": base_jit, "lead_s": 2.0, "kind": "iface_churn"},
        "tunnel_degrade": {"lat_peak": 35.0, "loss_peak": 5.0, "jit_peak": 12.0, "lead_s": 40.0, "kind": "tunnel_ramp"},
        "policy_drift":   {"lat_peak": 28.0, "loss_peak": 0.35, "jit_peak": 3.0, "lead_s": 3.0, "kind": "iface_churn"},
        "node_failure":   {"lat_peak": base_lat, "loss_peak": 1.0, "jit_peak": base_jit, "lead_s": 1.0, "kind": "iface_down"},
        "asymmetric_loss": {"lat_peak": base_lat * 1.1, "loss_peak": 4.0, "jit_peak": base_jit * 1.5, "lead_s": 30.0, "kind": "tunnel_ramp"},
        "brownout":       {"lat_peak": 45.0, "loss_peak": 1.5, "jit_peak": 6.0, "lead_s": 55.0, "kind": "tunnel_ramp"},
        "p_node_failure":  {"lat_peak": base_lat * 1.3, "loss_peak": 2.0, "jit_peak": base_jit * 2.0, "lead_s": 5.0, "kind": "iface_down"},
        "pop_isolation":   {"lat_peak": base_lat * 1.6, "loss_peak": 6.0, "jit_peak": base_jit * 2.5, "lead_s": 3.0, "kind": "iface_down"},
        "core_partition":  {"lat_peak": base_lat * 1.8, "loss_peak": 8.0, "jit_peak": base_jit * 3.0, "lead_s": 3.0, "kind": "iface_down"},
        "srlg_cut":        {"lat_peak": base_lat * 1.4, "loss_peak": 4.0, "jit_peak": base_jit * 2.2, "lead_s": 2.0, "kind": "iface_down"},
        "core_congestion": {"lat_peak": 55.0, "loss_peak": 2.5, "jit_peak": 10.0, "lead_s": 45.0, "kind": "tunnel_ramp"},
        "ospf_area_flap":  {"lat_peak": base_lat * 1.2, "loss_peak": 1.0, "jit_peak": base_jit * 2.0, "lead_s": 4.0, "kind": "iface_churn"},
        "path_asymmetry":  {"lat_peak": 30.0, "loss_peak": 0.4, "jit_peak": 4.0, "lead_s": 20.0, "kind": "tunnel_ramp"},
        "rr_failure":      {"lat_peak": base_lat, "loss_peak": 1.2, "jit_peak": base_jit, "lead_s": 3.0, "kind": "iface_churn"},
        "gray_failure":    {"lat_peak": base_lat * 1.15, "loss_peak": 1.8, "jit_peak": base_jit * 1.3, "lead_s": 90.0, "kind": "tunnel_ramp"},
        "mpls_underlay_failure": {"lat_peak": base_lat * 1.3, "loss_peak": 2.0, "jit_peak": base_jit * 1.8, "lead_s": 4.0, "kind": "iface_down"},
        "ldp_session_flap":      {"lat_peak": base_lat * 1.1, "loss_peak": 0.8, "jit_peak": base_jit * 1.6, "lead_s": 3.0, "kind": "iface_churn"},
        "hub_spoke_congest":     {"lat_peak": 70.0, "loss_peak": 5.0, "jit_peak": 14.0, "lead_s": 40.0, "kind": "tunnel_ramp"},
        "bgp_cascade":           {"lat_peak": base_lat * 1.2, "loss_peak": 1.5, "jit_peak": base_jit * 2.0, "lead_s": 3.0, "kind": "iface_churn"},
        "controller_drift":      {"lat_peak": 38.0, "loss_peak": 3.0, "jit_peak": 5.0, "lead_s": 25.0, "kind": "tunnel_ramp"},
    }


def _ref_prog(ep, t_start, t_impact, t_end, dur, sevmul, step, p_cross=1.0):
    ep = np.asarray(ep, dtype=float)
    knots_t = [t_start, t_impact, t_impact + 0.3 * max(dur, step), t_end]
    knots_p = [0.0, p_cross, 1.0, 0.0]
    return np.clip(np.interp(ep, knots_t, knots_p), 0.0, 1.0) * sevmul


def _ref_tunnel_ramp(sig, lat, jit, loss, p_t):
    # verbatim original generate._tunnel_ramp body
    lat_t = np.maximum(sig["lat_peak"], lat * 1.15)
    jit_t = np.maximum(sig["jit_peak"], jit * 1.15)
    return (np.round(lat + p_t * (lat_t - lat), 4),
            np.round(jit + p_t * (jit_t - jit), 4),
            np.round(loss + p_t * sig["loss_peak"], 4))


# --- tests -------------------------------------------------------------------

def test_default_signatures():
    for base_lat, base_loss, base_jit in [
        (20.0, 0.05, 2.0), (33.0, 0.33, 2.5), (0.0, 0.0, 0.0), (100.0, 5.0, 9.9),
    ]:
        assert (signatures.default_signatures(base_lat, base_loss, base_jit)
                == _ref_default_signatures(base_lat, base_loss, base_jit))


def test_prog():
    grid = np.linspace(0, 400, 41)
    cases = [
        # t_start, t_impact, t_end, dur, sevmul, step, p_cross
        (100, 200, 300, 60, 1.0, 30, 1.0),
        (100, 150, 300, 5,  0.8, 30, 0.4),   # dur < step -> floor at step
        (0,   50,  400, 120, 0.5, 30, 0.7),
        (100, 200, 300, 60, 1.0, 30, 0.0),
    ]
    for t_start, t_impact, t_end, dur, sevmul, step, p_cross in cases:
        got = signatures.prog(grid, t_start, t_impact, t_end, dur, sevmul, step, p_cross)
        ref = _ref_prog(grid, t_start, t_impact, t_end, dur, sevmul, step, p_cross)
        assert np.array_equal(got, ref)
    # scalar epoch path
    assert signatures.prog(250, 100, 200, 300, 60, 1.0, 30) == \
        _ref_prog(250, 100, 200, 300, 60, 1.0, 30)


def test_tunnel_ramp_targets():
    sigs = signatures.default_signatures(33.0, 0.33, 2.5)
    lat = np.array([20.0, 40.0, 65.0, 5.0])
    jit = np.array([2.0, 3.5, 15.0, 0.7])
    loss = np.array([0.05, 0.5, 3.0, 0.0])
    for ft in ("congestion", "tunnel_degrade", "gray_failure", "bgp_flap"):
        sig = sigs[ft]
        # shared func floors lat/jit; loss stays the caller's additive bump.
        lt, jt = signatures.tunnel_ramp_targets(sig, lat, jit)
        for p_t in (0.0, 0.4, 1.0):
            got = (np.round(lat + p_t * (lt - lat), 4),
                   np.round(jit + p_t * (jt - jit), 4),
                   np.round(loss + p_t * sig["loss_peak"], 4))
            ref = _ref_tunnel_ramp(sig, lat, jit, loss, p_t)
            for g, r in zip(got, ref):
                assert np.array_equal(g, r)


if __name__ == "__main__":
    test_default_signatures()
    test_prog()
    test_tunnel_ramp_targets()
    print("ok: signatures == pre-refactor closures")
