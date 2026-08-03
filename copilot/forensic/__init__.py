"""copilot.forensic -- Lane-Runtime (R5). Forensic trigger + case creation (ADR-0014).

R5a: the trigger loop -- poll the Event Ledger, fire on `decision.alert == true`, one case per
episode (dedup), restart-safe cursor. `poll_once` is one tick, `run_forensic` the sleep-loop
driver, `Cursor` the persisted last-fired position. The `handle(record, window)` seam it
fires is filled by R5b case creation (#23).
"""
from copilot.forensic.trigger import Cursor, poll_once, run_forensic

__all__ = ["Cursor", "poll_once", "run_forensic"]
