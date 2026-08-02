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
    test_every_event_carries_ts_and_canonical_type()
    test_ask_back_streams_question_no_tool_call()
    test_think_event_streams_before_tool_call()
    test_streamed_event_round_trips_into_an_event()
    print("copilot.api self-check OK")


if __name__ == "__main__":
    _run()
