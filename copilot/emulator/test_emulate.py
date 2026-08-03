"""Assert-based tests / self-check for the PA-emulator core (R4a, ADR-0003).

Prior art: dataapi/check_dataset.py (assert + __main__, no framework).
Seams under test:
  emulate_record(label, ...) -> a full §3.3 Prediction Record derived from ground truth
  prediction(cfg, labels, ...) -> emulate_pa routes emulator vs real PA, no caller change
  persist(ledger, record) -> the record lands in the Event Ledger (acceptance #1)
  fault_type / is_abstain -> the two consumer accessors (gate + skill selection hooks)
Run:  python3 -m copilot.emulator.test_emulate
"""
import dataclasses
import os
import tempfile

from copilot.config import Config
from copilot.emulator import (
    drift_state, emulate_record, fault_type, family, fetch_labels, is_abstain, persist,
    prediction, to_wire,
)
from copilot.memory import Ledger

# a ground-truth label row (faults/labels/labels.jsonl shape).
LABEL = {
    "scenario_id": "congestion-ce_branch1-87844aed", "type": "congestion",
    "target": {"device": "ce_branch1", "interface": "eth1"}, "severity": "high",
    "t_start": "2026-06-21T14:55:14Z", "t_impact": "2026-06-21T14:56:02Z",
    "t_end": "2026-06-21T14:56:14Z", "lead_time": 48.5, "probe": "sdwan_tunnel_latency_ms",
    "baseline_value": 24.79, "impact_value": 38.15,
    "signature": "latency+jitter creep then loss", "device": "ce_branch1",
}


def _cfg(**kw):
    return dataclasses.replace(Config(), **kw)


def test_emulate_record_is_a_full_section_3_3_record():
    r = emulate_record(LABEL, error_profile="oracle")
    # every §3.3 block present (ADR-0003 full fidelity), derived from the label.
    for block in ("risk", "forecast", "localization", "anomaly", "decision"):
        assert block in r, f"missing §3.3 block {block!r}"
    assert r["entity_id"] == "ce_branch1" and r["device"] == "ce_branch1"
    assert r["window_end_ts"] == LABEL["t_impact"]        # defaults to occurrence time
    ft = r["risk"]["fault_types"][0]
    assert ft["cause"] == "congestion" and ft["family"] == "congestion"
    assert ft["time_to_impact"]["median_s"] == 48.5       # oracle = exact lead_time, no jitter
    # the two resolved fields (docs conflicts a + b)
    assert r["health"]["drift_state"] == "R0"             # (a) model-health folded in, healthy
    assert r["n_concurrent"] == 1                         # (b) invented field, >=1


def test_oracle_reproduces_ground_truth_exactly():
    # R4b acceptance #1: oracle = perfect readout -- cause is the true type, no confusion, no
    # jitter, no abstain, drift healthy, regardless of drift_tick.
    r = emulate_record(LABEL, error_profile="oracle", drift_tick=99)
    assert fault_type(r) == "congestion"                  # true cause, never confused
    assert r["risk"]["fault_types"][0]["time_to_impact"]["median_s"] == 48.5  # exact lead_time
    assert is_abstain(r) is False and r["health"]["drift_state"] == "R0"


def test_drift_state_evolves_over_a_run():
    # R4b acceptance #4: the faked drift/health scalar climbs the R0-R5 ladder as a run advances
    # (drift_tick = the predictor tick). oracle is exempt (always healthy).
    states = [emulate_record(LABEL, error_profile="heavy", drift_tick=t)["health"]["drift_state"]
              for t in range(0, 18, 3)]
    ranks = [int(s[1:]) for s in states]
    assert ranks == sorted(ranks) and ranks[0] < ranks[-1], f"drift must climb: {states}"
    novs = [emulate_record(LABEL, error_profile="heavy", drift_tick=t)["health"]["codebook_novelty"]
            for t in range(0, 18, 3)]
    assert novs == sorted(novs) and novs[0] < novs[-1], f"novelty must rise: {novs}"
    assert {emulate_record(LABEL, error_profile="oracle", drift_tick=t)["health"]["drift_state"]
            for t in range(0, 18, 3)} == {"R0"}            # oracle never drifts


# a scenario set spanning every family (each family has >=2 members -> confusion is possible).
_SCEN = [
    {**LABEL, "scenario_id": f"s{i}", "type": t, "severity": sev, "device": f"d{i}"}
    for i, (t, sev) in enumerate([
        ("congestion", "high"), ("core_congestion", "medium"), ("bgp_flap", "high"),
        ("ospf_area_flap", "low"), ("ldp_session_flap", "medium"), ("tunnel_degrade", "high"),
        ("gray_failure", "low"), ("node_failure", "critical"), ("pop_isolation", "medium"),
        ("srlg_cut", "high"), ("asymmetric_loss", "low"), ("policy_drift", "medium"),
    ])
]


def test_light_and_heavy_confuse_the_cause_measurably():
    # R4b acceptance #2: light/heavy sometimes report a confusable cause (a same-family sibling --
    # right family, wrong specific type: the realism that mis-steers skill selection, ADR-0012).
    # Swept over 60 distinct scenario ids so the *rate* is measurable (occasional != never).
    sweep = [{**LABEL, "scenario_id": f"bgp_flap-{i}", "type": "bgp_flap"} for i in range(60)]
    def confused(profile):
        return sum(fault_type(emulate_record(s, error_profile=profile)) != "bgp_flap"
                   for s in sweep)
    assert confused("oracle") == 0                                 # oracle never confuses
    assert 0 < confused("light") < confused("heavy")               # heavy confuses more -- measurably
    # a confused cause always stays IN-family (confusable, not random) -- swept + spot set.
    for s in sweep + _SCEN:
        r = emulate_record(s, error_profile="heavy")
        assert family(fault_type(r)) == family(s["type"])


def test_tti_jitter_magnitude_grows_with_the_profile():
    # R4b acceptance #2: TTI jitter is injected *measurably* -- oracle exact, heavy noisier than
    # light. Spread = mean |reported_tti - true_lead| over the scenario set (each keyed distinctly).
    def spread(profile):
        tot = 0.0
        for s in _SCEN:
            tti = emulate_record(s, error_profile=profile)["risk"]["fault_types"][0][
                "time_to_impact"]["median_s"]
            tot += abs(tti - float(s.get("lead_time") or LABEL["lead_time"]))
        return tot / len(_SCEN)
    assert spread("oracle") == 0.0                                 # exact readout, no jitter
    assert 0 < spread("light") < spread("heavy")                   # heavy noisier -- measurably


def test_cumulative_incidence_is_monotone():
    # §3.3: F_k(h) non-decreasing by construction -- a readout can't dip.
    ps = [c["p"] for c in emulate_record(LABEL, error_profile="oracle")
          ["risk"]["fault_types"][0]["cumulative_incidence"]]
    assert ps == sorted(ps), f"cumulative incidence not monotone: {ps}"


def test_oracle_is_deterministic():
    # ADR-0017 deterministic eval: same label + oracle -> byte-identical record.
    assert emulate_record(LABEL, error_profile="oracle") == \
        emulate_record(LABEL, error_profile="oracle")


def test_family_maps_the_five_coarse_families():
    assert family("bgp_flap") == "routing-instability"
    assert family("tunnel_degrade") == "tunnel-degradation"
    assert family("node_failure") == "device-fault"
    assert family("srlg_cut") == "link-fault"
    assert family("never_seen") == "device-fault"         # unmapped -> safe default


def test_error_profile_light_can_abstain_on_low_severity():
    # ADR-0003: light abstains on a genuinely ambiguous low-severity call; oracle never does.
    low = {**LABEL, "severity": "low"}
    assert is_abstain(emulate_record(low, error_profile="light")) is True
    assert is_abstain(emulate_record(low, error_profile="oracle")) is False


def test_fault_type_and_is_abstain_accessors():
    assert fault_type(emulate_record(LABEL, error_profile="oracle")) == "congestion"
    assert fault_type(None) is None
    assert is_abstain(None) is False


def test_drift_state_accessor_reads_the_resolved_location():
    # T1: the trust gate reads health.drift_state (ADR-0003 won §3.5; #20). oracle = R0 (healthy),
    # heavy starts high on the R0-R5 ladder. None record -> None (no signal).
    assert drift_state(emulate_record(LABEL, error_profile="oracle")) == "R0"
    assert drift_state(emulate_record(LABEL, error_profile="heavy")) in {"R3", "R4", "R5"}
    assert drift_state(None) is None


def test_prediction_seam_emulate_pa_true_returns_a_record():
    r = prediction(_cfg(emulate_pa=True, error_profile="oracle"), [LABEL],
                   now="2026-06-21T14:55:30Z")
    assert r is not None and r["risk"]["fault_types"][0]["cause"] == "congestion"
    # no label active at that time -> no prediction (the seam returns None, not a fake).
    assert prediction(_cfg(emulate_pa=True), [LABEL], now="2020-01-01T00:00:00Z") is None


def test_prediction_seam_counts_concurrent_faults():
    other = {**LABEL, "scenario_id": "bgp_flap-pe1-x", "type": "bgp_flap", "device": "pe1"}
    r = prediction(_cfg(emulate_pa=True, error_profile="oracle"), [LABEL, other],
                   now="2026-06-21T14:55:30Z")
    assert r["n_concurrent"] == 2                          # both active in the window
    # #49: the record ENUMERATES all n concurrently-active faults (device + cause), not just a count.
    cf = r["concurrent_faults"]
    assert [(f["device"], f["cause"]) for f in cf] == [("ce_branch1", "congestion"), ("pe1", "bgp_flap")]


def test_single_fault_record_self_enumerates():
    # a lone fault still carries a 1-entry concurrent_faults (its own device+cause) so the field is
    # always present and consumers need no special-case for n==1.
    r = emulate_record(LABEL, error_profile="oracle")
    assert r["concurrent_faults"] == [{"device": "ce_branch1", "cause": "congestion"}]


def test_prediction_seam_emulate_pa_false_takes_the_real_pa_path():
    # the flag routes with NO caller change: false -> the real-PA callable, not the emulator.
    marker, seen = {"source": "real-pa"}, {}
    def fake_pa(now):
        seen["now"] = now                                 # the window_end_ts must cross the seam
        return marker
    assert prediction(_cfg(emulate_pa=False), [LABEL], now="2026-06-21T14:55:30Z",
                      real_pa=fake_pa) is marker
    assert seen["now"] == "2026-06-21T14:55:30Z"
    # default real-PA path (no PA built yet) surfaces a per-call error, not a silent None.
    try:
        prediction(_cfg(emulate_pa=False), [LABEL], now="2026-06-21T14:55:30Z")
    except RuntimeError:
        pass
    else:
        raise AssertionError("emulate_pa=false with no real PA must raise")


def test_fetch_labels_reads_the_ground_truth_rows():
    # acceptance #1: the /labels ground truth is reachable (injected transport, no live dataapi) --
    # so produce->persist runs off real rows, not only a literal. R4b loops this.
    captured = {}
    def fake_fetch(url):
        captured["url"] = url
        return [LABEL]
    rows = fetch_labels("http://dataapi:8000/", fetch=fake_fetch)
    assert rows == [LABEL] and captured["url"] == "http://dataapi:8000/"
    # end-to-end off fetched ground truth: fetch -> seam -> record.
    r = prediction(_cfg(emulate_pa=True, error_profile="oracle"),
                   fetch_labels("x", fetch=fake_fetch), now="2026-06-21T14:55:30Z")
    assert r["risk"]["fault_types"][0]["cause"] == "congestion"


def test_record_persists_to_the_event_ledger():
    # acceptance #1: ground truth -> a complete §3.3 record IN THE LEDGER, idempotent by alert_id.
    r = emulate_record(LABEL, error_profile="oracle")
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "ledger.db")
        led = Ledger(path)
        persist(led, r)
        persist(led, r)                                   # re-emit of the same episode = no-op
        got = Ledger(path).by_device("ce_branch1")        # fresh handle: survives restart
    assert len(got) == 1, "append-only + idempotent by alert_id"
    assert got[0]["type"] == "prediction"
    assert got[0]["record"]["risk"]["fault_types"][0]["cause"] == "congestion"


def test_to_wire_does_not_collide_with_record_keys():
    w = to_wire(emulate_record(LABEL, error_profile="oracle"))
    assert w["type"] == "prediction" and "record" in w
    assert "type" not in w["record"] and "ts" not in w["record"]   # lossless round-trip


def _run():
    test_emulate_record_is_a_full_section_3_3_record()
    test_oracle_reproduces_ground_truth_exactly()
    test_drift_state_evolves_over_a_run()
    test_light_and_heavy_confuse_the_cause_measurably()
    test_tti_jitter_magnitude_grows_with_the_profile()
    test_cumulative_incidence_is_monotone()
    test_oracle_is_deterministic()
    test_family_maps_the_five_coarse_families()
    test_error_profile_light_can_abstain_on_low_severity()
    test_fault_type_and_is_abstain_accessors()
    test_drift_state_accessor_reads_the_resolved_location()
    test_prediction_seam_emulate_pa_true_returns_a_record()
    test_prediction_seam_counts_concurrent_faults()
    test_single_fault_record_self_enumerates()
    test_prediction_seam_emulate_pa_false_takes_the_real_pa_path()
    test_fetch_labels_reads_the_ground_truth_rows()
    test_record_persists_to_the_event_ledger()
    test_to_wire_does_not_collide_with_record_keys()
    print("copilot.emulator.test_emulate OK")


if __name__ == "__main__":
    _run()
