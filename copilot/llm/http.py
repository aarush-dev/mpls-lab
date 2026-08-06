"""copilot.llm.http -- the real OpenAI-compatible LLM client, profile-selected (R1, ADR-0004/0005).

`make_client(cfg)` dispatches on `cfg.llm_profile` -- swap backend = one config line, no loop
code changes (mirrors retrieval.embedder.make_embedder). Both shipping profiles (`nim` interim /
gpt-oss-20b now, `unsloth-local` final air-gapped) speak the SAME OpenAI `/chat/completions`
wire, so they are one `OpenAIClient` differing only by base URL + model id + whether a key is
sent -- not two classes (ponytail: the embedder needed two because one runs in-process; here both
are HTTP). httpx is already a dep; the endpoint is touched on `chat()`, never at construction, so
profile selection stays testable air-gapped.

This client is the ONE place the flat tool-spec shape (copilot.tools.registry.TOOL_SPECS /
loop.LOAD_SKILL_SPEC: `{"name","description","parameters"}`) is wrapped into the chat-completions
`{"type":"function","function":{...}}` wire form (`_as_function`) -- so neither literal is touched
and there is still one spec shape emitted from one place (R1 acceptance).

Self-check:  python3 -m copilot.llm.test_http
Live smoke (opt-in, needs a running endpoint):  COPILOT_LLM_SMOKE=1 python3 -m copilot.llm.test_http
"""
import json
import logging
import os
import re
import time

from copilot.config import Config
from copilot.llm.client import Reply, ToolCall

log = logging.getLogger(__name__)

_TIMEOUT = 600.0     # per-chunk read timeout (streamed, so this is a silence gap, not total time).
                     # was 120s (one serial call), then 240s; concurrent requests share hosted
                     # NIM's rate limit and reasoning gaps between chunks widen under load, so
                     # raised again to a runaway backstop rather than a tuned working budget.


# profile -> (base-url env, base-url default, model env, model default). Both the endpoint AND
# the model are per-profile so flipping `llm_profile` moves traffic to the other backend (the nim
# endpoint is NOT air-gapped; unsloth-local must be able to point elsewhere, ADR-0004) and can't
# reuse the wrong model id (mirrors make_embedder's distinct model vars).
_PROFILES = {
    "nim": ("COPILOT_LLM_BASE_URL", "http://127.0.0.1:8000/v1",
            "COPILOT_LLM_MODEL_NIM", "gpt-oss-20b"),
    "unsloth-local": ("COPILOT_LLM_BASE_URL_LOCAL", "http://127.0.0.1:8001/v1",
                      "COPILOT_LLM_MODEL_LOCAL", "unsloth/gpt-oss-20b"),
    # gemma: on-prem gemma-4 (OpenAI-compatible, keyless). Its OWN base-url env, NOT nim's
    # COPILOT_LLM_BASE_URL -- an existing hosted-NIM .env sets that to the nvidia URL, which would
    # otherwise hijack this profile. Model id is server-mangled (mtp-...-Q8_0) and has changed
    # once already; pin it here, override via COPILOT_LLM_MODEL_GEMMA when the served model renames.
    "gemma": ("COPILOT_LLM_BASE_URL_GEMMA", "http://10.0.0.5:8888/v1",
              "COPILOT_LLM_MODEL_GEMMA", "mtp-gemma-4-26B-A4B-it-Q8_0"),
}


def make_client(cfg: Config):
    """Return the LLMClient for the configured profile (ADR-0004). An empty api_key sends no
    Authorization header, so a keyless local server just works."""
    prof = _PROFILES.get(cfg.llm_profile)
    if prof is None:                                    # config.py validates the enum; this is
        raise ValueError(f"unknown llm_profile {cfg.llm_profile!r}")   # defense-in-depth if raw.
    base_env, base_def, model_env, model_def = prof
    return OpenAIClient(os.environ.get(base_env, base_def),
                        os.environ.get(model_env, model_def), cfg.llm_api_key,
                        os.environ.get("COPILOT_LLM_REASONING_EFFORT", ""))


class OpenAIClient:
    """OpenAI-compatible `/chat/completions` client satisfying LLMClient. Native
    function-calling: `tools` -> the model's `tool_calls` ride back on `Reply.tool_calls`;
    with no tools (or a backend that doesn't call) the completion text is `Reply.content` for
    the loop's owned parser."""

    def __init__(self, base_url: str, model: str, api_key: str = "", reasoning_effort: str = ""):
        self._url = base_url.rstrip("/")
        self._model = model
        self._key = api_key
        self._effort = reasoning_effort   # gpt-oss reasoning tier (low|medium|high); "" -> omit

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> Reply:
        import httpx
        body = {"model": self._model, "messages": messages, "stream": True}
        if tools:
            body["tools"] = [_as_function(t) for t in tools]
        if self._effort:                  # gpt-oss burns tokens on reasoning -> tier it explicitly;
            body["reasoning_effort"] = self._effort   # unset (default) keeps the plain OpenAI body.
        elif "nemotron" in self._model:   # nvidia default recipe: thinking on, budgeted
            body["chat_template_kwargs"] = {"enable_thinking": True}
            body["reasoning_budget"] = 16384
        # streamed so _TIMEOUT is a per-chunk read timeout, not a total-completion one: a slow
        # multi-round turn keeps resetting it as tokens trickle in, instead of blocking on one
        # read for the whole (possibly minutes-long) non-streamed body (#trigger-crash timeout).
        headers = {"Authorization": f"Bearer {self._key}"} if self._key else {}
        n_msgs, t0 = len(messages), time.monotonic()
        log.info("-> %s model=%s n_messages=%s tools=%s", self._url, self._model, n_msgs,
                 len(tools) if tools else 0)
        # #114 (secondary): a hosted backend's 429 is a rate limit, not a hard failure -- retry
        # with a short backoff instead of crashing /chat straight to a 500. Bounded (3 tries
        # total) so a persistently-down/over-quota backend still surfaces the error. 503 too: the
        # shared on-prem gemma box returns 503 upstream_error under concurrent load (transient),
        # same treatment as a rate limit.
        for attempt in range(3):
            try:
                with httpx.stream("POST", f"{self._url}/chat/completions", json=body,
                                  headers=headers, timeout=_TIMEOUT) as r:
                    if r.status_code in (429, 503) and attempt < 2:
                        log.warning("<- %s %d, retrying (attempt %d)", self._url,
                                    r.status_code, attempt + 1)
                        time.sleep(2 * (attempt + 1))
                        continue
                    r.raise_for_status()
                    msg = _accumulate_stream(r)
                break
            except Exception:
                log.exception("<- %s FAILED after %.1fs", self._url, time.monotonic() - t0)
                raise
        log.info("<- %s ok in %.1fs tool_calls=%s", self._url, time.monotonic() - t0,
                 len(msg.get("tool_calls") or []))
        return _to_reply(msg)


def _accumulate_stream(r) -> dict:
    """SSE `chat.completions` chunks -> one assembled message dict (same shape as the non-streamed
    `choices[0].message`). Tool-call deltas arrive by `index` with `name`/`arguments` split across
    chunks (arguments is a growing JSON-string fragment), so accumulate by index and join strings."""
    content_parts = []
    calls: dict[int, dict] = {}
    for line in r.iter_lines():
        if not line.startswith("data: "):
            continue
        data = line[len("data: "):]
        if data == "[DONE]":
            break
        choices = json.loads(data).get("choices") or []   # some chunks (e.g. trailing usage-only) carry none
        if not choices:
            continue
        delta = choices[0].get("delta") or {}
        if delta.get("content"):
            content_parts.append(delta["content"])
        for i, tc in enumerate(delta.get("tool_calls") or []):
            slot = calls.setdefault(tc.get("index", i), {"id": "", "function": {"name": "", "arguments": ""}})
            if tc.get("id"):
                slot["id"] = tc["id"]
            fn = tc.get("function") or {}
            if fn.get("name"):
                slot["function"]["name"] += fn["name"]
            if fn.get("arguments"):
                slot["function"]["arguments"] += fn["arguments"]
    return {
        "content": "".join(content_parts) or None,
        "tool_calls": [calls[i] for i in sorted(calls)],
    }


def _as_function(spec: dict) -> dict:
    """Flat registry spec -> chat-completions tool wire form. Idempotent: an already-wrapped
    spec passes through, so this is safe to run over any tool list."""
    if spec.get("type") == "function":
        return spec
    return {"type": "function", "function": {
        "name": spec["name"],
        "description": spec.get("description", ""),
        "parameters": spec.get("parameters", {"type": "object", "properties": {}}),
    }}


def _to_reply(msg: dict) -> Reply:
    """One assistant message from the response -> Reply. `arguments` is a JSON string on the
    wire; a weak model can emit malformed JSON, so a bad blob degrades to `{}` (the loop's gate
    then sees an empty call) rather than raising inside chat()."""
    calls = tuple(
        ToolCall(name=_clean_name(c["function"]["name"]),
                 arguments=_loads_obj(c["function"].get("arguments")),
                 id=c.get("id", ""))
        for c in (msg.get("tool_calls") or [])
    )
    return Reply(content=msg.get("content"), tool_calls=calls)


def _clean_name(raw: str) -> str:
    # #45: gpt-oss (harmony) sometimes leaks a channel token into the tool-call name over NIM's
    # OpenAI-compatible endpoint (e.g. "search_logs<|channel|>commentary"); strip the markup so the
    # intended tool dispatches. Model-agnostic -- a name that never had markup is unchanged.
    return re.sub(r"<\|.*?\|>.*$", "", raw).strip()


def _loads_obj(s) -> dict:
    if isinstance(s, dict):
        return s
    try:
        obj = json.loads(s or "{}")
    except (ValueError, TypeError):
        return {}
    return obj if isinstance(obj, dict) else {}
