"""copilot.forensic.trigger -- the Forensic trigger loop (R5a, ADR-0014).

ADR-0014: the PA/emulator runs as a periodic predictor writing Prediction Records to the Event
Ledger (that stream is R4b, copilot.emulator.predictor). This module is the OTHER half: a
sleep-loop that polls the ledger and, on a record with `decision.alert == true`, freezes the
window and hands it downstream to open a case. Mirrors the predictor's shape -- `poll_once` is one
tick (scan -> fire), `run_forensic` is the sleep-loop driver (cron floors ~60s, so a 10s cadence is
a sleep-loop, ADR-0014).

One case per episode (ADR-0014): the ledger is idempotent by alert_id, so a fault that alerts for
minutes lands ONE ledger row; the `Cursor` then guarantees the trigger fires for it exactly once,
ever -- including across a restart (it persists its last-fired position).

The firing itself -- copy the frozen window, write prediction.json, open case.md, spawn chats -- is
R5b (copilot.forensic case creation, #23); this ticket delivers the trigger + dedup + cursor and
the `handle(record, window)` seam it fires. R5b supplies the production `handle`.

Self-check:  python3 -m copilot.forensic.test_trigger
"""
import json
import os
import time
from datetime import datetime

from copilot.window import WindowContext

_HIGH = "9999-12-31T23:59:59Z"   # lexical upper bound for the open-ended ledger range query


def _epoch(ts: str) -> int:
    """ISO-8601 UTC stamp (…Z) -> epoch seconds, the int bound WindowContext takes (Py3.11
    fromisoformat parses the trailing Z)."""
    return int(datetime.fromisoformat(ts).timestamp())


class Cursor:
    """The trigger's restart-safe position: the ts of the last-fired record plus the alert_ids
    fired AT that exact ts (the tie set). Persisted as a small JSON file so a new process resumes
    where the last left off -- no double-fire, no missed alert (ADR-0014 restart safety).

    Diverges from ADR-0014's literal "last-processed record **id**" (flagged, not silently
    overridden, per the ticket rule): the Ledger API exposes no monotonic rowid or read-all, only
    `by_time` (ledger.py) -- so ts IS the cursor. `window_end_ts` is monotonic with persist order
    (run_predictor's `now_fn` is a UTC clock), so scanning `[cursor.ts, +inf)` never skips a later
    record. ponytail: cursor is (ts, tie-set), not the full fired-id history -- ts monotonicity does
    the deduping and only the boundary ts needs a tie set (bounded to records sharing one instant).
    Ceiling: a ledger read-by-id (rowid cursor) if out-of-order persist or same-second floods bite."""

    def __init__(self, path: str):
        self.path = path
        self.ts, self.fired = "", set()
        if os.path.exists(path):
            with open(path) as fh:
                d = json.load(fh)
            self.ts, self.fired = d["ts"], set(d["fired"])

    def seen(self, ts: str, alert_id: str) -> bool:
        """True if this record was already fired: strictly before the cursor, or at the cursor ts
        and in its tie set."""
        return ts < self.ts or (ts == self.ts and alert_id in self.fired)

    def advance(self, ts: str, alert_id: str) -> None:
        """Record that `alert_id` fired at `ts` and persist. A later ts resets the tie set; the
        same ts extends it."""
        if ts > self.ts:
            self.ts, self.fired = ts, {alert_id}
        else:
            self.fired.add(alert_id)
        with open(self.path, "w") as fh:
            json.dump({"ts": self.ts, "fired": sorted(self.fired)}, fh)


def _new_alerts(ledger, cursor: Cursor):
    """The not-yet-fired alerting Prediction Records in the ledger, ascending by ts. Scans from the
    cursor ts forward (inclusive) so the boundary tie set is re-examined, then filters."""
    for wire in ledger.by_time(cursor.ts, _HIGH):
        if wire.get("type") != "prediction":
            continue
        rec = wire.get("record") or {}
        if not (rec.get("decision") or {}).get("alert"):
            continue
        alert_id = (rec.get("explanation_ref") or {}).get("alert_id")
        if not cursor.seen(wire["ts"], alert_id):
            yield rec


def poll_once(cfg, ledger, cursor: Cursor, handle) -> int:
    """One trigger tick: fire `handle(record, window)` (window frozen) for each new alerting record and
    advance the cursor past it. `handle` is the seam R5b (#23) fills with case creation; here it is
    whatever the caller injects. Returns the number of records fired this tick."""
    n = 0
    for rec in list(_new_alerts(ledger, cursor)):
        win = WindowContext.forensic(_epoch(rec["window_end_ts"]), cfg)
        handle(rec, win)
        cursor.advance(rec["window_end_ts"], rec["explanation_ref"]["alert_id"])
        n += 1
    return n


def run_forensic(cfg, ledger, cursor: Cursor, handle, *, stop_fn, sleep=time.sleep) -> int:
    """The periodic Forensic trigger (ADR-0014): until `stop_fn()`, poll the ledger and fire on new
    alerts, then sleep `cfg.predict_interval_s`. `stop_fn`/`sleep` are injected so a test drives the
    loop deterministically; in production `stop_fn` is a shutdown flag, `sleep` is time.sleep.
    Returns the tick count. ponytail: a sleep-loop, not cron (ADR-0014); restart-safety is the
    persisted Cursor, not loop state, so a crash mid-run resumes cleanly."""
    tick = 0
    while not stop_fn():
        poll_once(cfg, ledger, cursor, handle)
        tick += 1
        sleep(cfg.predict_interval_s)
    return tick


def _main():  # pragma: no cover -- #55 process entrypoint, exercised by copilot-up.sh, not units
    """Run the forensic trigger as a headless daemon (copilot-up.sh / noc-copilot.service). Shares
    the ledger with the predictor + api; the Cursor persists the restart-safe position. The
    production `handle` is R5b's case creator, wired from the SAME dependency builders the api uses
    (copilot.api.app.get_*) so daemon and request path stay one config. SIGTERM flips the stop flag.
    Function-local imports: api.app -> forensic.chat -> here would cycle at module load."""
    import os
    import signal

    from copilot.api.app import (get_adapter, get_cases_root, get_kg, get_llm,
                                 get_retriever, get_skills)
    from copilot.config import load
    from copilot.forensic.case import make_handler
    from copilot.memory import Ledger

    cfg = load()
    ledger = Ledger(os.environ.get("COPILOT_LEDGER_PATH", "ledger.db"))
    cursor = Cursor(os.environ.get("COPILOT_CURSOR_PATH", "forensic-cursor.json"))
    handle = make_handler(live_adapter=get_adapter(cfg), llm=get_llm(cfg), cfg=cfg,
                          cases_root=get_cases_root(), retriever=get_retriever(cfg),
                          skills=get_skills(cfg), kg=get_kg(cfg))
    stop = {"v": False}
    signal.signal(signal.SIGTERM, lambda *_: stop.__setitem__("v", True))
    run_forensic(cfg, ledger, cursor, handle, stop_fn=lambda: stop["v"])


if __name__ == "__main__":
    _main()
