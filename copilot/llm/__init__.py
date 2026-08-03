"""copilot.llm -- Lane-Runtime (F1/R1). OpenAI-compatible LLM client, profile-selected (ADR-0004).

F1: the `LLMClient` seam + shapes (`Reply`, `ToolCall`) + a scripted stub for
deterministic tests. R1: the real HTTP client (`OpenAIClient`) + profile selection
(`make_client`); `ScriptedLLM` stays as the test double only. The owned tool-call
parser lives in the F3 loop.
"""
from copilot.llm.client import LLMClient, Reply, ToolCall
from copilot.llm.http import OpenAIClient, make_client
from copilot.llm.stub import ScriptedLLM, final, tool_call

__all__ = ["LLMClient", "Reply", "ToolCall", "ScriptedLLM", "tool_call", "final",
           "OpenAIClient", "make_client"]
