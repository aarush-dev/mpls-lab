"""copilot.llm -- Lane-Runtime (F1/R1). OpenAI-compatible LLM client, profile-selected (ADR-0004).

F1: the `LLMClient` seam + shapes (`Reply`, `ToolCall`) + a scripted stub for
deterministic tests. Real HTTP client + profile selection land in R1; the owned
tool-call parser in F3. Owner lane builds here; do not edit from the other lane.
"""
from copilot.llm.client import LLMClient, Reply, ToolCall
from copilot.llm.stub import ScriptedLLM, final, tool_call

__all__ = ["LLMClient", "Reply", "ToolCall", "ScriptedLLM", "tool_call", "final"]
