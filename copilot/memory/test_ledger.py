"""Assert-based self-check for the Event Ledger (R2b, ADR-0009).

Prior art: dataapi/check_dataset.py (assert + __main__, no framework).
Seam under test: Ledger.append/by_device/by_time over a SQLite file -- append-only +
idempotent-by-record-id writes, query by device / by time range, resume across restart.
The "gate outcomes land for a real investigation (pass and fail)" acceptance is exercised
end-to-end at the /chat seam in copilot.api.test_api.
Run:  python3 -m copilot.memory.test_ledger
"""
import os
import shutil
import tempfile

from copilot.agent import Event, event_wire
from copilot.memory import Ledger


def _wire(type_, ts, **data):
    return event_wire(Event(type_, data, ts=ts))


def test_append_query_by_device():
    root = tempfile.mkdtemp()
    try:
        led = Ledger(os.path.join(root, "ledger.db"))
        led.append("p1", _wire("artifact", "2026-08-03T10:00:00+00:00", verdict="r1 cpu"), device="r1")
        led.append("p2", _wire("artifact", "2026-08-03T11:00:00+00:00", verdict="r2 bgp"), device="r2")
        got = led.by_device("r1")
        assert [w["verdict"] for w in got] == ["r1 cpu"], "only r1's records, full wire back"
        assert led.by_device("nope") == []
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_reappending_same_id_is_a_noop():
    root = tempfile.mkdtemp()
    try:
        led = Ledger(os.path.join(root, "ledger.db"))
        w = _wire("gate", "2026-08-03T10:00:00+00:00", ok=True, missing=[], retry=0)
        led.append("g1", w, device="r1")
        led.append("g1", w, device="r1")                      # same id -> no-op (idempotent)
        led.append("g1", _wire("gate", "2026-08-03T12:00:00+00:00", ok=False, missing=["x"], retry=1),
                   device="r1")                               # different payload, SAME id -> still ignored
        rows = led.by_device("r1")
        assert len(rows) == 1, "append-only + idempotent: one row per record id, never overwritten"
        assert rows[0]["ok"] is True, "the first write stands (immutable), not the re-append"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_query_by_time_range_inclusive():
    root = tempfile.mkdtemp()
    try:
        led = Ledger(os.path.join(root, "ledger.db"))
        for h in (9, 10, 11, 12):
            led.append(f"e{h}", _wire("gate", f"2026-08-03T{h:02d}:00:00+00:00", ok=True, missing=[], retry=0))
        got = led.by_time("2026-08-03T10:00:00+00:00", "2026-08-03T11:00:00+00:00")
        assert [w["ts"] for w in got] == \
            ["2026-08-03T10:00:00+00:00", "2026-08-03T11:00:00+00:00"], "inclusive range, in ts order"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_survives_process_restart():
    # a fresh Ledger instance (== a new process) on the same file recovers the timeline.
    root = tempfile.mkdtemp()
    try:
        path = os.path.join(root, "ledger.db")
        Ledger(path).append("g1", _wire("gate", "2026-08-03T10:00:00+00:00", ok=True, missing=[], retry=0),
                             device="r1")
        reopened = Ledger(path)                               # simulates a restart
        assert [w["ok"] for w in reopened.by_device("r1")] == [True]
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _run():
    test_append_query_by_device()
    test_reappending_same_id_is_a_noop()
    test_query_by_time_range_inclusive()
    test_survives_process_restart()
    print("copilot.memory ledger self-check OK")


if __name__ == "__main__":
    _run()
