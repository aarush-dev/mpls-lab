"""copilot.forensic -- Lane-Runtime (R5). Forensic trigger + case creation (ADR-0014).

R5a: the trigger loop -- poll the Event Ledger, fire on `decision.alert == true`, one case per
episode (dedup), restart-safe cursor. `poll_once` is one tick, `run_forensic` the sleep-loop
driver, `Cursor` the persisted last-fired position.

R5b: the case creation the trigger fires (`make_handler` binds the deps into the
`handle(record, window)` seam). Freeze the window to cases/<id>/window/, write prediction.json,
run the initial investigation against a `ReplayAdapter` over that frozen disk (a second F2
ToolAdapter, no live backend), write case.md (ADR-0014/0010).
"""
from copilot.forensic.case import (
    ReplayAdapter, case_id, create_case, make_handler, render_case_md, snapshot_window,
)
from copilot.forensic.trigger import Cursor, poll_once, run_forensic

__all__ = [
    "Cursor", "poll_once", "run_forensic",
    "ReplayAdapter", "case_id", "create_case", "make_handler", "render_case_md", "snapshot_window",
]
