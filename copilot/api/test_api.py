"""Behaviour tests / self-check for the streamed chat endpoint (F4).

Prior art: dataapi/check_dataset.py (assert + __main__, no framework).
Seam under test: POST /chat over HTTP (FastAPI TestClient), with the LLM client +
tool adapter stubbed via dependency_overrides (spec #3 §Testing, ADR-0010). Asserts
the canonical ADR-0009 event stream, each event timestamped.
Run:  python3 -m copilot.api.test_api
"""
import json
from datetime import datetime

from fastapi.testclient import TestClient

from copilot.adapter import StubAdapter
from copilot.agent import EVENT_TYPES, Event
from copilot.api.app import app, get_adapter, get_llm
from copilot.llm import Reply, ScriptedLLM, ToolCall, final, tool_call

ROWS = [{"device": "r1", "ts": 100 + i, "cpu": 90 + i} for i in range(3)]


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
    test_unfiltered_call_rejected_through_http_seam()
    test_every_event_carries_ts_and_canonical_type()
    test_ask_back_streams_question_no_tool_call()
    test_think_event_streams_before_tool_call()
    test_streamed_event_round_trips_into_an_event()
    print("copilot.api self-check OK")


if __name__ == "__main__":
    _run()
