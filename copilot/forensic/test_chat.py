"""Assert-based self-check for multi-chat per case + frozen-window follow-ups (R6a, ADR-0014/0002/0009).

Prior art: dataapi/check_dataset.py (assert + __main__, no framework); mirrors test_case/test_session.
Seams under test:
  create_case(...)                                  -- now seeds the initial run as chat `initial` + window.json
  follow_up(case_dir, chat_id, question, ...)       -- one follow-up turn, frozen window + replay adapter
  frozen_window(case_dir)                           -- the case's pinned WindowContext (T_snapshot guard in force)
  SessionStore.append (concurrent)                  -- single-writer lock serialises writes (ADR-0009)
Run:  python3 -m copilot.forensic.test_chat
"""
import json
import os
import tempfile
import threading

from copilot.adapter import FilterError
from copilot.config import Config
from copilot.emulator import emulate_record
from copilot.llm.stub import ScriptedLLM, final, tool_call
from copilot.forensic.case import create_case
from copilot.forensic.chat import (
    INITIAL_CHAT, case_chats, follow_up, frozen_window,
)
from copilot.memory import SessionStore
from copilot.window import WindowContext

LABEL = {"scenario_id": "congestion-pe6", "type": "congestion", "device": "pe6",
         "severity": "high", "target": {"device": "pe6"},
         "t_start": "2026-06-21T14:00:00Z", "t_impact": "2026-06-21T14:01:00Z",
         "t_end": "2026-06-21T14:02:00Z", "lead_time": 60.0, "probe": "latency_ms",
         "baseline_value": 10.0, "impact_value": 40.0, "signature": "sig-pe6"}
REC = emulate_record(LABEL, error_profile="oracle", now="2026-06-21T14:01:00Z")
WIN = WindowContext(1000, 1600, frozen=True)
STUB_METRICS = [{"device": "pe6", "ts": 1200, "latency_ms": 40},
                {"device": "pe6", "ts": 1300, "queue_drops": 5}]


def _stub_live():
    from copilot.adapter import StubAdapter
    return StubAdapter(metrics_rows=STUB_METRICS)


def _script():   # model reads metrics, answers with two citations, judge passes
    return [tool_call("query_metrics", {"device": "pe6"}),
            final("pe6 congestion [metrics:0] [metrics:1]."), final('{"pass": true}')]


def _make_case(root):
    return create_case(REC, WIN, live_adapter=_stub_live(), llm=ScriptedLLM(_script()),
                       cfg=Config(), cases_root=root)


def test_create_case_seeds_initial_chat_and_window():
    with tempfile.TemporaryDirectory() as d:
        cd = _make_case(d)
        assert os.path.exists(os.path.join(cd, "window.json")), "frozen window persisted for follow-ups"
        hist = case_chats(cd).history(INITIAL_CHAT)
        assert hist and hist[0]["role"] == "user", "the initial report run is persisted as chat `initial`"


def test_follow_up_sees_own_history_not_another_chats():
    # acceptance: a follow-up sees the prior turns of its OWN chat and not another chat's.
    with tempfile.TemporaryDirectory() as d:
        cd = _make_case(d)
        follow_up(cd, "a", "is pe6 still congested?", llm=ScriptedLLM(_script()), cfg=Config())
        follow_up(cd, "b", "what about the blast radius?", llm=ScriptedLLM(_script()), cfg=Config())
        ha = [m["content"] for m in case_chats(cd).history("a") if m["role"] == "user"]
        hb = [m["content"] for m in case_chats(cd).history("b") if m["role"] == "user"]
        assert "is pe6 still congested?" in ha and "what about the blast radius?" not in ha
        assert "what about the blast radius?" in hb and "is pe6 still congested?" not in hb
        # a second turn on `a` resumes a's history (sees the prior turn)
        follow_up(cd, "a", "and now?", llm=ScriptedLLM(_script()), cfg=Config())
        ha2 = [m["content"] for m in case_chats(cd).history("a") if m["role"] == "user"]
        assert ha2 == ["is pe6 still congested?", "and now?"], "chat `a` accumulates only its own turns"


def test_follow_up_asking_past_snapshot_rejected_with_guidance():
    # acceptance: a follow-up asking for data past T_snapshot is rejected at the adapter, w/ guidance.
    # Exercised through the REAL follow_up path (a requested window end past the freeze), not a
    # hand-built adapter call -- the case is frozen, so the request is refused, never silently clamped.
    with tempfile.TemporaryDirectory() as d:
        cd = _make_case(d)
        w = frozen_window(cd)
        assert w.frozen and w.t_snapshot == WIN.end, "follow-up window is pinned at T_snapshot"
        try:
            follow_up(cd, "chatX", "show me the next 10 minutes", llm=ScriptedLLM(_script()),
                      cfg=Config(), requested_end=w.end + 600)
            raise AssertionError("a follow-up past T_snapshot must be rejected")
        except FilterError as e:
            assert "T_snapshot" in str(e), "rejection carries actionable guidance"
        # a request WITHIN the freeze runs normally (the guard doesn't over-reject).
        out = follow_up(cd, "chatX", "recap the congestion", llm=ScriptedLLM(_script()),
                        cfg=Config(), requested_end=w.end)
        assert out.answer, "an in-freeze follow-up still runs"


def test_resolve_case_dir_blocks_traversal():
    # security: an untrusted case id must not escape cases_root -- not even a single-segment `..`
    # (the sanitise charset keeps `.`). Only a real case dir INSIDE the root resolves.
    from copilot.forensic.chat import resolve_case_dir
    with tempfile.TemporaryDirectory() as root:
        cd = _make_case(root)
        good = os.path.basename(cd)
        assert resolve_case_dir(root, good) == os.path.realpath(cd), "a real case resolves"
        for bad in ("..", ".", "../../etc", "/etc", good + "/../.."):
            try:
                resolve_case_dir(root, bad)
                raise AssertionError(f"traversal id {bad!r} must be rejected")
            except ValueError:
                pass


def test_concurrent_appends_to_one_chat_are_serialised():
    # acceptance: concurrent writes to one conversation are serialised (ADR-0009 single-writer lock).
    from copilot.agent import Event
    with tempfile.TemporaryDirectory() as root:
        store = SessionStore(root)
        n = 20

        def writer(i):
            store.append("s", [Event("user_msg", {"content": f"q{i}"}),
                               Event("assistant_msg", {"content": f"a{i}"})])
        threads = [threading.Thread(target=writer, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        # every line intact (no torn/interleaved write) and all 2*n turns present.
        path = os.path.join(root, "s", "events.jsonl")
        lines = [ln for ln in open(path) if ln.strip()]
        assert len(lines) == 2 * n, f"expected {2*n} intact lines, got {len(lines)}"
        c = [json.loads(ln)["content"] for ln in lines]  # raises on a torn line
        assert set(c) == {f"{p}{i}" for i in range(n) for p in "qa"}, "no write lost or duplicated"
        # single-writer: each append (q_i,a_i) lands contiguously -- a turn is never split by another.
        for j in range(0, len(c), 2):
            assert c[j][0] == "q" and c[j + 1] == "a" + c[j][1:], \
                f"turn interleaved at line {j}: {c[j]},{c[j+1]} -- writes not serialised"


def _run():
    test_create_case_seeds_initial_chat_and_window()
    test_follow_up_sees_own_history_not_another_chats()
    test_follow_up_asking_past_snapshot_rejected_with_guidance()
    test_resolve_case_dir_blocks_traversal()
    test_concurrent_appends_to_one_chat_are_serialised()
    print("copilot.forensic.test_chat OK")


if __name__ == "__main__":
    _run()
