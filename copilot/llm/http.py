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
import os
import re

from copilot.config import Config
from copilot.llm.client import Reply, ToolCall

_TIMEOUT = 120.0     # a real multi-round investigation turn on a 20B model is slow; generous so a
                     # healthy-but-slow completion is not cut off client-side as a false outage.


# profile -> (base-url env, base-url default, model env, model default). Both the endpoint AND
# the model are per-profile so flipping `llm_profile` moves traffic to the other backend (the nim
# endpoint is NOT air-gapped; unsloth-local must be able to point elsewhere, ADR-0004) and can't
# reuse the wrong model id (mirrors make_embedder's distinct model vars).
_PROFILES = {
    "nim": ("COPILOT_LLM_BASE_URL", "http://127.0.0.1:8000/v1",
            "COPILOT_LLM_MODEL_NIM", "gpt-oss-20b"),
    "unsloth-local": ("COPILOT_LLM_BASE_URL_LOCAL", "http://127.0.0.1:8001/v1",
                      "COPILOT_LLM_MODEL_LOCAL", "unsloth/gpt-oss-20b"),
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
        body = {"model": self._model, "messages": messages}
        if tools:
            body["tools"] = [_as_function(t) for t in tools]
        if self._effort:                  # gpt-oss burns tokens on reasoning -> tier it explicitly;
            body["reasoning_effort"] = self._effort   # unset (default) keeps the plain OpenAI body.
        r = httpx.post(
            f"{self._url}/chat/completions",
            json=body,
            headers={"Authorization": f"Bearer {self._key}"} if self._key else {},
            timeout=_TIMEOUT,
        )
        r.raise_for_status()
        return _to_reply(r.json()["choices"][0]["message"])


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
