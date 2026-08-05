"""Assert-based tests / self-check for the agent loop core (F3).

Prior art: dataapi/check_dataset.py (assert + __main__, no framework).
Seam under test: investigate(question, window, *, llm, adapter, cfg) -> Outcome,
with the LLM client + tool adapter stubbed (spec #3 §Testing). No HTTP yet (F4).
Run:  python3 -m copilot.agent.test_agent
"""
import base64
import dataclasses
import os
import tempfile

import pytest

from copilot.adapter import StubAdapter
from copilot.agent import Event, Outcome, investigate, parse_tool_calls
from copilot.agent.loop import compact_history
from copilot.config import Config
from copilot.llm import Reply, ScriptedLLM, ToolCall, final, tool_call
from copilot.skills import Skill
from copilot.window import WindowContext
from copilot.workspace import Executor, for_session
from copilot.workspace.executor import _nonet_ok

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


def test_empty_or_failed_tool_call_does_not_burn_the_cap():
    # #58: a search that returns no rows (or errors) gathered nothing, so it must NOT spend
    # the tool-call budget -- otherwise wasted searches starve the model of budget to reach
    # citable evidence and it stops at tool_call_cap before a cited answer. Only the productive
    # read here (query_metrics -> rows) spends the single-call budget.
    llm = ScriptedLLM([
        tool_call("search_logs", {"device": "r1"}, id="c1"),     # no events_rows -> "no rows"
        tool_call("flows", {"device": "r1"}, id="c2"),           # no flows_rows -> "no rows"
        tool_call("query_metrics", {"device": "r1"}, id="c3"),   # rows -> the one productive call
        final("r1 cpu pegged [metrics:0]"),
        final('{"pass": true}'),                                  # stage-2 self-judge verdict
    ])
    out = investigate("what happened on r1?", WINDOW, llm=llm,
                      adapter=StubAdapter(metrics_rows=ROWS), cfg=_cfg(tool_call_cap=1))
    assert out.stopped is None, "empty reads are free -> the productive read still fits the cap"
    assert out.answer == "r1 cpu pegged [metrics:0]"
    assert len(out.of_type("tool_call")) == 3, "all three dispatched; only one spent the budget"


def test_dispatch_backstop_bounds_a_flood_of_empty_calls_in_one_turn():
    # #58 (review): empty calls are free, but a single native turn packing many of them must
    # still be bounded -- step_cap only limits turns, not calls-per-turn. The dispatch backstop
    # (step_cap * tool_call_cap) terminates instead of firing every call (real adapter I/O each).
    flood = Reply(tool_calls=tuple(
        ToolCall("flows", {"device": "r1"}, id=f"c{i}") for i in range(20)))   # all -> "no rows"
    out = investigate("what happened on r1?", WINDOW, llm=ScriptedLLM([flood]),
                      adapter=StubAdapter(), cfg=_cfg(step_cap=2, tool_call_cap=2))  # cap = 4
    assert out.stopped == "tool_call_cap"
    assert len(out.of_type("tool_call")) == 4, "backstop caps total dispatch at step_cap*tool_call_cap"


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


def _executor(tmp="/tmp/_b3a", sid="s", **kw):
    return Executor(for_session(tmp, sid), **kw)


# real-sandbox bash tests need unshare -n (like test_executor.py). skipif reports the skip
# honestly under pytest; _run() (the __main__ self-check) prints one instead of silently passing.
needs_nonet = pytest.mark.skipif(not _nonet_ok(), reason="unshare -n unavailable on this host")


def _skip_no_nonet() -> bool:
    if _nonet_ok():
        return False
    print("  (skipped bash exec test: unshare -n unavailable)")
    return True


def test_bash_tool_inert_without_an_executor():
    # B3a: no executor wired -> bash is not advertised, and a call to it is unknown-tool
    # guidance (dispatch), never a crash. Byte-identical to F3 when exec isn't wired.
    llm = ScriptedLLM([tool_call("bash", {"command": "echo hi"}, id="c1"),
                       final("ran it, anything else?")])
    out = investigate("run it", WINDOW, llm=llm, adapter=StubAdapter(), cfg=_cfg())
    tr = out.of_type("tool_result")[0]
    assert tr.data["name"] == "bash" and "unknown tool" in tr.data["content"]


@needs_nonet
def test_bash_tool_runs_code_and_result_reaches_the_loop():
    # B3a acceptance: the agent runs code in its scratchpad and reads the result through the
    # loop. Real executor (no doubles).
    if _skip_no_nonet():
        return
    marker = "MARKER-7f3a"
    llm = ScriptedLLM([
        tool_call("bash", {"command": f"echo '{marker}'"}, id="c1"),
        final(f"printed {marker}, continue?"),
    ])
    out = investigate("run the script", WINDOW, llm=llm, adapter=StubAdapter(),
                      cfg=_cfg(), executor=_executor(sid="run"))
    tr = out.of_type("tool_result")[0]
    assert tr.data["name"] == "bash"
    assert "exit=0" in tr.data["content"] and marker in tr.data["content"]
    assert tr.data["n"] == 0, "bash output is action, not cited evidence"


@needs_nonet
def test_bash_no_net_bites_through_the_loop():
    # B3a acceptance: no-net still enforced via the loop -- a real connect() from executed code
    # fails, so the observation shows a nonzero exit.
    if _skip_no_nonet():
        return
    net = ScriptedLLM([
        tool_call("bash", {"command":
                  "python3 -c \"import socket; socket.create_connection(('1.1.1.1',80),3)\""},
                  id="c1"),
        final("net check done, ok?"),
    ])
    out = investigate("try the net", WINDOW, llm=net, adapter=StubAdapter(),
                      cfg=_cfg(), executor=_executor(sid="net"))
    assert "exit=0" not in out.of_type("tool_result")[0].data["content"]


@needs_nonet
def test_bash_timeout_bites_through_the_loop():
    # B3a acceptance: timeout still enforced via the loop -- a slow command under a 1s executor
    # cap is killed and flagged.
    if _skip_no_nonet():
        return
    slow = ScriptedLLM([tool_call("bash", {"command": "sleep 10"}, id="c1"),
                        final("slept, next?")])
    out = investigate("sleep", WINDOW, llm=slow, adapter=StubAdapter(), cfg=_cfg(),
                      executor=_executor(sid="slow", timeout_s=1, max_timeout_s=2))
    assert "timed out" in out.of_type("tool_result")[0].data["content"]


def test_present_snapshots_a_file_and_emits_an_artifact_event():
    # B3b acceptance: presenting a scratchpad file snapshots it into artifacts/ and emits an
    # `artifact` event carrying the inline payload a demo renders (kind chart -> base64). No
    # executor needed -- present is a file op, not exec.
    ws = for_session(tempfile.mkdtemp(), "s")
    chart = os.path.join(ws.scratchpad, "cpu.png")
    with open(chart, "wb") as f:
        f.write(b"PNG-DATA")
    llm = ScriptedLLM([tool_call("present", {"path": "cpu.png", "title": "cpu"}, id="c1"),
                       final("shown the chart, anything else?")])   # ask-back -> gate bypassed
    out = investigate("show the chart", WINDOW, llm=llm, adapter=StubAdapter(), cfg=_cfg(),
                      workspace=ws)
    art = out.of_type("artifact")[0].data
    assert art["kind"] == "chart" and art["mime"] == "image/png" and art["title"] == "cpu"
    assert art["content_b64"] == base64.b64encode(b"PNG-DATA").decode(), "inline payload for render"
    assert art["path"] == "artifacts/0000-cpu.png"
    assert os.path.isfile(os.path.join(ws.artifacts, "0000-cpu.png")), "snapshot copied to disk"
    tr = out.of_type("tool_result")[0]
    assert tr.data["name"] == "present" and "presented" in tr.data["content"] and tr.data["n"] == 0


def test_present_snapshot_is_frozen_against_a_later_overwrite():
    # B3b acceptance: overwriting the presented file does NOT change the already-shown artifact --
    # two presents of the same path yield two distinct, independently-frozen snapshots.
    ws = for_session(tempfile.mkdtemp(), "s")
    p = os.path.join(ws.scratchpad, "out.svg")
    with open(p, "w") as f:
        f.write("<svg>v1</svg>")
    first = investigate("show v1", WINDOW, llm=ScriptedLLM(
        [tool_call("present", {"path": "out.svg"}, id="c1"), final("shown v1, ok?")]),
        adapter=StubAdapter(), cfg=_cfg(), workspace=ws).of_type("artifact")[0].data
    with open(p, "w") as f:
        f.write("<svg>v2-DIFFERENT</svg>")               # overwrite the source AFTER presenting
    second = investigate("show v2", WINDOW, llm=ScriptedLLM(
        [tool_call("present", {"path": "out.svg"}, id="c1"), final("shown v2, ok?")]),
        adapter=StubAdapter(), cfg=_cfg(), workspace=ws).of_type("artifact")[0].data
    assert first["path"] == "artifacts/0000-out.svg" and second["path"] == "artifacts/0001-out.svg"
    assert first["kind"] == "chart" and first["mime"] == "image/svg+xml"   # svg renders as an image
    assert open(os.path.join(ws.artifacts, "0000-out.svg")).read() == "<svg>v1</svg>", \
        "the first artifact is unchanged by the overwrite"
    assert first["content_b64"] == base64.b64encode(b"<svg>v1</svg>").decode(), "first payload frozen"
    assert second["content_b64"] == base64.b64encode(b"<svg>v2-DIFFERENT</svg>").decode(), \
        "the second present captured the new bytes"


@needs_nonet
def test_bash_generates_then_present_snapshots_it_end_to_end():
    # B3b acceptance #2 (backend): the one novel chain -- the agent GENERATES a file with bash
    # (B3a) then presents it, and the generated bytes reach the artifact event. Real executor +
    # workspace (no doubles); skips where unshare is absent.
    if _skip_no_nonet():
        return
    ws = for_session(tempfile.mkdtemp(), "s")
    llm = ScriptedLLM([
        tool_call("bash", {"command": "printf 'GEN-CHART' > plot.png"}, id="c1"),
        tool_call("present", {"path": "plot.png", "title": "generated"}, id="c2"),
        final("generated and shown the chart, ok?"),
    ])
    out = investigate("make a chart", WINDOW, llm=llm, adapter=StubAdapter(), cfg=_cfg(),
                      executor=Executor(ws), workspace=ws)
    art = out.of_type("artifact")[0].data
    assert art["kind"] == "chart" and art["path"] == "artifacts/0000-plot.png"
    assert art["content_b64"] == base64.b64encode(b"GEN-CHART").decode(), "generated bytes reach the event"


def test_present_inert_without_a_workspace():
    # no workspace wired -> present is not advertised, and a call to it is unknown-tool guidance
    # (dispatch), never a crash + no artifact event. Byte-identical to F3 when no workspace.
    llm = ScriptedLLM([tool_call("present", {"path": "x.png"}, id="c1"),
                       final("nothing to show, ok?")])
    out = investigate("show x", WINDOW, llm=llm, adapter=StubAdapter(), cfg=_cfg())
    tr = out.of_type("tool_result")[0]
    assert tr.data["name"] == "present" and "unknown tool" in tr.data["content"]
    assert not out.of_type("artifact"), "no artifact event without a workspace"


def test_present_missing_file_is_guidance_no_artifact():
    # a present of a nonexistent file comes back AS guidance (ADR-0015), no artifact event.
    ws = for_session(tempfile.mkdtemp(), "s")
    llm = ScriptedLLM([tool_call("present", {"path": "nope.png"}, id="c1"),
                       final("could not show it, ok?")])
    out = investigate("show it", WINDOW, llm=llm, adapter=StubAdapter(), cfg=_cfg(), workspace=ws)
    assert out.of_type("tool_result")[0].data["content"].startswith("error:")
    assert not out.of_type("artifact"), "a failed present emits no artifact event"


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


def test_fault_type_steers_skill_selection():
    # R4a (ADR-0012): the Prediction Record's fault_type reaches the base prompt as a soft steer
    # for skill selection -- present with a prediction, absent without (no rigid mapping).
    skills = {"congestion": Skill("congestion", "the congestion playbook", "M"),
              "bgp_flap": Skill("bgp_flap", "the bgp-flap playbook", "M")}
    llm = ScriptedLLM([final("which device?")])
    investigate("look into it", WINDOW, llm=llm, adapter=StubAdapter(), cfg=_cfg(),
                skills=skills, fault_type="congestion")
    system = llm.calls[0][0][0]["content"]
    assert "'congestion'" in system and "prefer the diagnostic skill" in system
    llm2 = ScriptedLLM([final("which device?")])            # no fault_type -> no hint
    investigate("look into it", WINDOW, llm=llm2, adapter=StubAdapter(), cfg=_cfg(), skills=skills)
    assert "prefer the diagnostic skill" not in llm2.calls[0][0][0]["content"]


def test_abstain_prediction_softens_the_loop_gate():
    # R4a (ADR-0008 §Nuances): the SAME thin (1-item) cited answer blocks normally, but when the
    # PA abstained it passes as "anomalous, no confident call". Softening is SUFFICIENCY-only:
    # stage-2 self_judge still runs, and a CONTRADICTION verdict still blocks even under abstain.
    one = [{"device": "r1", "ts": 150, "cpu": 99}]
    passed = investigate("why is r1 slow?", WINDOW, adapter=StubAdapter(metrics_rows=one),
        cfg=_cfg(), abstain=True, llm=ScriptedLLM([
            tool_call("query_metrics", {"device": "r1"}, id="c1"),
            final("r1 anomalous, no confident call [metrics:0]"),
            final('{"pass": true}')]))                     # self_judge still consulted
    assert passed.answer.startswith("r1 anomalous")
    assert passed.of_type("gate")[0].data["ok"] is True
    blocked = investigate("why is r1 slow?", WINDOW, adapter=StubAdapter(metrics_rows=one),
        cfg=_cfg(gate_max_retries=0), llm=ScriptedLLM([
            tool_call("query_metrics", {"device": "r1"}, id="c1"),
            final("r1 anomalous, no confident call [metrics:0]")]))
    assert blocked.answer.startswith("cannot answer") and "thin evidence" in blocked.answer
    # abstain softens thin evidence but NOT self-contradiction (integrity kept).
    contra = investigate("why is r1 slow?", WINDOW, adapter=StubAdapter(metrics_rows=one),
        cfg=_cfg(gate_max_retries=0), abstain=True, llm=ScriptedLLM([
            tool_call("query_metrics", {"device": "r1"}, id="c1"),
            final("r1 anomalous, no confident call [metrics:0]"),
            final('{"pass": false, "contradictions": ["cpu is both 99 and idle"]}')]))
    assert contra.answer.startswith("cannot answer") and "contradiction" in contra.answer


def test_drift_state_flags_the_answer_but_does_not_block():
    # T1 / story 14: a degraded model-health rung (>= cfg.drift_distrust_at) prepends a distrust
    # banner to the SAME answer that a healthy rung leaves untouched; the answer still returns.
    script = lambda: ScriptedLLM([
        tool_call("query_metrics", {"device": "r1", "limit": 5}, id="c1"),
        final("r1 cpu is pegged [metrics:0]"),
        final('{"pass": true}')])
    base = "r1 cpu is pegged [metrics:0]"
    healthy = investigate("why is r1 slow?", WINDOW, llm=script(),
                          adapter=StubAdapter(metrics_rows=ROWS), cfg=_cfg(), drift_state="R0")
    assert healthy.answer == base                        # healthy -> unchanged (regression)
    drifted = investigate("why is r1 slow?", WINDOW, llm=script(),
                          adapter=StubAdapter(metrics_rows=ROWS), cfg=_cfg(), drift_state="R4")
    assert drifted.answer.endswith(base)                 # the real answer is preserved
    assert drifted.answer != base and "low trust" in drifted.answer   # flagged on top
    assert drifted.of_type("gate")[-1].data["ok"] is True             # it PASSED, wasn't blocked


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


def test_compact_history_small_history_unchanged():
    # I6/ADR-0015 §5: under budget -> returned as-is (no note, byte-identical).
    h = [{"role": "user", "content": "q1"}, {"role": "assistant", "content": "a1 [metrics:0]"}]
    assert compact_history(h, max_chars=1000) == h


def test_compact_history_bounds_a_long_session():
    # a long session collapses old turns into one leading note; total stays under the bound.
    h = [{"role": "user" if i % 2 == 0 else "assistant",
          "content": f"turn {i} " + "x" * 200} for i in range(40)]
    out = compact_history(h, max_chars=1200)
    assert sum(len(m["content"]) for m in out) <= 1200
    assert len(out) < len(h)                              # something was collapsed
    assert out[0]["content"].startswith("Investigation so far")   # the digest note leads


def test_compact_history_bound_holds_with_many_cites():
    # regression (review): cites reserved room inside the cap, not appended past it -> note <= budget.
    h = [{"role": "assistant", "content": f"finding {i} [metrics:{i}] [events:{i}]"} for i in range(15)]
    h += [{"role": "user" if i % 2 else "assistant", "content": "w" * 250} for i in range(20)]
    out = compact_history(h, max_chars=1500)
    assert sum(len(m["content"]) for m in out) <= 1500
    # every cite from the dropped turns still present despite the tight budget
    for i in range(15):
        assert f"[metrics:{i}]" in out[0]["content"]


def test_compact_history_never_drops_a_cited_evidence_id():
    # acceptance: a cite id in a dropped turn survives in the digest note.
    h = [{"role": "assistant", "content": "old finding [events:7]"}]
    h += [{"role": "user" if i % 2 else "assistant", "content": "z" * 300} for i in range(20)]
    out = compact_history(h, max_chars=900)
    assert "[events:7]" in out[0]["content"], "dropped cite id must be preserved in the note"


def test_compaction_flag_on_bounds_the_prompt_the_model_sees():
    # integration: with the flag on, a huge resumed history is compacted before the model call.
    history = [{"role": "user" if i % 2 == 0 else "assistant",
                "content": f"turn {i} " + "y" * 200} for i in range(40)]
    llm = ScriptedLLM([final("which device?")])          # ask-back, one turn, no tools
    investigate("and now?", WINDOW, llm=llm, adapter=StubAdapter(),
                cfg=_cfg(history_compaction=True, history_max_chars=1500), history=history)
    sent = llm.calls[0][0]
    hist_chars = sum(len(m["content"]) for m in sent[1:-1])   # between system and new question
    assert hist_chars <= 1500 < sum(len(m["content"]) for m in history)


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
    test_empty_or_failed_tool_call_does_not_burn_the_cap()
    test_dispatch_backstop_bounds_a_flood_of_empty_calls_in_one_turn()
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
    test_fault_type_steers_skill_selection()
    test_abstain_prediction_softens_the_loop_gate()
    test_drift_state_flags_the_answer_but_does_not_block()
    test_history_prior_turns_reach_the_model()
    test_no_history_is_a_fresh_single_turn()
    test_compact_history_small_history_unchanged()
    test_compact_history_bounds_a_long_session()
    test_compact_history_bound_holds_with_many_cites()
    test_compact_history_never_drops_a_cited_evidence_id()
    test_compaction_flag_on_bounds_the_prompt_the_model_sees()
    test_bad_event_type_rejected()
    test_bash_tool_inert_without_an_executor()
    test_bash_tool_runs_code_and_result_reaches_the_loop()
    test_bash_no_net_bites_through_the_loop()
    test_bash_timeout_bites_through_the_loop()
    test_present_snapshots_a_file_and_emits_an_artifact_event()
    test_present_snapshot_is_frozen_against_a_later_overwrite()
    test_bash_generates_then_present_snapshots_it_end_to_end()
    test_present_inert_without_a_workspace()
    test_present_missing_file_is_guidance_no_artifact()
    print("copilot.agent self-check OK")


if __name__ == "__main__":
    _run()
