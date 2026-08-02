"""copilot.llm.client -- the single boundary to the language model (ADR-0004/0005).

One OpenAI-compatible seam the whole agent loop + quality gate depend on. F1 ships
the interface + shapes only; the real HTTP client + profile selection is R1, the
owned tool-call parser is F3. Nothing here touches the network.

`chat(messages, tools?) -> Reply`:
  - `tools` given + endpoint supports native function-calling -> `Reply.tool_calls`.
  - else -> `Reply.content` holds the raw completion for F3's owned parser.
"""
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class ToolCall:
    """One model-requested tool invocation (OpenAI function-call shape)."""
    name: str
    arguments: dict
    id: str = ""


@dataclass(frozen=True)
class Reply:
    """A model turn: assistant text and/or tool calls.

    `content` doubles as the raw completion when the endpoint isn't native
    function-calling -- F3's parser reads it. `tool_calls` is empty then.
    """
    content: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()


@runtime_checkable
class LLMClient(Protocol):
    """The stable seam. Concrete clients (stub now, HTTP in R1) satisfy it
    structurally, so swapping one for another is config-only."""

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> Reply:
        ...
