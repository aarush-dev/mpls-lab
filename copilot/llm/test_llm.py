"""Assert-based tests / self-check for the LLM seam (F1).

Prior art: dataapi/check_dataset.py (assert + __main__, no framework).
Run:  python3 -m copilot.llm.test_llm
"""
from copilot.llm import LLMClient, ScriptedLLM, tool_call, final


def test_scripted_sequence_returns_toolcalls_then_final():
    llm = ScriptedLLM([
        tool_call("query_metrics", {"device": "r1"}, id="c1"),
        final("latency high on r1 [ev:1]"),
    ])
    r1 = llm.chat([{"role": "user", "content": "why slow?"}],
                  tools=[{"name": "query_metrics"}])
    assert r1.tool_calls and r1.tool_calls[0].name == "query_metrics"
    assert r1.tool_calls[0].arguments == {"device": "r1"}
    assert r1.tool_calls[0].id == "c1"
    assert r1.content is None

    r2 = llm.chat([{"role": "user", "content": "x"}])
    assert r2.tool_calls == () and r2.content == "latency high on r1 [ev:1]"


def test_records_calls_for_assertions():
    # stub-boundary use: the loop's calls must be inspectable in tests
    llm = ScriptedLLM([final("ok")])
    llm.chat([{"role": "user", "content": "hi"}], tools=[{"name": "t"}])
    assert len(llm.calls) == 1
    msgs, tools = llm.calls[0]
    assert msgs == [{"role": "user", "content": "hi"}]
    assert tools == [{"name": "t"}]


def test_exhausted_script_raises():
    llm = ScriptedLLM([final("only")])
    llm.chat([])
    raised = False
    try:
        llm.chat([])
    except AssertionError as e:
        raised = True
        assert "exhausted" in str(e)
    assert raised, "expected exhaustion error on over-run script"


def test_stub_satisfies_protocol():
    # config-only swap relies on the interface being satisfied structurally
    assert isinstance(ScriptedLLM([]), LLMClient)


def _run():
    test_scripted_sequence_returns_toolcalls_then_final()
    test_records_calls_for_assertions()
    test_exhausted_script_raises()
    test_stub_satisfies_protocol()
    print("copilot.llm self-check OK")


if __name__ == "__main__":
    _run()
