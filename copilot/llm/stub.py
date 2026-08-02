"""copilot.llm.stub -- scripted LLM for deterministic tests (stub boundary 1).

Returns pre-written replies in order so the agent loop, gate, and judge behave
deterministically without a model or GPU (spec §Testing). Records every call so
tests can assert what the loop sent.
"""
from collections.abc import Sequence

from copilot.llm.client import Reply, ToolCall


class ScriptedLLM:
    """Replays `script` one Reply per chat() call. Satisfies LLMClient."""

    def __init__(self, script: Sequence[Reply]):
        self._script = list(script)
        self._i = 0
        self.calls: list[tuple[list[dict], list[dict] | None]] = []

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> Reply:
        self.calls.append((messages, tools))
        assert self._i < len(self._script), \
            f"ScriptedLLM: script exhausted after {self._i} replies"
        reply = self._script[self._i]
        self._i += 1
        return reply


def tool_call(name: str, arguments: dict, id: str = "call_0") -> Reply:
    """Script helper: a reply that asks for one tool call."""
    return Reply(tool_calls=(ToolCall(name=name, arguments=arguments, id=id),))


def final(text: str) -> Reply:
    """Script helper: a reply with final assistant text (also used for judge verdicts)."""
    return Reply(content=text)
