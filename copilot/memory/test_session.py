"""Assert-based self-check for the Session Store (R2a, ADR-0009).

Prior art: dataapi/check_dataset.py (assert + __main__, no framework).
Seam under test: SessionStore.append/read/history over sessions/<id>/events.jsonl --
append-only persistence, the event_wire schema on disk, and resume-across-restart via a
fresh store instance reading the same root.
Run:  python3 -m copilot.memory.test_session
"""
import shutil
import tempfile

from copilot.agent import Event
from copilot.memory import SessionStore


def test_append_read_roundtrips_event_wire_schema():
    root = tempfile.mkdtemp()
    try:
        s = SessionStore(root)
        evs = [Event("user_msg", {"content": "why is r1 slow?"}),
               Event("tool_call", {"name": "query_metrics", "arguments": {"device": "r1"}, "id": "c1"}),
               Event("assistant_msg", {"content": "r1 cpu pegged [metrics:0]"})]
        s.append("sess1", evs)
        wire = s.read("sess1")
        assert len(wire) == 3
        # exact event_wire shape: type + ts + payload, one line each
        assert wire[0]["type"] == "user_msg" and wire[0]["content"] == "why is r1 slow?"
        assert wire[0]["ts"] == evs[0].ts, "occurrence ts persisted, not re-stamped on read"
        assert wire[1]["name"] == "query_metrics"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_missing_session_reads_empty():
    root = tempfile.mkdtemp()
    try:
        assert SessionStore(root).read("nope") == []
        assert SessionStore(root).history("nope") == []
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_append_is_additive_across_turns():
    root = tempfile.mkdtemp()
    try:
        s = SessionStore(root)
        s.append("s", [Event("user_msg", {"content": "q1"}), Event("assistant_msg", {"content": "a1"})])
        s.append("s", [Event("user_msg", {"content": "q2"}), Event("assistant_msg", {"content": "a2"})])
        assert [w["content"] for w in s.read("s")] == ["q1", "a1", "q2", "a2"], \
            "second turn appended, first turn not clobbered"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_history_is_conversation_turns_only():
    root = tempfile.mkdtemp()
    try:
        s = SessionStore(root)
        s.append("s", [
            Event("user_msg", {"content": "why is r1 slow?"}),
            Event("tool_call", {"name": "query_metrics", "arguments": {}, "id": "c1"}),
            Event("tool_result", {"id": "c1", "name": "query_metrics", "content": "rows", "n": 1}),
            Event("gate", {"ok": True, "missing": [], "retry": 0}),
            Event("assistant_msg", {"content": "r1 cpu pegged [metrics:0]"}),
        ])
        hist = s.history("s")
        # only the user/assistant turns become resume messages; trace events are skipped.
        assert hist == [{"role": "user", "content": "why is r1 slow?"},
                        {"role": "assistant", "content": "r1 cpu pegged [metrics:0]"}]
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_survives_process_restart():
    # a fresh store instance (== a new process) reading the same root recovers the session.
    root = tempfile.mkdtemp()
    try:
        SessionStore(root).append("s", [Event("user_msg", {"content": "q"}),
                                        Event("assistant_msg", {"content": "a"})])
        reopened = SessionStore(root)                     # simulates a restart
        assert reopened.history("s") == [{"role": "user", "content": "q"},
                                         {"role": "assistant", "content": "a"}]
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _run():
    test_append_read_roundtrips_event_wire_schema()
    test_missing_session_reads_empty()
    test_append_is_additive_across_turns()
    test_history_is_conversation_turns_only()
    test_survives_process_restart()
    print("copilot.memory self-check OK")


if __name__ == "__main__":
    _run()
