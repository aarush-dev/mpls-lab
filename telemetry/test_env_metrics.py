"""Self-check for the env sidecar's overlay->prog wiring (#63).

Prior art: envmodel.py __main__ asserts. The sidecar file has a hyphen, so it is
loaded via importlib rather than imported. Run (from telemetry/):
    python3 test_env_metrics.py
"""
import importlib.util
import os

_SPEC = importlib.util.spec_from_file_location(
    "env_metrics", os.path.join(os.path.dirname(os.path.abspath(__file__)), "env-metrics.py"))
m = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(m)


def test_overlay_prog_branches():
    ov = {"fault_type": "gray_failure", "t_start": 0.0, "t_impact": 100.0,
          "t_end": 200.0, "sevmul": 1.0}
    peak_t = 100.0 + 0.3 * (200.0 - 100.0)          # t_impact + 0.3*dur
    assert 0.99 <= m.overlay_prog(ov, peak_t) <= 1.0        # peaks at ~sevmul
    assert m.overlay_prog(ov, -5) == 0.0                    # before the window
    assert m.overlay_prog(ov, 300) == 0.0                   # past t_end
    assert m.overlay_prog({}, 50) == 0.0                    # malformed record -> 0
    # sevmul is honoured: a low fault (0.5) peaks at half a full one.
    lo = dict(ov, sevmul=0.5)
    assert abs(m.overlay_prog(lo, peak_t) - 0.5 * m.overlay_prog(ov, peak_t)) < 1e-6


def test_read_overlay_best_effort():
    # unreachable controller -> {} (never raises, telemetry must not block on it)
    m.CTRL_URL = "http://127.0.0.1:1"  # nothing listening
    assert m.read_overlay() == {}


def test_signatures_absent_is_inert():
    saved = m.signatures
    try:
        m.signatures = None                                  # stale/numpy-less image
        assert m.overlay_prog({"t_start": 0.0, "t_impact": 1.0, "t_end": 2.0}, 1.0) == 0.0
    finally:
        m.signatures = saved


def _run():
    test_overlay_prog_branches()
    test_read_overlay_best_effort()
    test_signatures_absent_is_inert()
    print("env-metrics overlay self-check OK")


if __name__ == "__main__":
    _run()
