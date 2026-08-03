"""copilot.memory.session -- Session Store (R2a, ADR-0009).

sessions/<id>/{events.jsonl, meta.json}: an append-only, per-session log of the
canonical trace events + minimal metadata, read back to resume a conversation
across process restarts. One events.jsonl per session, one JSON line per event in
the exact `event_wire` schema (ADR-0009: ONE vocabulary for the live stream and the
persisted log -- no second set). Resume = reload the user/assistant turns and feed
them back into the loop as `history`.

ponytail: plain files, no server -- the store is append+read-back, nothing more.
The idempotent-by-id records store is the Event Ledger (SQLite, R2b/#18); a
per-conversation single-writer lock (ADR-0009 nuance) lands with concurrent chats
(R6a/#24), not here -- one chatter per session today.

Self-check:  python3 -m copilot.memory.test_session
"""
import json
import os

from copilot.agent import event_wire

# event types whose payload is a conversation turn the model should see on resume.
_ROLE = {"user_msg": "user", "assistant_msg": "assistant"}


class SessionStore:
    """File-backed session store rooted at `root` (e.g. ./sessions). Sessions are
    created lazily on first append; a missing session reads back as empty."""

    def __init__(self, root: str):
        self.root = root

    def _dir(self, sid: str) -> str:
        return os.path.join(self.root, sid)

    def append(self, sid: str, events) -> None:
        """Append `events` (one JSON line each, event_wire schema) to the session's
        events.jsonl, creating the session dir + meta.json on first write. Append-only:
        prior lines are never rewritten, so a resumed session keeps its full history."""
        d = self._dir(sid)
        os.makedirs(d, exist_ok=True)
        meta = os.path.join(d, "meta.json")
        if not os.path.exists(meta):
            # ponytail: meta.json is in #17's store layout (ADR-0009) -- a session marker,
            # write-once. R2b/R5 hang case/ledger metadata here; keep the file, not the fields.
            with open(meta, "w") as f:
                json.dump({"id": sid}, f)
        with open(os.path.join(d, "events.jsonl"), "a") as f:
            for e in events:
                f.write(json.dumps(event_wire(e)) + "\n")

    def read(self, sid: str) -> list[dict]:
        """All persisted wire events for a session, in order; [] if the session has none."""
        path = os.path.join(self._dir(sid), "events.jsonl")
        if not os.path.exists(path):
            return []
        with open(path) as f:
            return [json.loads(line) for line in f if line.strip()]

    def history(self, sid: str) -> list[dict]:
        """Prior conversation turns as chat messages for the loop to resume from:
        user_msg/assistant_msg events -> {"role","content"} in order. Trace-only events
        (tool_call/tool_result/gate/think/artifact) are not conversation -> skipped, so
        the resumed model sees the dialogue, not the machinery."""
        msgs = []
        for w in self.read(sid):
            role = _ROLE.get(w["type"])
            if role and w.get("content"):
                msgs.append({"role": role, "content": w["content"]})
        return msgs
