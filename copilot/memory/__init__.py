"""copilot.memory -- Lane-Runtime (R2a/R2b). Session Store + Event Ledger (ADR-0009).

R2a: `SessionStore` -- sessions/<id>/{events.jsonl, meta.json}, append + read-back, so a
conversation resumes across process restarts (the events.jsonl reuses F4's `event_wire`
schema exactly). R2b: `Ledger` -- the append-only Event Ledger (SQLite), the system's
timeline of Prediction Records + journal + gate outcomes, idempotent by record id and
queryable by device / time range. Both persist the same `event_wire` shape (ADR-0009).
"""
from copilot.memory.ledger import Ledger
from copilot.memory.session import SessionStore

__all__ = ["Ledger", "SessionStore"]
