"""copilot.memory -- Lane-Runtime (R2a/R2b). Session Store + Event Ledger (ADR-0009).

R2a: `SessionStore` -- sessions/<id>/{events.jsonl, meta.json}, append + read-back, so a
conversation resumes across process restarts (the events.jsonl reuses F4's `event_wire`
schema exactly). R2b (#18): the append-only Event Ledger (SQLite, gate outcomes) lands beside it.
"""
from copilot.memory.session import SessionStore

__all__ = ["SessionStore"]
