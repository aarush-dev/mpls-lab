"""Assert-based tests / self-check for the agent loop core (F3).

Prior art: dataapi/check_dataset.py (assert + __main__, no framework).
Seam under test: investigate(question, window, *, llm, adapter, cfg) -> Outcome,
with the LLM client + tool adapter stubbed (spec #3 §Testing). No HTTP yet (F4).
Run:  python3 -m copilot.agent.test_agent
"""
import dataclasses

from copilot.adapter import StubAdapter
from copilot.agent import Event, Outcome, investigate, parse_tool_calls
from copilot.config import Config
from copilot.llm import Reply, ScriptedLLM, ToolCall, final, tool_call

WINDOW = (100, 200)
ROWS = [{"device": "r1", "ts": 100 + i, "cpu": 90 + i} for i in range(3)]


def _cfg(**kw):
    return dataclasses.replace(Config(), **kw)


def test_scripted_investigation_completes_with_query_metrics():
    # native function-calling path: a tool call, then a cited answer.
    llm = ScriptedLLM([
        tool_call("query_metrics", {"device": "r1", "limit": 5}, id="c1"),
        final("r1 cpu is pegged [metrics:0]"),
    ])
    out = investigate("why is r1 slow?", WINDOW,
                      llm=llm, adapter=StubAdapter(metrics_rows=ROWS), cfg=_cfg())
    assert isinstance(out, Outcome)
    assert out.answer == "r1 cpu is pegged [metrics:0]"
    assert out.stopped is None
    assert [e.type for e in out.events] == \
        ["user_msg", "tool_call", "tool_result", "assistant_msg"]
    # the window the loop passed, not the model, scoped the query -> rows observed;
    # the observation shows real evidence ids (metrics:N) the answer can cite.
    tr = out.of_type("tool_result")[0]
    assert "[metrics:0]" in tr.data["content"] and tr.data["n"] == 3
    # every event type is a canonical ADR-0009 enum member
    assert all(isinstance(e, Event) for e in out.events)


def test_loop_dispatches_search_logs_and_flows():
    # I1: the loop routes the new tools through the registry, not just query_metrics.
    logs = [{"device": "r1", "ts": 100 + i, "msg": f"bgp flap {i}"} for i in range(2)]
    flows = [{"device": "r1", "ts": 100 + i, "bytes": 1000 + i} for i in range(4)]
    llm = ScriptedLLM([
        tool_call("search_logs", {"device": "r1"}, id="c1"),
        tool_call("flows", {"device": "r1"}, id="c2"),
        final("r1 flapping [events:0] with a traffic spike [flows:0]"),
    ])
    out = investigate("what happened on r1?", WINDOW, llm=llm,
                      adapter=StubAdapter(events_rows=logs, flows_rows=flows), cfg=_cfg())
    assert out.stopped is None
    trs = out.of_type("tool_result")
    assert trs[0].data["name"] == "search_logs" and "[events:0]" in trs[0].data["content"]
    assert trs[1].data["name"] == "flows" and "[flows:0]" in trs[1].data["content"]


def test_ask_back_when_underspecified():
    # underspecified request -> clarifying question, no tool call (ADR-0005 ask-back).
    llm = ScriptedLLM([final("Which device or link should I look at?")])
    out = investigate("is the network ok?", WINDOW,
                      llm=llm, adapter=StubAdapter(metrics_rows=ROWS), cfg=_cfg())
    assert out.answer.startswith("Which device")
    assert out.of_type("tool_call") == ()
    assert [e.type for e in out.events] == ["user_msg", "assistant_msg"]


def test_tool_call_cap_stops_runaway():
    # every turn asks for another tool -> cap halts it, reports why.
    llm = ScriptedLLM([tool_call("query_metrics", {"device": "r1"}) for _ in range(5)])
    out = investigate("dig forever", WINDOW,
                      llm=llm, adapter=StubAdapter(metrics_rows=ROWS),
                      cfg=_cfg(tool_call_cap=2, step_cap=20))
    assert out.stopped == "tool_call_cap"
    assert len(out.of_type("tool_call")) == 2, "cap bounds tool invocations"


def test_step_cap_stops_runaway():
    llm = ScriptedLLM([tool_call("query_metrics", {"device": "r1"}) for _ in range(3)])
    out = investigate("dig forever", WINDOW,
                      llm=llm, adapter=StubAdapter(metrics_rows=ROWS),
                      cfg=_cfg(step_cap=3, tool_call_cap=99))
    assert out.stopped == "step_cap"
    assert len(llm.calls) == 3, "loop turns bounded by step_cap"


def test_filter_error_is_fed_back_as_observation():
    # over-broad tool args -> adapter rejects; the guidance returns as a tool_result
    # (ADR-0015) so the model can correct, then it answers.
    llm = ScriptedLLM([
        tool_call("query_metrics", {}, id="bad"),        # no device/pattern
        final("need to narrow down"),
    ])
    out = investigate("look", WINDOW,
                      llm=llm, adapter=StubAdapter(metrics_rows=ROWS), cfg=_cfg())
    tr = out.of_type("tool_result")[0]
    assert tr.data["content"].startswith("error:") and "device" in tr.data["content"]
    assert out.stopped is None


def test_think_event_emitted_with_reasoning():
    # native call carrying reasoning alongside it -> a `think` event precedes the call.
    llm = ScriptedLLM([
        Reply(content="checking r1 metrics first",
              tool_calls=(ToolCall("query_metrics", {"device": "r1"}, id="c1"),)),
        final("r1 cpu high [metrics:0]"),
    ])
    out = investigate("why slow?", WINDOW,
                      llm=llm, adapter=StubAdapter(metrics_rows=ROWS), cfg=_cfg())
    assert [e.type for e in out.events] == \
        ["user_msg", "think", "tool_call", "tool_result", "assistant_msg"]
    assert out.of_type("think")[0].data["content"] == "checking r1 metrics first"


def test_owned_parser_handles_non_native_toolcall():
    # backend without native fn-calling: the tool call arrives as JSON in content;
    # F3's owned parser turns it into a dispatch (ADR-0005).
    llm = ScriptedLLM([
        final('{"name": "query_metrics", "arguments": {"device": "r1"}}'),
        final("done [metrics:0]"),
    ])
    out = investigate("why slow?", WINDOW,
                      llm=llm, adapter=StubAdapter(metrics_rows=ROWS), cfg=_cfg())
    assert out.stopped is None
    assert [e.type for e in out.events] == \
        ["user_msg", "tool_call", "tool_result", "assistant_msg"]
    # plain prose must NOT be mistaken for a tool call
    assert parse_tool_calls("r1 cpu is high, no json here") == ()
    assert parse_tool_calls('{"name": "query_metrics", "arguments": {}}')[0].name \
        == "query_metrics"


def test_gate_passes_cited_sufficient_answer():
    # I4a: enough in-window, on-topic, cited evidence -> the answer flows through, no gate block.
    llm = ScriptedLLM([
        tool_call("query_metrics", {"device": "r1"}, id="c1"),
        final("r1 cpu pegged [metrics:0][metrics:1]"),
    ])
    out = investigate("why is r1 slow?", WINDOW,
                      llm=llm, adapter=StubAdapter(metrics_rows=ROWS), cfg=_cfg())
    assert out.of_type("gate") == (), "a passing answer emits no gate block"
    assert out.answer == "r1 cpu pegged [metrics:0][metrics:1]"


def test_gate_blocks_uncited_answer():
    # I4a acceptance: a device-anchored claim with no citation -> blocked, not answered.
    llm = ScriptedLLM([
        tool_call("query_metrics", {"device": "r1"}, id="c1"),
        final("r1 is on fire"),                           # claim, no [id]
    ])
    out = investigate("why is r1 slow?", WINDOW,
                      llm=llm, adapter=StubAdapter(metrics_rows=ROWS), cfg=_cfg())
    g = out.of_type("gate")
    assert len(g) == 1 and g[0].data["ok"] is False
    assert out.answer.startswith("cannot answer yet")
    assert any("uncited" in m for m in g[0].data["missing"])


def test_gate_blocks_off_topic_thin_evidence():
    # I4a acceptance: question about r1 but evidence only for r9 -> off-topic + thin -> blocked.
    llm = ScriptedLLM([
        tool_call("query_metrics", {"device": "r9"}, id="c1"),
        final("r1 looks fine [metrics:0]"),
    ])
    out = investigate("what is wrong with r1?", WINDOW, llm=llm,
                      adapter=StubAdapter(metrics_rows=[{"device": "r9", "ts": 150, "cpu": 10}]),
                      cfg=_cfg())
    g = out.of_type("gate")
    assert g and g[0].data["ok"] is False
    assert any("off-topic" in m for m in g[0].data["missing"])


def test_ask_back_bypasses_the_gate():
    # a clarifying question before any evidence is not an answer -> the gate must not block it.
    llm = ScriptedLLM([final("Which device or link should I look at?")])
    out = investigate("is the network ok?", WINDOW,
                      llm=llm, adapter=StubAdapter(metrics_rows=ROWS), cfg=_cfg())
    assert out.of_type("gate") == ()
    assert out.answer.startswith("Which device")


def test_bad_event_type_rejected():
    try:
        Event("not_a_real_type", {})
    except AssertionError:
        pass
    else:
        raise AssertionError("Event must reject non-canonical types")


def _run():
    test_scripted_investigation_completes_with_query_metrics()
    test_loop_dispatches_search_logs_and_flows()
    test_ask_back_when_underspecified()
    test_tool_call_cap_stops_runaway()
    test_step_cap_stops_runaway()
    test_filter_error_is_fed_back_as_observation()
    test_think_event_emitted_with_reasoning()
    test_owned_parser_handles_non_native_toolcall()
    test_gate_passes_cited_sufficient_answer()
    test_gate_blocks_uncited_answer()
    test_gate_blocks_off_topic_thin_evidence()
    test_ask_back_bypasses_the_gate()
    test_bad_event_type_rejected()
    print("copilot.agent self-check OK")


if __name__ == "__main__":
    _run()
