"""Assert-based tests / self-check for the real LLM client + the loop fixes it forces (R1).

Prior art: dataapi/check_dataset.py (assert + __main__, no framework). Covers the R1 acceptance:
a native tool-call round trip accepted by a FAKE OpenAI-shaped server (assistant `tool_calls`
preserved, `tool` reply matched), config-only profile swap, one wrapped tool-spec shape from one
place, `parse_tool_calls` not misreading quoted JSON in prose, and the three recorded risks.

Run:        python3 -m copilot.llm.test_http
Live smoke: COPILOT_LLM_SMOKE=1 python3 -m copilot.llm.test_http   (needs a running endpoint)
"""
import dataclasses
import json
import types

import httpx

from copilot.adapter import StubAdapter
from copilot.agent import investigate, parse_tool_calls
from copilot.config import Config
from copilot.llm import OpenAIClient, ScriptedLLM, final, make_client
from copilot.llm.http import _as_function, _to_reply
from copilot.window import WindowContext

WINDOW = WindowContext(100, 200)
ROWS = [{"device": "r1", "ts": 100 + i, "cpu": 90 + i} for i in range(3)]


def _cfg(**kw):
    return dataclasses.replace(Config(), **kw)


class _FakeResp:
    def __init__(self, payload):
        self._p = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._p


class _FakeServer:
    """A fake OpenAI-shaped /chat/completions endpoint: records every request body and returns
    scripted assistant messages in order -- the wire double the R1 acceptance calls for."""

    def __init__(self, messages):
        self._msgs = list(messages)
        self.requests = []
        self._i = 0

    def post(self, url, json=None, headers=None, timeout=None):
        self.requests.append(json)
        msg = self._msgs[self._i]
        self._i += 1
        return _FakeResp({"choices": [{"message": msg}]})


def _with_fake_server(server, fn):
    saved = httpx.post
    httpx.post = server.post
    try:
        return fn()
    finally:
        httpx.post = saved


def test_native_round_trip_preserves_tool_calls():
    # THE acceptance: a native call then a cited answer, driven end-to-end through the real
    # client + the loop. The second request must carry the assistant `tool_calls` AND a `tool`
    # message matching its id -- the R1 hard-break fix (loop was dropping tool_calls).
    server = _FakeServer([
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "c1", "type": "function",
             "function": {"name": "query_metrics", "arguments": '{"device": "r1"}'}}]},
        {"role": "assistant", "content": "r1 cpu pegged [metrics:0][metrics:1]"},
        {"role": "assistant", "content": '{"pass": true}'},          # self-judge verdict
    ])
    client = OpenAIClient("http://fake/v1", "gpt-oss-20b")
    out = _with_fake_server(server, lambda: investigate(
        "why is r1 slow?", WINDOW, llm=client, adapter=StubAdapter(metrics_rows=ROWS), cfg=_cfg()))

    assert out.answer == "r1 cpu pegged [metrics:0][metrics:1]"
    assert out.stopped is None
    # tools were wrapped into the chat-completions function shape (the one wrapping place)
    first = server.requests[0]
    assert first["tools"][0]["type"] == "function"
    assert first["tools"][0]["function"]["name"] == "query_metrics"
    # the follow-up request carries the assistant tool_calls + a matched tool reply
    follow = server.requests[1]["messages"]
    asst = [m for m in follow if m["role"] == "assistant" and m.get("tool_calls")]
    assert asst and asst[0]["tool_calls"][0]["id"] == "c1"
    assert asst[0]["tool_calls"][0]["function"]["name"] == "query_metrics"
    tool_msgs = [m for m in follow if m["role"] == "tool"]
    assert tool_msgs and tool_msgs[0]["tool_call_id"] == "c1", "tool reply matches the call id"


def test_make_client_profile_swap_is_config_only():
    nim = make_client(_cfg(llm_profile="nim"))
    local = make_client(_cfg(llm_profile="unsloth-local"))
    assert isinstance(nim, OpenAIClient) and isinstance(local, OpenAIClient)
    assert nim._model == "gpt-oss-20b" and local._model == "unsloth/gpt-oss-20b", \
        "distinct model per profile -- a swap must not reuse the wrong model id"
    # defense-in-depth: an unvalidated profile (bypassing Config's enum check) is rejected
    raised = False
    try:
        make_client(types.SimpleNamespace(llm_profile="gpt4", llm_api_key=""))
    except ValueError:
        raised = True
    assert raised, "unknown profile must raise, not silently pick a default"


def test_api_key_sent_only_when_present():
    server = _FakeServer([{"role": "assistant", "content": "ok"}])
    _with_fake_server(server, lambda: OpenAIClient("http://fake/v1", "m", "sk-xyz").chat(
        [{"role": "user", "content": "hi"}]))
    # (the fake records only the body, so assert on a keyed vs unkeyed client via a header probe)
    got = {}

    def probe(url, json=None, headers=None, timeout=None):
        got.update(headers or {})
        return _FakeResp({"choices": [{"message": {"role": "assistant", "content": "ok"}}]})

    saved = httpx.post
    httpx.post = probe
    try:
        OpenAIClient("http://fake/v1", "m", "sk-xyz").chat([{"role": "user", "content": "hi"}])
        assert got.get("Authorization") == "Bearer sk-xyz"
        got.clear()
        OpenAIClient("http://fake/v1", "m", "").chat([{"role": "user", "content": "hi"}])
        assert "Authorization" not in got, "no key -> no Authorization header"
    finally:
        httpx.post = saved


def test_as_function_wraps_flat_and_is_idempotent():
    flat = {"name": "query_metrics", "description": "d",
            "parameters": {"type": "object", "properties": {}}}
    wrapped = _as_function(flat)
    assert wrapped == {"type": "function", "function": flat}
    assert _as_function(wrapped) is wrapped, "already-wrapped spec passes through unchanged"


def test_to_reply_parses_calls_and_degrades_bad_args():
    r = _to_reply({"content": "hi", "tool_calls": [
        {"id": "c1", "function": {"name": "flows", "arguments": '{"device": "r1"}'}}]})
    assert r.content == "hi"
    assert r.tool_calls[0].name == "flows" and r.tool_calls[0].arguments == {"device": "r1"}
    # a weak model emitting malformed argument JSON degrades to {} -- never raises in chat()
    bad = _to_reply({"content": None, "tool_calls": [
        {"id": "c2", "function": {"name": "flows", "arguments": "{not json"}}]})
    assert bad.tool_calls[0].arguments == {}


def test_parse_tool_calls_ignores_json_quoted_in_prose():
    # R1 hardening: a real model quoting a call inside a prose ANSWER is not a tool call.
    prose = 'I checked and the call would be {"name": "query_metrics", "arguments": ' \
            '{"device": "r1"}} but r1 is fine [metrics:0].'
    assert parse_tool_calls(prose) == (), "embedded JSON in prose must not be read as a call"
    # a bare call, or one in a ```json fence, still parses
    assert parse_tool_calls('{"name": "flows", "arguments": {"device": "r1"}}')[0].name == "flows"
    fenced = 'reasoning first, then:\n```json\n{"name": "flows", "arguments": {}}\n```'
    assert parse_tool_calls(fenced)[0].name == "flows", "fenced call parses"


def test_recorded_risk_self_judge_fails_open_on_prose():
    # RISK (recorded, docs/SPEC-NOTES.md R1): a real weak model emitting prose instead of the
    # verdict JSON must NOT wedge an in-window cited answer -> self_judge fails open. The
    # deterministic pre-gate stays the hard guarantee. Outcome: unchanged, by design.
    from copilot.agent.loop import self_judge
    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "why is r1 slow?"}]
    assert self_judge(ScriptedLLM([final("Sure! The answer looks well supported.")]),
                      msgs, "r1 cpu pegged").ok is True


def _smoke():
    """One live smoke call (R1 acceptance) -- opt-in, needs a running endpoint. Proves the
    client speaks the wire protocol; NOT a full investigation."""
    from copilot.config import load
    client = make_client(load())
    reply = client.chat([{"role": "user", "content": "Reply with the single word: ok"}])
    assert reply.content is not None, "live endpoint returned no completion"
    print(f"live smoke OK -- endpoint replied: {reply.content!r}")


def _run():
    test_native_round_trip_preserves_tool_calls()
    test_make_client_profile_swap_is_config_only()
    test_api_key_sent_only_when_present()
    test_as_function_wraps_flat_and_is_idempotent()
    test_to_reply_parses_calls_and_degrades_bad_args()
    test_parse_tool_calls_ignores_json_quoted_in_prose()
    test_recorded_risk_self_judge_fails_open_on_prose()
    print("copilot.llm.http self-check OK")


if __name__ == "__main__":
    import os
    _run()
    if os.environ.get("COPILOT_LLM_SMOKE"):
        _smoke()
