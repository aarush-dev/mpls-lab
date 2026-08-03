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
from copilot.api.app import app, get_adapter, get_kg, get_llm, get_retriever
from copilot.config import Config
from copilot.llm import Reply, ScriptedLLM, ToolCall, final, tool_call
from copilot.retrieval import Doc, HashEmbedder, LanceRetriever

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
    ])
    resp = client.post("/chat", json={"question": "why is r1 slow?",
                                      "start": 100, "end": 200})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    evs = _events(resp)
    assert [e["type"] for e in evs] == \
        ["user_msg", "tool_call", "tool_result", "assistant_msg"]
    # a visible tool_call event + a final cited answer (acceptance)
    assert evs[1]["name"] == "query_metrics"
    assert evs[-1]["content"] == "r1 cpu is pegged [metrics:0]"
    # the observation carried real evidence ids
    assert "[metrics:0]" in evs[2]["content"]


def test_every_event_carries_ts_and_canonical_type():
    client = _client([
        tool_call("query_metrics", {"device": "r1"}, id="c1"),
        final("done [metrics:0]"),
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
    ])
    app.dependency_overrides[get_adapter] = lambda: StubAdapter(events_rows=logs, flows_rows=flows)
    resp = TestClient(app).post("/chat", json={"question": "what happened on r1?",
                                               "start": 100, "end": 200})
    evs = _events(resp)
    assert [e["type"] for e in evs] == \
        ["user_msg", "tool_call", "tool_call", "tool_result", "tool_result", "assistant_msg"] or \
        [e["type"] for e in evs] == \
        ["user_msg", "tool_call", "tool_result", "tool_call", "tool_result", "assistant_msg"]
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
    ])
    resp = client.post("/chat", json={"question": "why slow?",
                                      "start": 100, "end": 200})
    evs = _events(resp)
    assert [e["type"] for e in evs] == \
        ["user_msg", "think", "tool_call", "tool_result", "assistant_msg"]
    assert evs[1]["content"] == "checking r1 metrics first"


def test_streamed_event_round_trips_into_an_event():
    # audit correction (#8): a streamed wire event round-trips into events.jsonl
    # unchanged. Reconstruct an Event from the wire dict (drop the store-stamped ts)
    # and it must be a valid canonical Event with the same type + payload.
    client = _client([
        tool_call("query_metrics", {"device": "r1"}, id="c1"),
        final("done [metrics:0]"),
    ])
    resp = client.post("/chat", json={"question": "why slow?",
                                      "start": 100, "end": 200})
    for wire in _events(resp):
        data = {k: v for k, v in wire.items() if k not in ("type", "ts")}
        ev = Event(wire["type"], data)          # raises if type non-canonical
        assert ev.type == wire["type"] and ev.data == data


def _run():
    test_chat_streams_tool_call_and_cited_answer()
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
