"""Assert-based tests for the Forensic trigger loop (R5a, ADR-0014).

Prior art: dataapi/check_dataset.py (assert + __main__, no framework); mirrors the R4b
predictor tests (copilot.emulator.test_predictor) -- same Ledger + Config + WindowContext.
Seams under test:
  Cursor(path)                                -- restart-safe last-fired position (persisted)
  poll_once(cfg, ledger, cursor, handle) -> n -- fire handle(record, window) per NEW alert
  run_forensic(cfg, ledger, cursor, handle, *, stop_fn, sleep) -> ticks -- the sleep-loop driver
Run:  python3 -m copilot.forensic.test_trigger
"""
import dataclasses
import os
import tempfile

from copilot.config import Config
from copilot.emulator import emulate_record, persist
from copilot.memory import Ledger
from copilot.forensic.trigger import Cursor, poll_once, run_forensic

# two non-overlapping ground-truth episodes (faults/labels row shape); oracle high -> alert==true.
EP_A = {"scenario_id": "congestion-a", "type": "congestion", "device": "d_a", "severity": "high",
        "target": {"device": "d_a"}, "t_start": "2026-06-21T14:00:00Z",
        "t_impact": "2026-06-21T14:01:00Z", "t_end": "2026-06-21T14:02:00Z", "lead_time": 60.0,
        "probe": "latency_ms", "baseline_value": 10.0, "impact_value": 40.0, "signature": "sig-a"}
EP_B = {**EP_A, "scenario_id": "bgp_flap-b", "type": "bgp_flap", "device": "d_b",
        "t_impact": "2026-06-21T14:11:00Z", "signature": "sig-b"}
EP_LOW = {**EP_A, "scenario_id": "quiet-c", "severity": "low", "device": "d_c",
          "t_impact": "2026-06-21T14:21:00Z"}   # oracle low -> calibrated < tau -> alert==false


def _cfg(**kw):
    return dataclasses.replace(Config(), **kw)


def _rec(ep):
    return emulate_record(ep, error_profile="oracle", now=ep["t_impact"])


def _collector():
    got = []
    return got, (lambda rec, win: got.append((rec, win)))


def test_poll_fires_only_on_alert_and_hands_a_frozen_window():
    with tempfile.TemporaryDirectory() as d:
        led = Ledger(os.path.join(d, "l.db"))
        persist(led, _rec(EP_A))              # alert==true
        persist(led, _rec(EP_LOW))            # alert==false -> must NOT fire
        assert (_rec(EP_A)["decision"]["alert"], _rec(EP_LOW)["decision"]["alert"]) == (True, False)
        got, handle = _collector()
        n = poll_once(_cfg(), led, Cursor(os.path.join(d, "cur.json")), handle)
        assert n == 1 and len(got) == 1, "fires once, only on the alerting record"
        rec, win = got[0]
        assert rec["device"] == "d_a"
        assert win.frozen is True and win.t_snapshot is not None, "downstream gets a frozen window"


def test_one_episode_one_case_across_repeated_alerts():
    # ADR-0014 one-case-per-episode: the ledger is idempotent by alert_id, so re-persisting the
    # same episode's record is a no-op; the trigger fires exactly once for it, ever.
    with tempfile.TemporaryDirectory() as d:
        led = Ledger(os.path.join(d, "l.db"))
        cur = Cursor(os.path.join(d, "cur.json"))
        got, handle = _collector()
        for _ in range(3):                    # the episode alerts on 3 consecutive predictor ticks
            persist(led, _rec(EP_A))
            poll_once(_cfg(), led, cur, handle)
        assert len(got) == 1, "one episode -> exactly one case, no duplicates"


def test_restart_resumes_from_cursor_without_refiring():
    with tempfile.TemporaryDirectory() as d:
        path, curp = os.path.join(d, "l.db"), os.path.join(d, "cur.json")
        led = Ledger(path)
        persist(led, _rec(EP_A))
        got1, h1 = _collector()
        assert poll_once(_cfg(), led, Cursor(curp), h1) == 1     # first process handles A
        # a NEW process: fresh Cursor reloads the persisted position, A must not re-fire
        got2, h2 = _collector()
        persist(led, _rec(EP_B))                                 # B arrives after the restart
        n = poll_once(_cfg(), Ledger(path), Cursor(curp), h2)
        assert n == 1 and [r["device"] for r, _ in got2] == ["d_b"], "resumes past A, fires only B"


def _seq(items):
    it = iter(items)
    return lambda: next(it)


def test_run_forensic_polls_at_interval_until_stopped():
    with tempfile.TemporaryDirectory() as d:
        led = Ledger(os.path.join(d, "l.db"))
        persist(led, _rec(EP_A))
        got, handle = _collector()
        sleeps = []
        cfg = _cfg(predict_interval_s=10)
        ticks = run_forensic(cfg, led, Cursor(os.path.join(d, "cur.json")), handle,
                             stop_fn=_seq([False, False, True]), sleep=lambda s: sleeps.append(s))
        assert ticks == 2 and sleeps == [10, 10], "sleep-loop reads cfg.predict_interval_s"
        assert len(got) == 1, "A fires once across the polls"


def _run():
    test_poll_fires_only_on_alert_and_hands_a_frozen_window()
    test_one_episode_one_case_across_repeated_alerts()
    test_restart_resumes_from_cursor_without_refiring()
    test_run_forensic_polls_at_interval_until_stopped()
    print("copilot.forensic.test_trigger OK")


if __name__ == "__main__":
    _run()
