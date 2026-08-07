"""PA-A6/G4 self-check: a canned snapshot response -> a §3.3 record that
round-trips through emulate.persist() (has alert_id + device) and that the copilot
consumers read without error.

Run:  python3 -m copilot.emulator.test_real_pa
"""
from copilot.config import load
from copilot.emulator import emulate
from copilot.emulator.real_pa import RealPA


def _rec(entity, cause, cal, alert):
    """A minimal real /v1/predict/snapshot record (as the model's predictor emits)."""
    return {"model_version": "reduced_graph_v2_test0.859", "entity_id": entity,
            "entity_type": "device", "vrf": "", "window_end_ts": "2026-08-06T11:00:00Z",
            "graph_fused": True, "snapshot_nodes": 3,
            "risk": {"fault_types": [{"cause": cause, "family": "routing_instability",
                                      "cause_probability": cal}],
                     "survival_curve": [], "p_no_impact_in_horizon": 1 - cal,
                     "p_any_fault_in_horizon": cal},
            "forecast": {"horizon_steps": 240, "quantile_spread_mean": 1.2},
            "anomaly": {"anomaly_score": 0.7, "vq_code": 42},
            "decision": {"available": True, "fused_probability": cal, "calibrated_probability": cal,
                         "temperature": 1.04, "threshold": 0.8, "alert": alert,
                         "conformal_set_valid_at_fpr": 0.01},
            "explanation_ref": {"explanation_status": "not_implemented"}}


def _fake_windows(*_a, **_k):
    # two entities on two devices; build_windows is bypassed via monkeypatch below
    return {"pe1": {"device": "pe1", "entity_type": "device", "vrf": "", "window": [], "etc": 2, "stc": 1},
            "pe2": {"device": "pe2", "entity_type": "device", "vrf": "", "window": [], "etc": 2, "stc": 1}}


def test_snapshot_to_record_roundtrips():
    cfg = load()
    # canned snapshot response: pe1 alerts (higher prob), pe2 alerts too -> n_concurrent=2
    canned = {"records": [_rec("pe1", "bgp_flap", 0.95, True),
                          _rec("pe2", "ospf_area_flap", 0.85, True),
                          _rec("p3", "gray_failure", 0.10, False)]}
    pa = RealPA(cfg, post=lambda path, body: canned, get=lambda path: {"channels": ["x"]})
    # bypass live window assembly
    import copilot.emulator.real_pa as rp
    rp.build_windows = lambda *a, **k: _fake_windows()

    rec = pa.predict("2026-08-06T11:00:00Z")
    assert rec is not None, "should return the primary alerting record"
    # primary = highest calibrated_probability
    assert rec["device"] == "pe1" and rec["risk"]["fault_types"][0]["cause"] == "bgp_flap"
    # §3.3 gaps filled
    assert rec["explanation_ref"]["alert_id"] == "alt_pe1__bgp_flap"
    assert rec["n_concurrent"] == 2, rec["n_concurrent"]
    assert {c["device"] for c in rec["concurrent_faults"]} == {"pe1", "pe2"}
    assert rec["decision"]["abstain"] is False
    assert rec["health"]["drift_state"] == "R0"
    assert rec["anomaly"]["vq_label"] == "vq_42"

    # consumers read it without error
    assert emulate.fault_type(rec) == "bgp_flap"
    assert emulate.is_abstain(rec) is False
    assert emulate.drift_state(rec) == "R0"

    # round-trips through the ledger wire form (persist keys on alert_id + device)
    wire = emulate.to_wire(rec)
    assert wire["record"]["explanation_ref"]["alert_id"] == "alt_pe1__bgp_flap"
    assert wire["ts"] == rec["window_end_ts"]
    print("OK real_pa: snapshot->record, gaps filled, consumers + persist round-trip")


def test_no_alert_returns_none():
    cfg = load()
    canned = {"records": [_rec("pe1", "bgp_flap", 0.10, False)]}
    pa = RealPA(cfg, post=lambda p, b: canned, get=lambda p: {"channels": ["x"]})
    import copilot.emulator.real_pa as rp
    rp.build_windows = lambda *a, **k: _fake_windows()
    assert pa.predict("2026-08-06T11:00:00Z") is None
    print("OK real_pa: no alert -> None")


if __name__ == "__main__":
    test_snapshot_to_record_roundtrips()
    test_no_alert_returns_none()
