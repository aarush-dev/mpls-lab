"""copilot.forensic -- Lane-Runtime (R5). Forensic trigger + case creation (ADR-0014).

R5a: the trigger loop -- poll the Event Ledger, fire on `decision.alert == true`, one case per
episode (dedup), restart-safe cursor. `poll_once` is one tick, `run_forensic` the sleep-loop
driver, `Cursor` the persisted last-fired position.

R5b: the case creation the trigger fires (`make_handler` binds the deps into the
`handle(record, window)` seam). Freeze the window to cases/<id>/window/, write prediction.json,
run the initial investigation against a `ReplayAdapter` over that frozen disk (a second F2
ToolAdapter, no live backend), write case.md (ADR-0014/0010). The creation run is persisted as
the first chat (`INITIAL_CHAT`).

R6a: multi-chat per case + follow-ups (`follow_up`) bound to the FROZEN window -- n independent
chats coexist under one case (each resuming only its own history), all reading the frozen
snapshot via the ReplayAdapter (disk only; the freeze guard rejects reads past T_snapshot).
`resolve_case_dir` maps an untrusted case id to its dir; the SessionStore single-writer lock
serialises concurrent writes to one chat (ADR-0009).

R6b: concurrent-fault fan-out (`synthesize_concurrent`) -- `n_concurrent > 1` spawns one
investigation chat per fault + a `master_synthesis` chat that merges their findings, inheriting the
sub-chats' cites (attributed per sub-chat) so it passes the gate as first-class `prior_cites`
(gate.run_gate); single-fault cases never reach it (ADR-0014/0008).
"""
from copilot.forensic.case import (
    ReplayAdapter, case_id, create_case, make_handler, render_case_md, snapshot_window,
)
from copilot.forensic.chat import (
    INITIAL_CHAT, case_chats, follow_up, frozen_window, resolve_case_dir,
)
from copilot.forensic.synthesis import MASTER_CHAT, master_synthesis, synthesize_concurrent
from copilot.forensic.trigger import Cursor, poll_once, run_forensic

__all__ = [
    "Cursor", "poll_once", "run_forensic",
    "ReplayAdapter", "case_id", "create_case", "make_handler", "render_case_md", "snapshot_window",
    "INITIAL_CHAT", "case_chats", "follow_up", "frozen_window", "resolve_case_dir",
    "MASTER_CHAT", "master_synthesis", "synthesize_concurrent",
]
