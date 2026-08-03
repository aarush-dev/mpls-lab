"""copilot.memory.ledger -- Event Ledger (R2b, ADR-0009).

The system's timeline: an append-only, immutable-once-written SQLite store of Prediction
Records + journal + gate outcomes, idempotent by record id and queryable by device / time
range (ADR-0009). Every row is one `event_wire` dict (ADR-0009: ONE persisted schema -- the
same shape the live SSE stream and sessions/<id>/events.jsonl use); `ts`/`type` are lifted
into columns for the range/timeline queries, `device` for the by-device query, the full wire
kept verbatim in `wire` so a read round-trips losslessly.

ponytail: one SQLite file, connection-per-call (a local-file connect is ~microseconds, and
this sidesteps sqlite3's same-thread guard under FastAPI's threadpool). Writes are serialized
by SQLite's own file lock -- ADR-0009's per-conversation single-writer lock lands with
concurrent chats (R6a/#24), not here. `INSERT OR IGNORE` on the PK IS the append-only +
idempotent guarantee: a written row is never updated, a re-append of the same id is a no-op.

Self-check:  python3 -m copilot.memory.test_ledger
"""
import json
import os
import sqlite3
from contextlib import closing

_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS ledger("
    " id TEXT PRIMARY KEY, ts TEXT NOT NULL, device TEXT, type TEXT NOT NULL, wire TEXT NOT NULL);"
    "CREATE INDEX IF NOT EXISTS ix_ledger_device ON ledger(device);"
    "CREATE INDEX IF NOT EXISTS ix_ledger_ts ON ledger(ts);"
)


class Ledger:
    """Append-only Event Ledger backed by the SQLite file at `path` (created on first use)."""

    def __init__(self, path: str):
        self.path = path
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        with closing(sqlite3.connect(path)) as db, db:
            db.executescript(_SCHEMA)

    def append(self, rec_id: str, wire: dict, device: str | None = None) -> None:
        """Record one wire event under `rec_id`. Append-only + idempotent: the row is written
        once and never updated, so re-appending the same id (even with a changed payload) is a
        no-op -- the first write is the immutable timeline entry (ADR-0009)."""
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("INSERT OR IGNORE INTO ledger(id, ts, device, type, wire) VALUES(?,?,?,?,?)",
                       (rec_id, wire["ts"], device, wire["type"], json.dumps(wire)))

    def by_device(self, device: str) -> list[dict]:
        """All wire events recorded for `device`, in ts order."""
        return self._q("SELECT wire FROM ledger WHERE device=? ORDER BY ts", (device,))

    def by_time(self, start: str, end: str) -> list[dict]:
        """All wire events whose ts is within [start, end] inclusive, in ts order. Bounds are
        ISO-8601 UTC strings -- a lexical compare is chronological because the ts are all
        zero-padded, same-offset UTC (as event_wire stamps them)."""
        return self._q("SELECT wire FROM ledger WHERE ts>=? AND ts<=? ORDER BY ts", (start, end))

    def _q(self, sql: str, params: tuple) -> list[dict]:
        with closing(sqlite3.connect(self.path)) as db:
            return [json.loads(w) for (w,) in db.execute(sql, params)]
