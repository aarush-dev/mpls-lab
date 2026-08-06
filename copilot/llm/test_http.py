"""Assert-based tests / self-check for the real LLM client + the loop fixes it forces (R1).

Prior art: dataapi/check_dataset.py (assert + __main__, no framework); real-HTTP double like
copilot/api/test_api.py. Covers the R1 acceptance: a native tool-call round trip accepted by a
real (in-process) OpenAI-shaped server -- assistant `tool_calls` preserved, `tool` reply matched
over the wire -- config-only profile swap (endpoint AND model), one wrapped tool-spec shape from
one place, `parse_tool_calls` not misreading JSON quoted in a prose answer, and the recorded risk.

Run:        python3 -m copilot.llm.test_http
Live smoke: COPILOT_LLM_SMOKE=1 python3 -m copilot.llm.test_http   (needs a running endpoint)
"""
import dataclasses
import http.server
import json
import socketserver
import threading
import types

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


class _OAIHandler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        self.server.requests.append(json.loads(self.rfile.read(n) or b"{}"))
        self.server.auths.append(self.headers.get("Authorization"))
        msg = self.server.replies[self.server.i]
        self.server.i += 1
        payload = json.dumps({"choices": [{"message": msg}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *a):
        pass


class _FakeOpenAI:
    """A real (in-process, ephemeral-port) OpenAI-shaped /chat/completions server: records every
    request body + Authorization header and returns scripted assistant messages in order."""

    def __init__(self, replies):
        self._httpd = socketserver.TCPServer(("127.0.0.1", 0), _OAIHandler)
        self._httpd.replies, self._httpd.i = list(replies), 0
        self._httpd.requests, self._httpd.auths = [], []
        self.requests, self.auths = self._httpd.requests, self._httpd.auths
        self.url = f"http://127.0.0.1:{self._httpd.server_address[1]}/v1"
        threading.Thread(target=self._httpd.serve_forever, daemon=True).start()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self._httpd.shutdown()
        self._httpd.server_close()


def test_native_round_trip_preserves_tool_calls():
    # THE acceptance, over a real socket: a native call then a cited answer, driven end-to-end
    # through the real client + the loop. The second request must carry the assistant `tool_calls`
    # AND a `tool` message matching its id -- the R1 hard-break fix (loop was dropping tool_calls).
    with _FakeOpenAI([
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "c1", "type": "function",
             "function": {"name": "query_metrics", "arguments": '{"device": "r1"}'}}]},
        {"role": "assistant", "content": "r1 cpu pegged [metrics:0][metrics:1]"},
        {"role": "assistant", "content": '{"pass": true}'},          # self-judge verdict
    ]) as srv:
        out = investigate("why is r1 slow?", WINDOW, llm=OpenAIClient(srv.url, "gpt-oss-20b"),
                          adapter=StubAdapter(metrics_rows=ROWS), cfg=_cfg())

    assert out.answer == "r1 cpu pegged [metrics:0][metrics:1]"
    assert out.stopped is None
    # tools were wrapped into the chat-completions function shape (the one wrapping place)
    first = srv.requests[0]
    assert first["tools"][0]["type"] == "function"
    assert first["tools"][0]["function"]["name"] == "query_metrics"
    # the follow-up request carries the assistant tool_calls + a matched tool reply
    follow = srv.requests[1]["messages"]
    asst = [m for m in follow if m["role"] == "assistant" and m.get("tool_calls")]
    assert asst and asst[0]["tool_calls"][0]["id"] == "c1"
    assert asst[0]["tool_calls"][0]["function"]["name"] == "query_metrics"
    tool_msgs = [m for m in follow if m["role"] == "tool"]
    assert tool_msgs and tool_msgs[0]["tool_call_id"] == "c1", "tool reply matches the call id"


def test_make_client_profile_swap_moves_endpoint_and_model():
    # tests the documented FALLBACK defaults -> clear any env override (a real copilot/.env sets
    # COPILOT_LLM_MODEL_NIM/BASE_URL, which load() bleeds into os.environ during the suite).
    import os
    saved = {k: os.environ.pop(k, None) for k in
             ("COPILOT_LLM_BASE_URL", "COPILOT_LLM_MODEL_NIM",
              "COPILOT_LLM_BASE_URL_LOCAL", "COPILOT_LLM_MODEL_LOCAL")}
    try:
        nim = make_client(_cfg(llm_profile="nim"))
        local = make_client(_cfg(llm_profile="unsloth-local"))
    finally:
        os.environ.update({k: v for k, v in saved.items() if v is not None})
    assert isinstance(nim, OpenAIClient) and isinstance(local, OpenAIClient)
    assert nim._model == "gpt-oss-20b" and local._model == "unsloth/gpt-oss-20b", \
        "distinct model per profile -- a swap must not reuse the wrong model id"
    assert nim._url != local._url, \
        "distinct endpoint per profile -- flipping to unsloth-local must not hit the nim URL"
    # defense-in-depth: an unvalidated profile (bypassing Config's enum check) is rejected
    raised = False
    try:
        make_client(types.SimpleNamespace(llm_profile="gpt4", llm_api_key=""))
    except ValueError:
        raised = True
    assert raised, "unknown profile must raise, not silently pick a default"


def test_reasoning_effort_rides_body_only_when_set():
    # gpt-oss is a reasoning model: E1 needs the tier explicit (env COPILOT_LLM_REASONING_EFFORT).
    # Set -> body carries reasoning_effort; unset ("") -> the body stays plain OpenAI shape.
    with _FakeOpenAI([{"role": "assistant", "content": "ok"},
                      {"role": "assistant", "content": "ok"}]) as srv:
        OpenAIClient(srv.url, "m", "", "high").chat([{"role": "user", "content": "hi"}])
        OpenAIClient(srv.url, "m", "").chat([{"role": "user", "content": "hi"}])
    assert srv.requests[0].get("reasoning_effort") == "high"
    assert "reasoning_effort" not in srv.requests[1], "unset effort -> plain body, no extra key"


def test_api_key_sent_only_when_present():
    with _FakeOpenAI([{"role": "assistant", "content": "ok"},
                      {"role": "assistant", "content": "ok"}]) as srv:
        OpenAIClient(srv.url, "m", "sk-xyz").chat([{"role": "user", "content": "hi"}])
        OpenAIClient(srv.url, "m", "").chat([{"role": "user", "content": "hi"}])
        assert srv.auths[0] == "Bearer sk-xyz"
        assert srv.auths[1] is None, "no key -> no Authorization header"


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


def test_to_reply_strips_harmony_channel_leak_from_tool_name():
    # gpt-oss (harmony format) sometimes leaks a channel token into the tool-call name over NIM's
    # OpenAI-compatible endpoint; strip it so the intended tool dispatches (#45).
    r = _to_reply({"content": None, "tool_calls": [
        {"id": "c1", "function": {"name": "search_logs<|channel|>commentary",
                                  "arguments": '{"device": "pe8"}'}}]})
    assert r.tool_calls[0].name == "search_logs"
    assert r.tool_calls[0].arguments == {"device": "pe8"}
    # a clean name is model-agnostic -- unchanged
    clean = _to_reply({"content": None, "tool_calls": [
        {"id": "c2", "function": {"name": "search_logs", "arguments": "{}"}}]})
    assert clean.tool_calls[0].name == "search_logs"


def test_parse_tool_calls_only_when_the_whole_turn_is_the_call():
    # R1 hardening: a real model quoting a call inside a prose ANSWER is not a tool call -- and
    # neither is reasoning followed by a fenced block. Only a whole-turn call parses.
    prose = 'I checked and the call would be {"name": "query_metrics", "arguments": ' \
            '{"device": "r1"}} but r1 is fine [metrics:0].'
    assert parse_tool_calls(prose) == (), "embedded JSON in prose must not be read as a call"
    reasoning_then_fence = 'First I will pull metrics:\n```json\n{"name": "flows", ' \
                           '"arguments": {}}\n```'
    assert parse_tool_calls(reasoning_then_fence) == (), \
        "reasoning + a fenced block is a prose answer, not a call"
    # a bare call, or a LONE fence that is the whole turn, still parses
    assert parse_tool_calls('{"name": "flows", "arguments": {"device": "r1"}}')[0].name == "flows"
    assert parse_tool_calls('```json\n{"name": "flows", "arguments": {}}\n```')[0].name == "flows"


def test_recorded_risk_self_judge_fails_open_on_prose():
    # RISK 1 (recorded in the R1 commit message): a real weak model emitting prose instead of the
    # verdict JSON must NOT wedge an in-window cited answer -> self_judge fails open. The
    # deterministic pre-gate stays the hard guarantee. Outcome: unchanged, by design.
    from copilot.agent.loop import self_judge
    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "why is r1 slow?"}]
    assert self_judge(ScriptedLLM([final("Sure! The answer looks well supported.")]),
                      msgs, "r1 cpu pegged").ok is True


def test_recorded_risk_ask_back_without_question_mark_is_gated():
    # RISK 2 (loop.py ask-back heuristic): the ask-back only fires when a no-evidence turn ends
    # in '?'. Exercise the failing shape -- a real model's clarifying prose that does NOT end in
    # '?' -- and record the outcome: it does NOT surface as an ask-back; it falls into the gate
    # and is safely BLOCKED (never emitted as an uncited answer). The heuristic's limitation is a
    # UX degradation, not a correctness hole, so it is recorded here, not "fixed" (ticket: fix
    # only if it bites).
    llm = ScriptedLLM([final("I need to know which device you mean before I can look.")])
    out = investigate("is the network ok?", WINDOW, llm=llm,
                      adapter=StubAdapter(metrics_rows=ROWS), cfg=_cfg(gate_max_retries=0))
    assert not out.answer.startswith("I need to know"), \
        "clarifying prose without '?' must not surface as an ask-back (heuristic limitation)"
    assert out.answer.startswith("cannot answer yet"), "it is safely gated instead, not emitted"


def test_recorded_risk_investigate_and_chat_are_sync():
    # RISK 3 (sync blocking): investigate() and the client's chat() are synchronous, and the
    # /chat endpoint is a sync `def`. Recorded outcome: this is intentional (ADR-0010, local-only
    # single-user) -- Starlette runs a sync endpoint in its threadpool, so a slow multi-round
    # investigation blocks a worker thread, not the event loop. Exercised by asserting the shape
    # so a later async refactor is a conscious change, not an accident.
    import inspect
    from copilot.api.app import chat as chat_endpoint
    assert not inspect.iscoroutinefunction(OpenAIClient.chat)
    assert not inspect.iscoroutinefunction(investigate)
    assert not inspect.iscoroutinefunction(chat_endpoint), \
        "sync endpoint -> Starlette threadpool (ADR-0010 local-only); blocks a worker, not the loop"


def _smoke():
    """One live smoke call (R1 acceptance) -- opt-in, needs a running endpoint. Proves the
    client speaks the wire protocol; NOT a full investigation."""
    from copilot.config import load
    reply = make_client(load()).chat([{"role": "user", "content": "Reply with the word: ok"}])
    assert reply.content is not None, "live endpoint returned no completion"
    print(f"live smoke OK -- endpoint replied: {reply.content!r}")


def _run():
    test_native_round_trip_preserves_tool_calls()
    test_make_client_profile_swap_moves_endpoint_and_model()
    test_reasoning_effort_rides_body_only_when_set()
    test_api_key_sent_only_when_present()
    test_as_function_wraps_flat_and_is_idempotent()
    test_to_reply_parses_calls_and_degrades_bad_args()
    test_to_reply_strips_harmony_channel_leak_from_tool_name()
    test_parse_tool_calls_only_when_the_whole_turn_is_the_call()
    test_recorded_risk_self_judge_fails_open_on_prose()
    test_recorded_risk_ask_back_without_question_mark_is_gated()
    test_recorded_risk_investigate_and_chat_are_sync()
    print("copilot.llm.http self-check OK")


if __name__ == "__main__":
    import os
    _run()
    if os.environ.get("COPILOT_LLM_SMOKE"):
        _smoke()
