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
from copilot.skills import Skill
from copilot.window import WindowContext

WINDOW = WindowContext(100, 200)
ROWS = [{"device": "r1", "ts": 100 + i, "cpu": 90 + i} for i in range(3)]


def _cfg(**kw):
    return dataclasses.replace(Config(), **kw)


def test_scripted_investigation_completes_with_query_metrics():
    # native function-calling path: a tool call, then a cited answer.
    llm = ScriptedLLM([
        tool_call("query_metrics", {"device": "r1", "limit": 5}, id="c1"),
        final("r1 cpu is pegged [metrics:0]"),
        final('{"pass": true}'),                          # stage-2 self-judge verdict (I4b)
    ])
    out = investigate("why is r1 slow?", WINDOW,
                      llm=llm, adapter=StubAdapter(metrics_rows=ROWS), cfg=_cfg())
    assert isinstance(out, Outcome)
    assert out.answer == "r1 cpu is pegged [metrics:0]"
    assert out.stopped is None
    assert [e.type for e in out.events] == \
        ["user_msg", "tool_call", "tool_result", "gate", "assistant_msg"]
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
        final('{"pass": true}'),                          # stage-2 self-judge verdict (I4b)
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
    # gate_max_retries=0: keep this focused on the ADR-0015 observation feedback, not the retry.
    out = investigate("look", WINDOW, llm=llm,
                      adapter=StubAdapter(metrics_rows=ROWS), cfg=_cfg(gate_max_retries=0))
    tr = out.of_type("tool_result")[0]
    assert tr.data["content"].startswith("error:") and "device" in tr.data["content"]
    assert out.stopped is None


def test_think_event_emitted_with_reasoning():
    # native call carrying reasoning alongside it -> a `think` event precedes the call.
    llm = ScriptedLLM([
        Reply(content="checking r1 metrics first",
              tool_calls=(ToolCall("query_metrics", {"device": "r1"}, id="c1"),)),
        final("r1 cpu high [metrics:0]"),
        final('{"pass": true}'),                          # stage-2 self-judge verdict (I4b)
    ])
    out = investigate("why slow?", WINDOW,
                      llm=llm, adapter=StubAdapter(metrics_rows=ROWS), cfg=_cfg())
    assert [e.type for e in out.events] == \
        ["user_msg", "think", "tool_call", "tool_result", "gate", "assistant_msg"]
    assert out.of_type("think")[0].data["content"] == "checking r1 metrics first"


def test_owned_parser_handles_non_native_toolcall():
    # backend without native fn-calling: the tool call arrives as JSON in content;
    # F3's owned parser turns it into a dispatch (ADR-0005).
    llm = ScriptedLLM([
        final('{"name": "query_metrics", "arguments": {"device": "r1"}}'),
        final("done [metrics:0]"),
        final('{"pass": true}'),                          # stage-2 self-judge verdict (I4b)
    ])
    out = investigate("why slow?", WINDOW,
                      llm=llm, adapter=StubAdapter(metrics_rows=ROWS), cfg=_cfg())
    assert out.stopped is None
    assert [e.type for e in out.events] == \
        ["user_msg", "tool_call", "tool_result", "gate", "assistant_msg"]
    # plain prose must NOT be mistaken for a tool call
    assert parse_tool_calls("r1 cpu is high, no json here") == ()
    assert parse_tool_calls('{"name": "query_metrics", "arguments": {}}')[0].name \
        == "query_metrics"


def test_gate_passes_cited_sufficient_answer():
    # I4a: enough in-window, on-topic, cited evidence -> the answer flows through. R2a: a
    # passing gate is now itself a visible event (ok=True), emitted before the answer.
    llm = ScriptedLLM([
        tool_call("query_metrics", {"device": "r1"}, id="c1"),
        final("r1 cpu pegged [metrics:0][metrics:1]"),
        final('{"pass": true}'),                          # stage-2 self-judge verdict (I4b)
    ])
    out = investigate("why is r1 slow?", WINDOW,
                      llm=llm, adapter=StubAdapter(metrics_rows=ROWS), cfg=_cfg())
    g = out.of_type("gate")
    assert len(g) == 1 and g[0].data["ok"] is True, "a passing answer emits a gate ok=True event"
    assert out.answer == "r1 cpu pegged [metrics:0][metrics:1]"


def test_gate_blocks_uncited_answer():
    # I4a acceptance: a device-anchored claim with no citation -> blocked, not answered.
    llm = ScriptedLLM([
        tool_call("query_metrics", {"device": "r1"}, id="c1"),
        final("r1 is on fire"),                           # claim, no [id]
    ])
    # gate_max_retries=0 -> a stage-1 block returns immediately (no retry); I4b retry tested below.
    out = investigate("why is r1 slow?", WINDOW, llm=llm,
                      adapter=StubAdapter(metrics_rows=ROWS), cfg=_cfg(gate_max_retries=0))
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
                      cfg=_cfg(gate_max_retries=0))
    g = out.of_type("gate")
    assert g and g[0].data["ok"] is False
    assert any("off-topic" in m for m in g[0].data["missing"])


def test_gate_blocks_on_a_failed_tool_call():
    # ADR-0008 check 1: a tool call that errored -> the answer is blocked even with otherwise
    # sufficient, cited evidence (I4b's retry re-fetches the failed call).
    llm = ScriptedLLM([
        tool_call("query_metrics", {}, id="bad"),        # over-broad -> FilterError guidance
        tool_call("query_metrics", {"device": "r1"}, id="c2"),
        final("r1 cpu pegged [metrics:0][metrics:1]"),
    ])
    out = investigate("why is r1 slow?", WINDOW, llm=llm,
                      adapter=StubAdapter(metrics_rows=ROWS), cfg=_cfg(gate_max_retries=0))
    g = out.of_type("gate")
    assert g and g[0].data["ok"] is False
    assert any("failed tool call" in m for m in g[0].data["missing"])


def test_ask_back_bypasses_the_gate():
    # a clarifying question before any evidence is not an answer -> the gate must not block it.
    llm = ScriptedLLM([final("Which device or link should I look at?")])
    out = investigate("is the network ok?", WINDOW,
                      llm=llm, adapter=StubAdapter(metrics_rows=ROWS), cfg=_cfg())
    assert out.of_type("gate") == ()
    assert out.answer.startswith("Which device")


def test_self_judge_parses_verdict():
    # I4b stage-2 seam: pass/fail JSON verdicts parse; contradictions fold into missing[].
    from copilot.agent.loop import self_judge
    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "why is r1 slow?"}]
    assert self_judge(ScriptedLLM([final('{"pass": true}')]), msgs, "a").ok
    r = self_judge(ScriptedLLM([final('{"pass": false, "missing": ["r1 logs"], '
                                       '"contradictions": ["cpu vs flows"]}')]), msgs, "a")
    assert not r.ok
    assert any("r1 logs" in m for m in r.missing) and any("contradiction" in m for m in r.missing)
    # junk verdict OR an omitted "pass" key -> fail-open (deterministic gate is the hard guarantee)
    assert self_judge(ScriptedLLM([final("not json at all")]), msgs, "a").ok
    assert self_judge(ScriptedLLM([final('{"missing": ["x"]}')]), msgs, "a").ok


def test_self_judge_fail_retries_then_answers():
    # I4b acceptance: stage-1 passes but the self-judge says thin -> a retry fetches more
    # evidence -> the judge then passes -> the answer flows out.
    logs = [{"device": "r1", "ts": 100 + i, "msg": f"bgp {i}"} for i in range(2)]
    llm = ScriptedLLM([
        tool_call("query_metrics", {"device": "r1"}, id="c1"),
        final("r1 cpu pegged [metrics:0][metrics:1]"),
        final('{"pass": false, "missing": ["r1 bgp logs"]}'),   # judge: fetch more
        tool_call("search_logs", {"device": "r1"}, id="c2"),    # retry fetches it
        final("r1 cpu pegged [metrics:0] and bgp flapping [events:0]"),
        final('{"pass": true}'),                                # judge: now sufficient
    ])
    out = investigate("why is r1 slow?", WINDOW, llm=llm,
                      adapter=StubAdapter(metrics_rows=ROWS, events_rows=logs), cfg=_cfg())
    assert out.stopped is None
    assert out.answer == "r1 cpu pegged [metrics:0] and bgp flapping [events:0]"
    fails = [e for e in out.of_type("gate") if e.data["ok"] is False]
    assert len(fails) == 1 and fails[0].data["retry"] == 0, "exactly one retry fired"
    assert any("r1 bgp logs" in m for m in fails[0].data["missing"])
    assert [e.data["ok"] for e in out.of_type("gate")] == [False, True], "then a passing gate"


def test_gate_retry_recovers_from_a_failed_tool_call():
    # I4b: a failed tool call blocks round 1; the retry re-issues it successfully -> answers.
    # Guards the tool_errors-cleared-across-retries fix: without it the stale error blocks every
    # retry to the cap and the answer is always "cannot answer yet".
    llm = ScriptedLLM([
        tool_call("query_metrics", {}, id="bad"),          # over-broad -> FilterError guidance
        final("r1 cpu pegged [metrics:0][metrics:1]"),     # premature answer, the call had failed
        tool_call("query_metrics", {"device": "r1"}, id="c2"),   # retry re-issues it, now valid
        final("r1 cpu pegged [metrics:0][metrics:1]"),
        final('{"pass": true}'),                           # judge passes round 2
    ])
    out = investigate("why is r1 slow?", WINDOW, llm=llm,
                      adapter=StubAdapter(metrics_rows=ROWS), cfg=_cfg())
    assert out.stopped is None
    assert out.answer == "r1 cpu pegged [metrics:0][metrics:1]"
    fails = [e for e in out.of_type("gate") if e.data["ok"] is False]
    assert len(fails) == 1 and any("failed tool call" in m for m in fails[0].data["missing"])


def test_gate_retry_respects_the_cap():
    # I4b acceptance: the judge never passes -> retries stop at gate_max_retries, then the
    # missing[] list is reported instead of a forced answer.
    script = []
    for _ in range(3):                                          # 1 initial + 2 retries = 3 rounds
        script += [tool_call("query_metrics", {"device": "r1"}, id="c"),
                   final("r1 cpu pegged [metrics:0][metrics:1]"),
                   final('{"pass": false, "missing": ["more"]}')]
    out = investigate("why is r1 slow?", WINDOW, llm=ScriptedLLM(script),
                      adapter=StubAdapter(metrics_rows=ROWS), cfg=_cfg(gate_max_retries=2))
    assert out.stopped is None
    assert out.answer.startswith("cannot answer yet")
    g = out.of_type("gate")                                     # retry0, retry1, final block
    assert len(g) == 3 and [e.data["retry"] for e in g] == [0, 1, 2]


def test_bad_event_type_rejected():
    try:
        Event("not_a_real_type", {})
    except AssertionError:
        pass
    else:
        raise AssertionError("Event must reject non-canonical types")


def test_skill_descriptions_sit_in_prompt():
    # I5 (ADR-0012): name+description of every skill sit in the base prompt; the BODY does
    # not (progressive disclosure); the load_skill tool is advertised so the agent can pull one.
    skills = {"bgp_flap": Skill("bgp_flap", "How to chase a flapping BGP session.",
                                "1. pull the session logs")}
    llm = ScriptedLLM([final("which device?")])          # ask-back -> one call, gate bypassed
    investigate("look into it", WINDOW, llm=llm, adapter=StubAdapter(), cfg=_cfg(),
                skills=skills)
    system, tools = llm.calls[0][0][0]["content"], llm.calls[0][1]
    assert "bgp_flap" in system and "How to chase a flapping BGP session." in system
    assert "1. pull the session logs" not in system, "body must not sit in the base prompt"
    assert any(t["name"] == "load_skill" for t in tools), "load_skill advertised with skills"


def test_no_skills_leaves_prompt_and_tools_unchanged():
    # backward-compat: skills default None -> no catalog, no load_skill tool.
    llm = ScriptedLLM([final("which device?")])
    investigate("q", WINDOW, llm=llm, adapter=StubAdapter(), cfg=_cfg())
    system, tools = llm.calls[0][0][0]["content"], llm.calls[0][1]
    assert "Diagnostic skills" not in system
    assert not any(t["name"] == "load_skill" for t in tools)


def test_manual_invoke_loads_skill_body():
    # I5: a human invokes a named skill -> its BODY is preloaded into the prompt.
    skills = {"bgp_flap": Skill("bgp_flap", "chase a bgp flap", "STEP: pull the session logs")}
    llm = ScriptedLLM([final("which device?")])
    investigate("help", WINDOW, llm=llm, adapter=StubAdapter(), cfg=_cfg(),
                skills=skills, invoke=["bgp_flap"])
    system = llm.calls[0][0][0]["content"]
    assert "STEP: pull the session logs" in system, "invoked skill body loads into context"


def test_agent_loads_skill_body_via_tool():
    # I5: the agent auto-selects by description -> load_skill(name) returns the body as an
    # observation (method, no cites); then it gathers evidence + answers.
    skills = {"bgp_flap": Skill("bgp_flap", "chase a bgp flap", "STEP: pull the session logs")}
    logs = [{"device": "r1", "ts": 100 + i, "msg": f"bgp flap {i}"} for i in range(2)]
    llm = ScriptedLLM([
        tool_call("load_skill", {"name": "bgp_flap"}, id="s1"),
        tool_call("search_logs", {"device": "r1"}, id="c1"),
        final("r1 bgp flapping [events:0] [events:1]"),
        final('{"pass": true}'),                          # stage-2 self-judge verdict (I4b)
    ])
    out = investigate("why is r1 down?", WINDOW, llm=llm,
                      adapter=StubAdapter(events_rows=logs), cfg=_cfg(), skills=skills)
    assert out.stopped is None
    tr = out.of_type("tool_result")[0]
    assert tr.data["name"] == "load_skill"
    assert tr.data["content"] == "STEP: pull the session logs"
    assert tr.data["n"] == 0, "a skill body is method, not cited evidence"
    assert out.answer.startswith("r1 bgp flapping")


def test_history_prior_turns_reach_the_model():
    # R2a: a resumed session threads prior turns between the system prompt and the new
    # question, so the model actually sees where the chat left off (multi-turn loop entry).
    history = [{"role": "user", "content": "why is r1 slow?"},
               {"role": "assistant", "content": "r1 cpu pegged [metrics:0]"}]
    llm = ScriptedLLM([final("still pegged, same cause as before?")])   # ask-back, one turn
    investigate("and now?", WINDOW, llm=llm, adapter=StubAdapter(metrics_rows=ROWS),
                cfg=_cfg(), history=history)
    sent = llm.calls[0][0]                                  # the messages list of the first call
    assert sent[0]["role"] == "system"
    assert sent[1:3] == history, "prior turns sit between system and the new question"
    assert sent[3] == {"role": "user", "content": "and now?"}, "new question comes last"


def test_no_history_is_a_fresh_single_turn():
    # backward-compat: history default None -> system + the one question, unchanged from F3.
    llm = ScriptedLLM([final("which device?")])
    investigate("q", WINDOW, llm=llm, adapter=StubAdapter(), cfg=_cfg())
    sent = llm.calls[0][0]
    assert [m["role"] for m in sent] == ["system", "user"]


def test_load_skill_unknown_is_guidance():
    # a bad skill name comes back AS guidance (ADR-0015), never a raise.
    skills = {"bgp_flap": Skill("bgp_flap", "d", "b")}
    llm = ScriptedLLM([
        tool_call("load_skill", {"name": "nope"}, id="s1"),
        final("which device?"),
    ])
    out = investigate("q", WINDOW, llm=llm, adapter=StubAdapter(), cfg=_cfg(), skills=skills)
    assert out.of_type("tool_result")[0].data["content"].startswith("error:")


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
    test_gate_blocks_on_a_failed_tool_call()
    test_ask_back_bypasses_the_gate()
    test_self_judge_parses_verdict()
    test_self_judge_fail_retries_then_answers()
    test_gate_retry_recovers_from_a_failed_tool_call()
    test_gate_retry_respects_the_cap()
    test_skill_descriptions_sit_in_prompt()
    test_no_skills_leaves_prompt_and_tools_unchanged()
    test_manual_invoke_loads_skill_body()
    test_agent_loads_skill_body_via_tool()
    test_load_skill_unknown_is_guidance()
    test_history_prior_turns_reach_the_model()
    test_no_history_is_a_fresh_single_turn()
    test_bad_event_type_rejected()
    print("copilot.agent self-check OK")


if __name__ == "__main__":
    _run()
