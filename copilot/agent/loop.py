"""copilot.agent.loop -- the owned think->tool->observe->decide->answer loop (ADR-0005).

~150 lines, no framework. Rides the F1 LLM seam (copilot.llm) + the F2 tool adapter
(copilot.adapter), dispatching every tool call through the registry (copilot.tools:
query_metrics/search_logs/flows + search_runbooks/search_incidents + walk_topology_graph).
Emits canonical trace events (ADR-0009 enum) for F4 to stream / persist. A step
cap + tool-call cap (config, ADR-0005) stop runaway; ask-back returns a clarifying
question to the human instead of a tool call.

Before an answer is allowed out, the two-stage quality gate (ADR-0008) runs over the
structured `Cite`s gathered from tool calls: stage 1 is the I4a deterministic pre-gate
(copilot.agent.gate: sufficiency / in-window / on-topic + citation check); over its
survivors stage 2 is the I4b `self_judge` LLM call (relevance / sufficiency / consistency).
On fail the loop re-enters to fetch the reported `missing[]`, up to cfg.gate_max_retries;
still failing -> the `missing[]` list IS the answer (a `gate` event is emitted each fail).
Ask-back (a clarifying question before any evidence) bypasses the gate.

Windowing (ADR-0002): the loop -- not the model -- passes the window's (start, end)
into every tool call, so the agent cannot read outside its window. F3 threads a bare
`(start, end)` epoch pair (basic form); R3 swaps in the full WindowContext {start, end,
frozen} and the forensic `end`-freeze guard.
"""
import json
from dataclasses import dataclass

from copilot.adapter import ToolAdapter
from copilot.agent.gate import GateResult, run_gate
from copilot.config import Config
from copilot.llm import LLMClient, ToolCall
from copilot.retrieval import Retriever
from copilot.tools import Cite, TOOL_SPECS, dispatch

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
    "You are a read-only network investigator. Use the live-telemetry tools "
    "(query_metrics, search_logs, flows) to gather evidence within the given time "
    "window -- narrow every such call by device or pattern -- and the knowledge-base "
    "tools (search_runbooks, search_incidents) to find the matching runbook and similar "
    "past incidents (pass a device to search_incidents to focus on nearby topology). Use "
    "walk_topology_graph to see the blast-radius / downstream devices of a fault and their "
    "live status. Cite the evidence ids you rely on. If the request is too vague to "
    "investigate, ask one clarifying question instead of calling a tool."
)

JUDGE_SYSTEM = (
    "You are a strict evidence auditor for a network investigation. Given the question, the "
    "evidence gathered (the tool results above), and a draft answer, judge whether that "
    "evidence is RELEVANT, SUFFICIENT, and CONSISTENT enough to support the answer. Reply with "
    'ONE JSON object and nothing else: {"pass": <bool>, "missing": [<str>], '
    '"contradictions": [<str>]}. missing = specific evidence still needed to answer; '
    "contradictions = gathered evidence that conflicts."
)

# TOOL_SPECS + dispatch live in copilot.tools (the registry, I1) -- re-exported here so
# the F3 agent API surface (copilot.agent) is unchanged. start/end are NOT advertised:
# the loop supplies the window, the model only narrows within it (ADR-0002).


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
                llm: LLMClient, adapter: ToolAdapter, cfg: Config,
                retriever: Retriever | None = None,
                kg: dict[str, str] | None = None) -> Outcome:
    """Run the loop until the model answers, asks back, or a cap trips. `kg` is the optional
    curated-KG hint map (ADR-0007): additive, never load-bearing -- the caller passes it only
    when cfg.kg_enabled (get_kg), so it's None here whenever the flag is off."""
    events: list[Event] = []

    def emit(type_: str, **data) -> None:
        events.append(Event(type_, data))

    emit("user_msg", content=question)
    messages = [{"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": question}]
    tool_calls = 0
    retries = 0                                           # agentic gate retries used (ADR-0008)
    evidence: list[Cite] = []                            # structured cites gathered, for the gate
    tool_errors: list[str] = []                          # guidance errors from failed calls (gate)

    for _ in range(cfg.step_cap):
        reply = llm.chat(messages, tools=TOOL_SPECS)
        native = reply.tool_calls
        calls = native or parse_tool_calls(reply.content)

        if not calls:                                    # terminal: answer, ask-back, or gate
            text = reply.content or ""
            # ask-back (ADR-0005): a clarifying question asked BEFORE any evidence is not an
            # answer, so the quality gate doesn't apply. ponytail: detect it by a trailing '?'
            # on a no-evidence turn -- misclassifies a cited answer ending in a rhetorical
            # question, but on a no-evidence turn the gate would block that anyway. Upgrade to
            # an explicit ask-back signal from the loop/model if the heuristic ever bites.
            if not evidence and text.rstrip().endswith("?"):
                emit("assistant_msg", content=text)
                return Outcome(answer=text, events=tuple(events))
            # stage 1 (I4a deterministic) then, over its survivors, stage 2 (I4b self-judge).
            gate = run_gate(text, evidence, window=window, question=question,
                            min_evidence=cfg.gate_min_evidence, tool_errors=tool_errors)
            # stage 2 only over stage-1 survivors (self_judge sees the sanitized transcript).
            missing = gate.missing or self_judge(llm, messages, text).missing
            if missing:
                # ADR-0008: on fail re-enter the loop to fetch missing[], bounded by
                # gate_max_retries; when the cap is hit the missing[] list IS the message.
                emit("gate", ok=False, missing=list(missing), retry=retries)
                if retries < cfg.gate_max_retries:
                    retries += 1
                    tool_errors.clear()          # a retry re-issues the failed call; judge each
                    messages.append({"role": "assistant", "content": text})  # round on its own errors
                    messages.append({"role": "user", "content":
                        "That answer was blocked -- " + "; ".join(missing) + ". Gather any "
                        "missing evidence with the tools (or resolve the noted conflicts), "
                        "then answer again."})
                    continue
                msg = "cannot answer yet: " + "; ".join(missing)
                emit("assistant_msg", content=msg)
                return Outcome(answer=msg, events=tuple(events))
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
            observation, cites = dispatch(tc.name, tc.arguments, adapter, window, retriever, kg)
            evidence.extend(cites)
            if observation.startswith("error:"):        # guidance error -> a failed call (gate)
                tool_errors.append(observation)
            emit("tool_result", id=tc.id, name=tc.name, content=observation, n=len(cites))
            messages.append({"role": "tool", "tool_call_id": tc.id,
                             "name": tc.name, "content": observation})

    return _capped(events, "step_cap")


def self_judge(llm: LLMClient, messages: list[dict], answer: str) -> GateResult:
    """I4b stage 2 (ADR-0008): one self-judge LLM call over the evidence that survived the
    deterministic pre-gate -> the semantic verdict (relevance / sufficiency / consistency) a
    weak model can bluff and pure code can't check. Judges over the running transcript, so it
    sees the actual tool-result content (already adapter-sanitized, ADR-0016) -- not just cite
    ids -- which is what makes the CONSISTENT / contradictions[] check possible. A fail's
    missing[]/contradictions[] ride the same GateResult stage 1 uses, so the loop treats both
    stages uniformly (and re-fetches missing[] on retry).

    ponytail: fail-OPEN unless the verdict is an explicit {"pass": false} -- the deterministic
    gate is the hard guarantee, so a judge that emits junk JSON (or omits the key) must not
    wedge an otherwise-good, in-window, cited answer. Upgrade to a stricter parse / a re-ask if
    the self-judge ever rubber-stamps (ADR-0008).
    """
    reply = llm.chat(                                     # no tools -> a pure verdict, not a call
        [{"role": "system", "content": JUDGE_SYSTEM}] + messages[1:] +
        [{"role": "user", "content": f"Draft answer: {answer}\nReturn the verdict JSON now."}])
    obj = _first_json_object(reply.content or "")
    if not isinstance(obj, dict) or obj.get("pass", True):
        return GateResult(True)                           # pass / fail-open
    missing = [f"self-judge: {m}" for m in obj.get("missing") or []]
    missing += [f"contradiction: {c}" for c in obj.get("contradictions") or []]
    return GateResult(False, tuple(missing or ("self-judge: evidence insufficient",)))


def _capped(events: list[Event], why: str) -> Outcome:
    msg = f"stopped: {why} reached before a conclusion"
    events.append(Event("assistant_msg", {"content": msg}))
    return Outcome(answer=msg, events=tuple(events), stopped=why)


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
