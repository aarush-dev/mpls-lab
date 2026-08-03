"""Behaviour tests / self-check for the streamed chat endpoint (F4).

Prior art: dataapi/check_dataset.py (assert + __main__, no framework).
Seam under test: POST /chat over HTTP (FastAPI TestClient), with the LLM client +
tool adapter stubbed via dependency_overrides (spec #3 §Testing, ADR-0010). Asserts
the canonical ADR-0009 event stream, each event timestamped.
Run:  python3 -m copilot.api.test_api
"""
import atexit
import json
import shutil
import tempfile
from datetime import datetime

from fastapi.testclient import TestClient

from copilot.adapter import StubAdapter
from copilot.agent import EVENT_TYPES, Event
from copilot.api.app import (
    app, get_adapter, get_config, get_kg, get_llm, get_retriever, get_skills,
)
from copilot.config import Config
from copilot.llm import Reply, ScriptedLLM, ToolCall, final, tool_call
from copilot.retrieval import Doc, HashEmbedder, LanceRetriever
from copilot.skills import Skill

ROWS = [{"device": "r1", "ts": 100 + i, "cpu": 90 + i} for i in range(3)]

# KB + topology fixtures for the I2b retrieval-over-HTTP test (real corpus = S1/S2).
KB = [
    Doc(id="rb-bgp", text="bgp neighbor down runbook check hold timer and session reset",
        source="runbook", node="pe1", ts=1000),
    Doc(id="inc-near", text="past incident bgp session dropped hold timer expiry", source="incident",
        node="pe2", ts=1001),
    Doc(id="inc-far", text="past incident bgp session dropped hold timer expiry", source="incident",
        node="pe4", ts=1002),
]
TOPOLOGY = {"nodes": [{"id": n} for n in ("pe1", "pe2", "pe3", "pe4")],
            "links": [{"source": "pe1", "target": "pe2"},
                      {"source": "pe2", "target": "pe3"},
                      {"source": "pe3", "target": "pe4"}]}


def _kb_retriever():
    d = tempfile.mkdtemp()
    atexit.register(shutil.rmtree, d, ignore_errors=True)
    r = LanceRetriever(HashEmbedder(), d)
    r.add(KB)
    return r


def _client(script):
    app.dependency_overrides.clear()          # fresh stubs per test
    app.dependency_overrides[get_llm] = lambda: ScriptedLLM(script)
    app.dependency_overrides[get_adapter] = lambda: StubAdapter(metrics_rows=ROWS)
    return TestClient(app)


def _events(resp):
    # parse the SSE body: blocks separated by blank line, each `data: <json>`
    out = []
    for block in resp.text.split("\n\n"):
        block = block.strip()
        if block.startswith("data:"):
            out.append(json.loads(block[len("data:"):].strip()))
    return out


def test_chat_streams_tool_call_and_cited_answer():
    client = _client([
        tool_call("query_metrics", {"device": "r1", "limit": 5}, id="c1"),
        final("r1 cpu is pegged [metrics:0]"),
        final('{"pass": true}'),                  # I4b stage-2 self-judge verdict
    ])
    resp = client.post("/chat", json={"question": "why is r1 slow?",
                                      "start": 100, "end": 200})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    evs = _events(resp)
    assert [e["type"] for e in evs] == \
        ["user_msg", "tool_call", "tool_result", "gate", "assistant_msg"]
    # a visible tool_call event + a final cited answer (acceptance)
    assert evs[1]["name"] == "query_metrics"
    assert evs[-1]["content"] == "r1 cpu is pegged [metrics:0]"
    # the observation carried real evidence ids
    assert "[metrics:0]" in evs[2]["content"]


def test_every_event_carries_ts_and_canonical_type():
    client = _client([
        tool_call("query_metrics", {"device": "r1"}, id="c1"),
        final("done [metrics:0]"),
        final('{"pass": true}'),                  # I4b stage-2 self-judge verdict
    ])
    resp = client.post("/chat", json={"question": "why slow?",
                                      "start": 100, "end": 200})
    for e in _events(resp):
        assert e["type"] in EVENT_TYPES, f"non-canonical type {e['type']!r}"
        # ISO-UTC ts, parseable, tz-aware
        dt = datetime.fromisoformat(e["ts"])
        assert dt.tzinfo is not None, "ts must be tz-aware UTC"


def test_device_question_streams_cited_log_and_flow_rows():
    # I1 acceptance: a device question returns filtered, cited log/flow rows through
    # the HTTP seam (search_logs + flows now ride the loop).
    app.dependency_overrides.clear()
    logs = [{"device": "r1", "ts": 100 + i, "msg": f"bgp flap {i}"} for i in range(2)]
    flows = [{"device": "r1", "ts": 100 + i, "bytes": 1000 + i} for i in range(4)]
    app.dependency_overrides[get_llm] = lambda: ScriptedLLM([
        tool_call("search_logs", {"device": "r1"}, id="c1"),
        tool_call("flows", {"device": "r1"}, id="c2"),
        final("r1 flapping [events:0] with a traffic spike [flows:0]"),
        final('{"pass": true}'),                  # I4b stage-2 self-judge verdict
    ])
    app.dependency_overrides[get_adapter] = lambda: StubAdapter(events_rows=logs, flows_rows=flows)
    resp = TestClient(app).post("/chat", json={"question": "what happened on r1?",
                                               "start": 100, "end": 200})
    evs = _events(resp)
    assert [e["type"] for e in evs] == \
        ["user_msg", "tool_call", "tool_call", "tool_result", "tool_result", "gate", "assistant_msg"] or \
        [e["type"] for e in evs] == \
        ["user_msg", "tool_call", "tool_result", "tool_call", "tool_result", "gate", "assistant_msg"]
    names = [e["name"] for e in evs if e["type"] == "tool_call"]
    assert names == ["search_logs", "flows"]
    trs = [e for e in evs if e["type"] == "tool_result"]
    assert any("[events:0]" in e["content"] for e in trs)
    assert any("[flows:0]" in e["content"] for e in trs)
    assert evs[-1]["content"] == "r1 flapping [events:0] with a traffic spike [flows:0]"


def test_fault_returns_cited_runbook_and_nearby_incident_over_http():
    # I2b acceptance: ask a fault -> a cited runbook + a similar past incident via the
    # HTTP seam, and the hop-filter narrows incidents to devices near the focus (pe1).
    app.dependency_overrides.clear()
    app.dependency_overrides[get_llm] = lambda: ScriptedLLM([
        tool_call("search_runbooks", {"query": "bgp neighbor down"}, id="c1"),
        tool_call("search_incidents", {"query": "bgp session dropped", "device": "pe1",
                                       "hops": 2}, id="c2"),
        final("bgp hold-timer expiry [rb-bgp]; matches past incident [inc-near]"),
        final('{"pass": true}'),                  # I4b stage-2 self-judge verdict
    ])
    app.dependency_overrides[get_adapter] = lambda: StubAdapter(topology=TOPOLOGY)
    app.dependency_overrides[get_retriever] = _kb_retriever
    resp = TestClient(app).post("/chat", json={"question": "bgp neighbor down on pe1?",
                                               "start": 100, "end": 200})
    evs = _events(resp)
    names = [e["name"] for e in evs if e["type"] == "tool_call"]
    assert names == ["search_runbooks", "search_incidents"]
    trs = [e for e in evs if e["type"] == "tool_result"]
    assert any("[rb-bgp]" in e["content"] and "source=runbook" in e["content"] for e in trs)
    inc = next(e["content"] for e in trs if e["name"] == "search_incidents")
    assert "[inc-near]" in inc, "1-hop incident kept"
    assert "[inc-far]" not in inc, "3-hop incident filtered out by the hop filter"
    assert evs[-1]["content"] == "bgp hold-timer expiry [rb-bgp]; matches past incident [inc-near]"


def test_walk_topology_graph_streams_enriched_blast_radius_over_http():
    # I3 acceptance: ask for the blast radius -> a cited subgraph enriched with live status
    # over the HTTP seam. Line pe1-pe2-pe3-pe4; metrics only on pe1.
    app.dependency_overrides.clear()
    app.dependency_overrides[get_llm] = lambda: ScriptedLLM([
        tool_call("walk_topology_graph", {"device": "pe1", "hops": 2}, id="c1"),
        final("blast radius is pe1->pe2->pe3; pe1 cpu hot"),
    ])
    pe1_metrics = [{"device": "pe1", "ts": 100 + i, "cpu": 90 + i} for i in range(3)]
    app.dependency_overrides[get_adapter] = lambda: StubAdapter(metrics_rows=pe1_metrics,
                                                                topology=TOPOLOGY)
    # the scripted answer is uncited -> a stage-1 block; retries=0 keeps it a one-shot block
    # (the tool_result under test still streams) instead of re-entering the loop (I4b).
    app.dependency_overrides[get_config] = lambda: Config(gate_max_retries=0)
    resp = TestClient(app).post("/chat", json={"question": "downstream of pe1?",
                                               "start": 100, "end": 200})
    evs = _events(resp)
    assert [e["name"] for e in evs if e["type"] == "tool_call"] == ["walk_topology_graph"]
    tr = next(e for e in evs if e["type"] == "tool_result")
    assert "[topo:pe1] hop 0: cpu=92" in tr["content"], "focus cited + enriched with live metric"
    assert "[topo:pe2] hop 1: no metrics" in tr["content"] and "[topo:pe3] hop 2" in tr["content"]
    assert "pe4" not in tr["content"], "beyond the hop radius"


def test_get_kg_respects_flag_and_source():
    # ADR-0007: kg_enabled is default-on, off-able, and the curated KG is loaded only when ON
    # AND a source is seeded. This is what makes "identical with kg off" real (not vacuous):
    # OFF -> None regardless of source, so the walk is KG-free.
    import os
    d = tempfile.mkdtemp()
    atexit.register(shutil.rmtree, d, ignore_errors=True)
    path = f"{d}/kg.json"
    with open(path, "w") as f:
        json.dump({"pe2": "curated: flaps under load"}, f)
    os.environ.pop("COPILOT_KG_URI", None)
    assert get_kg(Config(kg_enabled=True)) is None, "enabled but no source -> None (nothing seeded)"
    os.environ["COPILOT_KG_URI"] = path
    try:
        assert get_kg(Config(kg_enabled=False)) is None, "flag OFF -> None even with a source"
        assert get_kg(Config(kg_enabled=True)) == {"pe2": "curated: flaps under load"}
    finally:
        os.environ.pop("COPILOT_KG_URI", None)


def test_unfiltered_call_rejected_through_http_seam():
    # over-broad tool call (no device/pattern) -> F2 contract guidance as a tool_result,
    # never rows (acceptance: unfiltered rejected).
    client = _client([
        tool_call("search_logs", {}, id="c1"),
        final("need to narrow to a device"),
    ])
    # failed tool call -> a stage-1 block; retries=0 keeps it one-shot (tool_result still streams).
    app.dependency_overrides[get_config] = lambda: Config(gate_max_retries=0)
    resp = client.post("/chat", json={"question": "show me everything",
                                      "start": 100, "end": 200})
    tr = [e for e in _events(resp) if e["type"] == "tool_result"][0]
    assert tr["content"].startswith("error:") and "device" in tr["content"]


def test_ask_back_streams_question_no_tool_call():
    client = _client([final("Which device should I look at?")])
    resp = client.post("/chat", json={"question": "is the network ok?",
                                      "start": 100, "end": 200})
    evs = _events(resp)
    assert [e["type"] for e in evs] == ["user_msg", "assistant_msg"]
    assert evs[-1]["content"].startswith("Which device")


def test_think_event_streams_before_tool_call():
    client = _client([
        Reply(content="checking r1 metrics first",
              tool_calls=(ToolCall("query_metrics", {"device": "r1"}, id="c1"),)),
        final("r1 cpu high [metrics:0]"),
        final('{"pass": true}'),                  # I4b stage-2 self-judge verdict
    ])
    resp = client.post("/chat", json={"question": "why slow?",
                                      "start": 100, "end": 200})
    evs = _events(resp)
    assert [e["type"] for e in evs] == \
        ["user_msg", "think", "tool_call", "tool_result", "gate", "assistant_msg"]
    assert evs[1]["content"] == "checking r1 metrics first"


def test_streamed_event_round_trips_into_an_event():
    # audit correction (#8): a streamed wire event round-trips into events.jsonl
    # unchanged. Reconstruct an Event from the wire dict (drop the store-stamped ts)
    # and it must be a valid canonical Event with the same type + payload.
    client = _client([
        tool_call("query_metrics", {"device": "r1"}, id="c1"),
        final("done [metrics:0]"),
        final('{"pass": true}'),                  # I4b stage-2 self-judge verdict
    ])
    resp = client.post("/chat", json={"question": "why slow?",
                                      "start": 100, "end": 200})
    for wire in _events(resp):
        data = {k: v for k, v in wire.items() if k not in ("type", "ts")}
        ev = Event(wire["type"], data)          # raises if type non-canonical
        assert ev.type == wire["type"] and ev.data == data


def test_manual_skill_invoke_over_http():
    # I5 acceptance: a human invokes a named skill via /chat (`skills` field) -> the catalog
    # description sits in the base prompt AND the invoked body is preloaded, reachable over
    # HTTP; the load_skill tool is advertised so the agent can also auto-select one.
    app.dependency_overrides.clear()
    skills = {"bgp_flap": Skill("bgp_flap", "chase a bgp flap", "STEP: pull the session logs")}
    llm = ScriptedLLM([final("which device?")])          # ask-back -> one call, gate bypassed
    app.dependency_overrides[get_llm] = lambda: llm
    app.dependency_overrides[get_adapter] = lambda: StubAdapter()
    app.dependency_overrides[get_skills] = lambda: skills
    resp = TestClient(app).post("/chat", json={"question": "help", "start": 100, "end": 200,
                                               "skills": ["bgp_flap"]})
    assert resp.status_code == 200
    system, tools = llm.calls[0][0][0]["content"], llm.calls[0][1]
    assert "chase a bgp flap" in system, "catalog description in the base prompt"
    assert "STEP: pull the session logs" in system, "invoked skill body preloaded over HTTP"
    assert any(t["name"] == "load_skill" for t in tools), "load_skill advertised over HTTP"


def test_session_persists_and_resumes_over_http():
    # R2a acceptance: chat with a session_id, reload the session (a fresh store == a process
    # restart), continue -> prior turns reach the model AND this turn's events land in
    # events.jsonl, append-only.
    from copilot.api.app import get_sessions
    from copilot.memory import SessionStore

    d = tempfile.mkdtemp()
    atexit.register(shutil.rmtree, d, ignore_errors=True)

    app.dependency_overrides.clear()
    app.dependency_overrides[get_adapter] = lambda: StubAdapter(metrics_rows=ROWS)
    app.dependency_overrides[get_sessions] = lambda: SessionStore(d)
    app.dependency_overrides[get_llm] = lambda: ScriptedLLM([
        tool_call("query_metrics", {"device": "r1"}, id="c1"),
        final("r1 cpu pegged [metrics:0]"),
        final('{"pass": true}'),                          # I4b stage-2 self-judge verdict
    ])
    r1 = TestClient(app).post("/chat", json={"question": "why is r1 slow?",
                                             "start": 100, "end": 200, "session_id": "s1"})
    assert r1.status_code == 200

    # a fresh store instance (the restart) recovers turn 1 from events.jsonl
    reopened = SessionStore(d)
    wire = reopened.read("s1")
    assert wire[0]["type"] == "user_msg"
    assert any(w["type"] == "assistant_msg" and w["content"] == "r1 cpu pegged [metrics:0]"
               for w in wire), "the answer landed in events.jsonl"

    # turn 2: resume -> the prior user+assistant turns reach the model
    resumed = ScriptedLLM([final("is it still the same cause?")])   # ask-back, one turn
    app.dependency_overrides[get_sessions] = lambda: reopened
    app.dependency_overrides[get_llm] = lambda: resumed
    r2 = TestClient(app).post("/chat", json={"question": "and now?",
                                             "start": 100, "end": 200, "session_id": "s1"})
    assert r2.status_code == 200
    contents = [m["content"] for m in resumed.calls[0][0]]          # messages of the resumed loop
    assert "why is r1 slow?" in contents and "r1 cpu pegged [metrics:0]" in contents, \
        "prior user+assistant turns reached the model on resume"
    assert contents[-1] == "and now?", "the new question comes last"
    assert len(reopened.read("s1")) > len(wire), "turn 2 appended, turn 1 not clobbered"


def test_no_session_id_persists_nothing():
    # a stateless one-off chat: no session_id -> the store is never touched.
    from copilot.api.app import get_sessions
    from copilot.memory import SessionStore

    d = tempfile.mkdtemp()
    atexit.register(shutil.rmtree, d, ignore_errors=True)
    app.dependency_overrides.clear()
    app.dependency_overrides[get_adapter] = lambda: StubAdapter(metrics_rows=ROWS)
    app.dependency_overrides[get_sessions] = lambda: SessionStore(d)
    app.dependency_overrides[get_llm] = lambda: ScriptedLLM([final("which device?")])
    TestClient(app).post("/chat", json={"question": "hi", "start": 100, "end": 200})
    import os
    assert os.listdir(d) == [], "no session_id -> nothing written"


def _ledger_client(script, ledger, *, retries=1):
    from copilot.api.app import get_ledger, get_sessions
    from copilot.memory import SessionStore

    sroot = tempfile.mkdtemp()
    atexit.register(shutil.rmtree, sroot, ignore_errors=True)
    app.dependency_overrides.clear()
    app.dependency_overrides[get_adapter] = lambda: StubAdapter(metrics_rows=ROWS)
    app.dependency_overrides[get_sessions] = lambda: SessionStore(sroot)
    app.dependency_overrides[get_ledger] = lambda: ledger
    app.dependency_overrides[get_config] = lambda: Config(gate_max_retries=retries)
    app.dependency_overrides[get_llm] = lambda: ScriptedLLM(script)
    return TestClient(app)


def test_gate_outcomes_land_in_the_ledger_pass_and_fail():
    # R2b acceptance: a real investigation's gate outcome (pass AND fail) lands in the Event
    # Ledger, keyed to the session, and survives a restart (a fresh Ledger on the same file).
    import os

    from copilot.memory import Ledger

    dbdir = tempfile.mkdtemp()
    atexit.register(shutil.rmtree, dbdir, ignore_errors=True)
    path = os.path.join(dbdir, "ledger.db")
    led = Ledger(path)

    # pass: a cited answer clears the gate (ok=True lands).
    _ledger_client([
        tool_call("query_metrics", {"device": "r1"}, id="c1"),
        final("r1 cpu pegged [metrics:0]"),
        final('{"pass": true}'),
    ], led).post("/chat", json={"question": "why is r1 slow?", "start": 100, "end": 200,
                                "session_id": "pass1"})
    # fail: an uncited answer is blocked; retries=0 keeps it a one-shot fail (ok=False lands).
    _ledger_client([final("r1 is slow")], led, retries=0).post(
        "/chat", json={"question": "why is r1 slow?", "start": 100, "end": 200,
                       "session_id": "fail1"})

    # one turn, TWO gate events: an uncited answer fails (ok=False, retry=0), the loop re-enters
    # and the cited retry passes (ok=True, retry=1). Both must land as DISTINCT ledger rows --
    # the id folds in `retry`, so a same-ts pair can't collide under INSERT OR IGNORE.
    _ledger_client([
        tool_call("query_metrics", {"device": "r1"}, id="c1"),
        final("r1 is slow"),                              # uncited -> gate fail (retry=0)
        final("r1 cpu pegged [metrics:0]"),               # cited retry -> gate pass (retry=1)
        final('{"pass": true}'),                          # self-judge over the retry
    ], led, retries=1).post("/chat", json={"question": "why is r1 slow?", "start": 100,
                                           "end": 200, "session_id": "retry1"})

    # a fresh Ledger on the same file (the restart) recovers every outcome from the timeline.
    reopened = Ledger(path)
    gates = reopened.by_time("2000-01-01T00:00:00+00:00", "2100-01-01T00:00:00+00:00")
    assert all(g["type"] == "gate" for g in gates), "only gate outcomes routed to the ledger so far"
    assert {g["ok"] for g in gates} == {True, False}, "both a passing and a failing gate outcome persisted"
    # the retry1 turn's two gates survived as two rows (no INSERT OR IGNORE collision).
    assert len(gates) == 4, "pass1(1) + fail1(1) + retry1(2) = 4 distinct gate rows"


def _run():
    test_chat_streams_tool_call_and_cited_answer()
    test_session_persists_and_resumes_over_http()
    test_no_session_id_persists_nothing()
    test_gate_outcomes_land_in_the_ledger_pass_and_fail()
    test_manual_skill_invoke_over_http()
    test_device_question_streams_cited_log_and_flow_rows()
    test_fault_returns_cited_runbook_and_nearby_incident_over_http()
    test_walk_topology_graph_streams_enriched_blast_radius_over_http()
    test_get_kg_respects_flag_and_source()
    test_unfiltered_call_rejected_through_http_seam()
    test_every_event_carries_ts_and_canonical_type()
    test_ask_back_streams_question_no_tool_call()
    test_think_event_streams_before_tool_call()
    test_streamed_event_round_trips_into_an_event()
    print("copilot.api self-check OK")


if __name__ == "__main__":
    _run()
