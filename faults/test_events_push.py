"""test_events_push.py -- the #65 seam: live FRR precursors into Loki.

Black-box on the behaviours a consumer sees: (1) the pushed events ARE the
fault's dataset signature (the impact-anchored event_type/template_id events.py
emits) and land at the buildup-start (before impact); (2) recovery (end-anchored)
events are dropped -- a precursor never says "up" before the fault fires; (3) the
line is the dataset row shape (params a JSON string); (4) the full emit_precursors
seam no-ops when events/pandas/Loki are unavailable. No lab, no running Loki --
the push and the events import are mocked at their seams.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "synthetic"))
import events  # dataset event builder -- the ground truth we must match
import events_push as ep


def test_signature_matches_dataset_impact_events():
    """Pushed template_ids == events._spec's IMPACT-anchored set; end-anchored
    (recovery) events are dropped; all stamped at buildup-start."""
    precursor_ts = 1_000_000.0
    built = ep.build_events(
        events, "bgp_flap", "iface_churn", "ce_branch1", "eth1", None, "high",
        "bgp_flap-ce_branch1-aa", "live", precursor_ts)
    spec = events._spec("bgp_flap", "iface_churn", False)
    want = {tid for tid, anchor, _ in spec if anchor == "impact"}
    dropped = {tid for tid, anchor, _ in spec if anchor != "impact"}
    got = {labels["template_id"] for _, labels, _ in built}
    assert got == want, f"signature drift: {got} != {want}"
    assert dropped and not (got & dropped), "recovery events must be dropped"
    assert all(ts >= precursor_ts for ts, _, _ in built), "event before buildup-start"
    # bgp_flap keeps NBR-DOWN/HOLD-EXPIRE/ROUTE-WITHDRAW, drops the NBR-UP recovery
    types = {l["event_type"] for _, l, _ in built}
    assert "bgp_session_down" in types and "bgp_session_up" not in types


def test_line_is_dataset_row_shape():
    """Line matches events.py row: params is a JSON *string*, plus device/entity/
    severity carried in the row (not just labels)."""
    built = ep.build_events(
        events, "bgp_flap", "iface_churn", "ce_branch1", "eth1", None, "high",
        "bgp_flap-ce_branch1-aa", "live", 1.0)
    for _, labels, line in built:
        row = json.loads(line)
        assert row["event_id"].startswith("ev-")
        assert isinstance(row["params"], str), "params must be a JSON string"
        json.loads(row["params"])  # raises if not valid JSON
        assert row["device"] == "ce_branch1" and row["entity"] == "eth1"
        assert row["severity"] == "high"
        assert labels["device"] == "ce_branch1" and labels["app"] == "frr"


def test_no_signature_faults_emit_nothing():
    """policy_drift has no discrete _spec signature -> zero events, no push."""
    built = ep.build_events(
        events, "policy_drift", "iface_churn", "ce_branch1", None, "vrf_CORP",
        None, "policy_drift-ce_branch1-bb", "live", 1.0)
    assert built == []


def test_push_is_graceful_noop_when_loki_down(monkeypatch):
    """A push against an unreachable Loki returns 0 and never raises."""
    def _boom(*a, **k):
        raise OSError("connection refused")
    monkeypatch.setattr(ep.urllib.request, "urlopen", _boom)
    n = ep.push_events([(1.0, {"job": "fault-events", "device": "d"}, "{}")])
    assert n == 0


def test_push_sends_ns_timestamps(monkeypatch):
    """Loki wants ns string timestamps; assert the payload the wrapper POSTs."""
    captured = {}

    def _capture(req, timeout=None):
        captured["body"] = json.loads(req.data.decode())
        return None
    monkeypatch.setattr(ep.urllib.request, "urlopen", _capture)
    n = ep.push_events([(1_000_000.5, {"job": "fault-events", "device": "d"}, "line")])
    assert n == 1
    stream = captured["body"]["streams"][0]
    ts_ns, line = stream["values"][0]
    assert ts_ns == str(int(1_000_000.5 * 1e9)) and line == "line"
    assert stream["stream"]["device"] == "d"


def test_emit_precursors_noop_when_events_unavailable(monkeypatch):
    """The full seam: no events module (no pandas) -> 0, no raise, no push."""
    monkeypatch.setattr(ep, "_events_mod", lambda: None)
    n = ep.emit_precursors("bgp_flap", "ce_branch1", "eth1", None, "high",
                           "sid", "live", 1.0)
    assert n == 0


def test_emit_precursors_unmapped_fault_is_zero(monkeypatch):
    """A fault_type absent from the signature table -> 0 (never pushes)."""
    pushed = []
    monkeypatch.setattr(ep, "push_events", lambda lst: pushed.append(lst) or len(lst))
    n = ep.emit_precursors("not_a_real_fault", "ce_branch1", "eth1", None, "high",
                           "sid", "live", 1.0)
    assert n == 0 and pushed == []


def test_emit_precursors_happy_path(monkeypatch):
    """kind resolved from signatures -> build -> push, count returned."""
    monkeypatch.setattr(ep, "push_events", lambda lst: len(lst))
    n = ep.emit_precursors("node_failure", "ce_branch2", "eth2", None, None,
                           "sid", "live", 1.0)
    # node_failure (iface_down) _spec has 4 events, but its 1 end-anchored
    # (KERN-LINK-UP) is dropped -> 3 impact-anchored precursors pushed
    # (KERN-LINK-DOWN, FRR-OSPF-ADJ-DOWN, SYS-PROC-RESTART).
    assert n == 3


if __name__ == "__main__":
    class _MP:  # tiny monkeypatch shim so `python3 test_events_push.py` runs sans pytest
        def __init__(self): self._undo = []
        def setattr(self, obj, name, val):
            self._undo.append((obj, name, getattr(obj, name)))
            setattr(obj, name, val)
        def undo(self):
            for o, n, v in reversed(self._undo): setattr(o, n, v)

    _plain = [test_signature_matches_dataset_impact_events,
              test_line_is_dataset_row_shape, test_no_signature_faults_emit_nothing]
    _mp = [test_push_is_graceful_noop_when_loki_down, test_push_sends_ns_timestamps,
           test_emit_precursors_noop_when_events_unavailable,
           test_emit_precursors_unmapped_fault_is_zero, test_emit_precursors_happy_path]
    for t in _plain:
        t()
    for t in _mp:
        mp = _MP()
        try:
            t(mp)
        finally:
            mp.undo()
    print("events_push selfcheck OK")
