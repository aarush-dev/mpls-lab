"""copilot.agent.loop -- the owned think->tool->observe->decide->answer loop (ADR-0005).

~150 lines, no framework. Rides the F1 LLM seam (copilot.llm) + the F2 tool adapter
(copilot.adapter). One real tool wired: query_metrics -> adapter.metrics (I1 adds the
rest). Emits canonical trace events (ADR-0009 enum) for F4 to stream / persist. A step
cap + tool-call cap (config, ADR-0005) stop runaway; ask-back returns a clarifying
question to the human instead of a tool call.

Windowing (ADR-0002): the loop -- not the model -- passes the window's (start, end)
into every tool call, so the agent cannot read outside its window. F3 threads a bare
`(start, end)` epoch pair (basic form); R3 swaps in the full WindowContext {start, end,
frozen} and the forensic `end`-freeze guard.
"""
import json
from dataclasses import dataclass

from copilot.adapter import FilterError, Filters, Result, ToolAdapter
from copilot.config import Config
from copilot.llm import LLMClient, ToolCall

# canonical event enum (ADR-0009) -- ONE vocabulary for the live stream AND the
# persisted events.jsonl. F3 emits a subset; the gate/artifact types land later.
EVENT_TYPES = frozenset({
    "user_msg", "assistant_msg", "think", "tool_call", "tool_result", "gate", "artifact",
})


@dataclass(frozen=True)
class Event:
    """One trace event. `data` is the type-specific payload (F4 stamps the ISO-8601
    ts + session id when it persists/streams -- the loop owns type + payload only)."""
    type: str
    data: dict

    def __post_init__(self):
        assert self.type in EVENT_TYPES, \
            f"unknown event type {self.type!r} (not an ADR-0009 canonical type)"


@dataclass(frozen=True)
class Outcome:
    answer: str | None                       # final answer OR the ask-back question
    events: tuple[Event, ...] = ()
    stopped: str | None = None               # None | "step_cap" | "tool_call_cap"

    def of_type(self, t: str) -> tuple[Event, ...]:
        return tuple(e for e in self.events if e.type == t)


SYSTEM_PROMPT = (
    "You are a read-only network investigator. Use query_metrics to gather evidence "
    "within the given time window; you must narrow every call by device or pattern. "
    "Cite the evidence ids you rely on. If the request is too vague to investigate, "
    "ask one clarifying question instead of calling a tool."
)

# the tool schema advertised to a native function-calling backend. start/end are NOT
# exposed -- the loop supplies the window, the model only narrows within it.
TOOL_SPECS = [{
    "name": "query_metrics",
    "description": "Read device metrics within the investigation window.",
    "parameters": {
        "type": "object",
        "properties": {
            "device": {"type": "string"},
            "pattern": {"type": "string"},
            "limit": {"type": "integer"},
            "offset": {"type": "integer"},
        },
    },
}]


def parse_tool_calls(content: str | None) -> tuple[ToolCall, ...]:
    """Owned fallback parser (ADR-0005) for backends without native function-calling:
    a single JSON object {"name": ..., "arguments": {...}} (the ToolCall shape) in the
    completion. Returns () for plain prose, so a normal answer falls through to terminal.

    ponytail: one JSON object, one key spelling -- the simplest thing a weak model
    reliably emits. Upgrade to a multi-step ReAct grammar only if a real backend needs it.
    """
    obj = _first_json_object(content or "")
    if not isinstance(obj, dict):
        return ()
    name = obj.get("name")
    args = obj.get("arguments", {})
    if not name or not isinstance(args, dict):
        return ()
    return (ToolCall(name=str(name), arguments=args, id="parsed_0"),)


def investigate(question: str, window: tuple[int, int], *,
                llm: LLMClient, adapter: ToolAdapter, cfg: Config) -> Outcome:
    """Run the loop until the model answers, asks back, or a cap trips."""
    events: list[Event] = []

    def emit(type_: str, **data) -> None:
        events.append(Event(type_, data))

    emit("user_msg", content=question)
    messages = [{"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": question}]
    tool_calls = 0

    for _ in range(cfg.step_cap):
        reply = llm.chat(messages, tools=TOOL_SPECS)
        native = reply.tool_calls
        calls = native or parse_tool_calls(reply.content)

        if not calls:                                    # terminal: answer or ask-back
            text = reply.content or ""
            emit("assistant_msg", content=text)
            return Outcome(answer=text, events=tuple(events))

        if native and reply.content:                     # reasoning alongside a call
            emit("think", content=reply.content)
        messages.append({"role": "assistant",
                         "content": reply.content or _render_calls(calls)})

        for tc in calls:
            if tool_calls >= cfg.tool_call_cap:
                return _capped(events, "tool_call_cap")
            tool_calls += 1
            emit("tool_call", name=tc.name, arguments=tc.arguments, id=tc.id)
            observation, n = _dispatch(tc, adapter, window)
            emit("tool_result", id=tc.id, name=tc.name, content=observation, n=n)
            messages.append({"role": "tool", "tool_call_id": tc.id,
                             "name": tc.name, "content": observation})

    return _capped(events, "step_cap")


def _capped(events: list[Event], why: str) -> Outcome:
    msg = f"stopped: {why} reached before a conclusion"
    events.append(Event("assistant_msg", {"content": msg}))
    return Outcome(answer=msg, events=tuple(events), stopped=why)


def _dispatch(tc: ToolCall, adapter: ToolAdapter, window: tuple[int, int]) -> tuple[str, int]:
    """Run one tool. Returns (observation_text, n_rows). A FilterError comes back as
    the observation (ADR-0015 guidance) so the model can narrow and retry."""
    if tc.name != "query_metrics":                       # I1 wires the rest
        return f"error: unknown tool {tc.name!r}", 0
    # ponytail: window is a bare (start,end) pair (F3 basic form); R3 swaps in the
    # full WindowContext {start,end,frozen} + the forensic end-freeze guard (ADR-0002).
    start, end = window
    a = tc.arguments
    narrow = {k: a[k] for k in ("device", "pattern") if a.get(k)}
    for k in ("limit", "offset"):                         # let Filters own the defaults
        if k in a:
            narrow[k] = int(a[k])
    filters = Filters(start=start, end=end, **narrow)
    try:
        result = adapter.metrics(filters)
    except FilterError as e:
        return f"error: {e}", 0
    return _render_result(result), len(result.evidence)


def _render_result(res: Result) -> str:
    if not res.evidence:
        return "no rows"
    lines = [f"[{ev.id}] {ev.content}" for ev in res.evidence]
    if res.next_page:
        lines.append(f"(more: offset={res.next_page})")
    return "\n".join(lines)


def _render_calls(calls: tuple[ToolCall, ...]) -> str:
    return json.dumps([{"name": c.name, "arguments": c.arguments} for c in calls])


def _first_json_object(text: str) -> dict | None:
    """First balanced {...} in text, parsed; None if none / invalid."""
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:i + 1])
                except ValueError:
                    return None
    return None
