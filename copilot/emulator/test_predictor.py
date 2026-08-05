"""Assert-based tests for the periodic predictor firing loop (R4b, ADR-0014).

Prior art: dataapi/check_dataset.py (assert + __main__, no framework).
Seams under test:
  predict_once(cfg, labels, ledger, now, drift_tick) -> one tick: predict on the window, persist
  run_predictor(cfg, base_url, ledger, ...) -> the periodic driver (re-fetch, tick, persist)
  the gate-stress demonstration: heavy blocks more than oracle over one scenario set (acceptance #3)
Run:  python3 -m copilot.emulator.test_predictor
"""
import dataclasses
import os
import tempfile

from copilot.config import Config
from copilot.emulator import emulate_record, is_abstain
from copilot.emulator.predictor import predict_once, run_predictor
from copilot.memory import Ledger
from copilot.adapter import StubAdapter
from copilot.agent import investigate
from copilot.llm import ScriptedLLM, final, tool_call
from copilot.window import WindowContext

# two non-overlapping ground-truth episodes: A then B, distinct devices (faults/labels row shape).
EP_A = {"scenario_id": "congestion-a", "type": "congestion", "device": "d_a", "severity": "high",
        "target": {"device": "d_a"}, "t_start": "2026-06-21T14:00:00Z",
        "t_impact": "2026-06-21T14:01:00Z", "t_end": "2026-06-21T14:02:00Z", "lead_time": 60.0,
        "probe": "latency_ms", "baseline_value": 10.0, "impact_value": 40.0, "signature": "sig-a"}
EP_B = {**EP_A, "scenario_id": "bgp_flap-b", "type": "bgp_flap", "device": "d_b",
        "t_start": "2026-06-21T14:10:00Z", "t_impact": "2026-06-21T14:11:00Z",
        "t_end": "2026-06-21T14:12:00Z", "signature": "sig-b"}


def _cfg(**kw):
    return dataclasses.replace(Config(), **kw)


def _seq(items):
    """A callable that yields `items` one per call (a scripted now_fn / stop clock)."""
    it = iter(items)
    return lambda: next(it)


def test_predict_once_persists_an_active_fault_and_skips_a_quiet_window():
    with tempfile.TemporaryDirectory() as d:
        led = Ledger(os.path.join(d, "l.db"))
        cfg = _cfg(emulate_pa=True, error_profile="oracle")
        rec = predict_once(cfg, [EP_A], led, "2026-06-21T14:01:00Z")     # inside A
        assert rec is not None and led.by_device("d_a")                  # persisted
        assert predict_once(cfg, [EP_A], led, "2020-01-01T00:00:00Z") is None  # quiet -> nothing
        assert len(led.by_time("0", "9")) == 1                           # only the active tick wrote


def test_run_predictor_walks_the_timeline_dedups_and_evolves_drift():
    # ADR-0014: fetch the timeline once, tick through it, persist. One row PER EPISODE (idempotent
    # by alert_id across the 3 ticks A stays active -- a re-emit is a no-op, so a single episode's
    # ledger row is frozen at its first tick). As the RUN advances, later episodes carry a higher
    # drift rung: A persisted at tick0 < B persisted at tick3 -- the scalar evolves over the run.
    nows = ["2026-06-21T14:01:00Z"] * 3 + ["2026-06-21T14:11:00Z"] * 3    # A x3 then B x3
    sleeps = []
    cfg = _cfg(emulate_pa=True, error_profile="heavy", predict_interval_s=10)
    with tempfile.TemporaryDirectory() as d:
        led = Ledger(os.path.join(d, "l.db"))
        ticks = run_predictor(cfg, "http://x", led,
                              now_fn=_seq(nows), stop_fn=_seq([False] * 6 + [True]),
                              sleep=lambda s: sleeps.append(s), fetch=lambda u: [EP_A, EP_B])
        rows = led.by_time("0", "9")
    assert ticks == 6
    assert len(rows) == 2                                        # A + B once each (dedup within A)
    assert sleeps == [10] * 6                                    # reads cfg.predict_interval_s
    ranks = [int(r["record"]["health"]["drift_state"][1:]) for r in rows]
    assert ranks[0] < ranks[1], f"drift must climb across the run: {ranks}"   # A@tick0 < B@tick3


def test_run_predictor_refetches_labels_each_tick_so_a_late_fault_is_seen():
    # #55 staleness fix: the timeline is re-read EVERY tick, not once at boot. A fault injected
    # after the daemon started must still produce a record within one tick. Fetch-once (the bug)
    # sees the boot snapshot [] forever -> nothing ever fires.
    calls = {"n": 0}
    def fetch(_url):
        calls["n"] += 1
        return [] if calls["n"] == 1 else [EP_A]        # empty at boot; EP_A appears on tick 2
    nows = ["2026-06-21T14:01:00Z"] * 3                  # all inside EP_A's active window
    cfg = _cfg(emulate_pa=True, error_profile="oracle", predict_interval_s=10)
    with tempfile.TemporaryDirectory() as d:
        led = Ledger(os.path.join(d, "l.db"))
        run_predictor(cfg, "http://x", led, now_fn=_seq(nows),
                      stop_fn=_seq([False] * 3 + [True]), sleep=lambda s: None, fetch=fetch)
        rows = led.by_time("0", "9")
    assert calls["n"] == 3, f"labels must be re-fetched every tick, not once: {calls['n']}"
    assert len(rows) == 1, "a fault injected after boot still fires (dedup across tick2,3 -> 1 row)"


def test_run_predictor_survives_a_transient_labels_fetch_failure():
    # #55: a per-tick fetch that raises (transient dataapi outage) must NOT kill the daemon -- the
    # tick is skipped, the loop lives on, and a later good tick still fires.
    calls = {"n": 0}
    def fetch(_url):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("dataapi 503")           # transient outage on tick 1
        return [EP_A]
    nows = ["2026-06-21T14:01:00Z"] * 2
    cfg = _cfg(emulate_pa=True, error_profile="oracle", predict_interval_s=10)
    with tempfile.TemporaryDirectory() as d:
        led = Ledger(os.path.join(d, "l.db"))
        ticks = run_predictor(cfg, "http://x", led, now_fn=_seq(nows),
                              stop_fn=_seq([False] * 2 + [True]), sleep=lambda s: None, fetch=fetch)
        rows = led.by_time("0", "9")
    assert ticks == 2, "the outage tick is counted and the loop keeps going"
    assert len(rows) == 1, "the good tick after the outage still fires"


# ---- the emulator's error_profile changes the REAL gate outcome (acceptance #3, honest) --------
# HONEST SCOPE (spec-review finding, R4b): the ONLY deterministic lever a Prediction Record pulls
# on `run_gate` is `abstain` (ADR-0008 §Nuances) -- and abstain RELIEVES the gate, it does not
# stress it. A confusable cause (ADR-0012) mis-steers *skill selection*, which stresses the gate
# only through an LLM actually picking a worse skill -- NOT unit-deterministic (flagged on #21;
# it surfaces in the real-loop eval, E1/#42). So the honest, wired demonstration here is the abstain
# lever: a heavy low-severity record abstains and softens thin evidence through the REAL investigate
# loop, where the same record under oracle blocks. Same loop, same evidence, profile drives the gate.
_WIN = WindowContext(100, 200)
_THIN = [{"device": "r1", "ts": 150, "cpu": 95}]                 # one row < gate_min_evidence(2)
_LOW = {**EP_A, "type": "congestion", "severity": "low", "device": "r1"}  # low sev -> heavy abstains


def _investigate_thin(abstain: bool):
    llm = ScriptedLLM([tool_call("query_metrics", {"device": "r1"}, id="c1"),
                       final("r1 cpu pegged [metrics:0]"),
                       final('{"pass": true}')])               # self-judge (reached only if soft)
    return investigate("why is r1 slow?", _WIN, llm=llm, adapter=StubAdapter(metrics_rows=_THIN),
                       cfg=_cfg(gate_max_retries=0), abstain=abstain)


def test_emulator_profile_drives_the_real_gate_via_abstain():
    heavy = emulate_record(_LOW, error_profile="heavy")         # abstains (thin confidence)
    oracle = emulate_record(_LOW, error_profile="oracle")       # never abstains
    assert is_abstain(heavy) is True and is_abstain(oracle) is False
    # same loop + same thin evidence: oracle BLOCKS on sufficiency, heavy's abstain SOFTENS -> passes.
    assert _investigate_thin(is_abstain(oracle)).answer.startswith("cannot answer yet")
    assert _investigate_thin(is_abstain(heavy)).answer == "r1 cpu pegged [metrics:0]"


def _run():
    test_predict_once_persists_an_active_fault_and_skips_a_quiet_window()
    test_run_predictor_walks_the_timeline_dedups_and_evolves_drift()
    test_run_predictor_refetches_labels_each_tick_so_a_late_fault_is_seen()
    test_run_predictor_survives_a_transient_labels_fetch_failure()
    test_emulator_profile_drives_the_real_gate_via_abstain()
    print("copilot.emulator.test_predictor OK")


if __name__ == "__main__":
    _run()
