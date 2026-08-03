"""copilot.agent -- Lane-Investigation (F3/I4a/I4b). Agent core loop + two-stage quality gate (ADR-0005/0008).

F3: the owned think->tool->observe->decide->answer loop (`investigate`) over the F1
LLM seam + F2 tool adapter -- tools dispatched through the I1 registry (copilot.tools),
step/tool-call caps, ask-back, and the owned JSON fallback parser. Emits canonical
trace events (ADR-0009
`Event`/`Outcome`) for F4 to stream. The quality gate (I4a/I4b) reuses this same loop.
Owner lane builds here; do not edit from the other lane.
"""
from copilot.agent.loop import (
    EVENT_TYPES, SYSTEM_PROMPT, TOOL_SPECS, Event, Outcome, event_wire,
    investigate, parse_tool_calls,
)

__all__ = [
    "investigate", "Event", "Outcome", "event_wire", "parse_tool_calls",
    "EVENT_TYPES", "SYSTEM_PROMPT", "TOOL_SPECS",
]
