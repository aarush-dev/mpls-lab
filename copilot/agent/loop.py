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
still failing -> the `missing[]` list IS the answer (a `gate` event is emitted for each
outcome, pass and fail -- R2a makes gate outcomes user-visible generally, ADR-0009).
Ask-back (a clarifying question before any evidence) bypasses the gate.

Diagnostic skills (ADR-0012, I5): when the caller wires `skills`, every skill's
{name, description} sits in the base prompt (progressive disclosure) while its body loads
on demand -- the model auto-selects one via the `load_skill` tool, or a human preloads one
by name (`invoke`). A skill is METHOD (how to investigate), not cited evidence -> no Cite.

Windowing (ADR-0002): the loop -- not the model -- passes the `WindowContext {start, end,
frozen}` into every tool call, so the agent cannot read outside its window. When frozen
(forensic), the adapter freeze guard rejects any read past T_snapshot (Filters.validate).
"""
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone

from copilot.adapter import ToolAdapter
from copilot.agent.gate import GateResult, run_gate, trust_banner
from copilot.config import Config
from copilot.llm import LLMClient, ToolCall
from copilot.retrieval import Retriever
from copilot.skills import Skill, catalog, fault_type_hint
from copilot.tools import Cite, TOOL_SPECS, dispatch
from copilot.window import WindowContext
from copilot.workspace import Executor, PathPolicyError, Workspace, snapshot

# canonical event enum (ADR-0009) -- ONE vocabulary for the live stream AND the
# persisted events.jsonl. F3 emits a subset; gate (I4a) and artifact (B3b) types land in later tickets.
EVENT_TYPES = frozenset({
    "user_msg", "assistant_msg", "think", "tool_call", "tool_result", "gate", "artifact",
})


@dataclass(frozen=True)
class Event:
    """One trace event, stamped at occurrence (R2a, ADR-0009). `data` is the type-specific
    payload; `ts` is auto-set to the ISO-8601 UTC construction time unless one is passed --
    events are built exactly when they occur, so this IS occurrence time (not F4's old
    send-time stamp). `event_wire` serializes {type, ts, **data} for stream AND events.jsonl."""
    type: str
    data: dict
    ts: str = ""

    def __post_init__(self):
        assert self.type in EVENT_TYPES, \
            f"unknown event type {self.type!r} (not an ADR-0009 canonical type)"
        if not self.ts:
            object.__setattr__(self, "ts", datetime.now(timezone.utc).isoformat())


@dataclass(frozen=True)
class Outcome:
    answer: str | None                       # final answer OR the ask-back question
    events: tuple[Event, ...] = ()
    stopped: str | None = None               # None | "step_cap" | "tool_call_cap"
    cites: tuple[Cite, ...] = ()             # R6b: evidence this run gathered, for a master synthesis to inherit

    def of_type(self, t: str) -> tuple[Event, ...]:
        return tuple(e for e in self.events if e.type == t)


def event_wire(e: Event) -> dict:
    """Canonical wire/store shape (ADR-0009): type + occurrence ts + the event's payload.
    ONE schema for the live SSE stream (F4) AND the persisted events.jsonl (R2a). The
    payload owns no `type`/`ts` key, so the round-trip is lossless -- guard it."""
    assert "type" not in e.data and "ts" not in e.data, \
        f"event payload must not shadow type/ts (round-trip would clobber): {e.data!r}"
    return {"type": e.type, "ts": e.ts, **e.data}


SYSTEM_PROMPT = (
    "You are a read-only network investigator. Use the live-telemetry tools "
    "(query_metrics, search_logs, flows) to gather evidence within the given time "
    "window -- narrow every such call by device or pattern -- and the knowledge-base "
    "tools (search_runbooks, search_incidents) to find the matching runbook and similar "
    "past incidents (pass a device to search_incidents to focus on nearby topology). Use "
    "walk_topology_graph to see the blast-radius / downstream devices of a fault and their "
    "live status. Cite evidence by its bracketed id EXACTLY as it appears in the tool "
    "result (telemetry e.g. [metrics:0], [events:2], [flows:0]; a runbook/incident e.g. "
    "[runbook-mpls-ldp]) -- only cite an id you actually retrieved, list each separately, "
    "NEVER a range like [metrics:3-5]. Every sentence that names a device MUST "
    "carry such a citation, or it is rejected as unsupported. If the request is too vague "
    "to investigate, ask one clarifying question instead of calling a tool."
)

JUDGE_SYSTEM = (
    "You are a strict evidence auditor for a network investigation. Given the question, the "
    "evidence gathered (the tool results above), and a draft answer, judge whether that "
    "evidence is RELEVANT, SUFFICIENT, and CONSISTENT enough to support the answer. Reply with "
    'ONE JSON object and nothing else: {"pass": <bool>, "missing": [<str>], '
    '"contradictions": [<str>]}. missing = specific evidence still needed to answer; '
    "contradictions = gathered evidence that conflicts."
)

# I5 progressive disclosure (ADR-0012): the load_skill tool is advertised only when skills
# are wired, so the agent can auto-select a skill by its catalog description and pull the full
# method into context. A skill body is METHOD, not cited evidence -> it yields no Cite.
LOAD_SKILL_SPEC = {
    "name": "load_skill",
    "description": "Load the full method of a named diagnostic skill (see the skills list) "
                   "into context before investigating.",
    "parameters": {"type": "object", "required": ["name"],
                   "properties": {"name": {"type": "string"}}},
}

# B3a (ADR-0011/0013): the coding-agent bash tool, advertised only when a per-session Executor
# is wired (get to run code end-to-end). Runs in the B0 scratchpad, no network, time-limited
# (B2). Its output is ACTION, not cited evidence -> no Cite, like load_skill.
BASH_SPEC = {
    "name": "bash",
    "description": "Run a shell command in your scratchpad working directory (no network, "
                   "time-limited). Write a script with the file tools or a heredoc, run it, "
                   "and read its stdout back.",
    "parameters": {"type": "object", "required": ["command"],
                   "properties": {"command": {"type": "string"}}},
}

# B3b (ADR-0009/0010): present a generated output (a chart image or code) from the scratchpad --
# snapshot it into the append-only artifacts/ and emit an `artifact` event for the demo to render
# inline. A later overwrite of the source can't change the shown artifact (snapshot-on-present).
# Advertised only when a per-session Workspace is wired. It's an output, not a fact -> no Cite.
PRESENT_SPEC = {
    "name": "present",
    "description": "Show a file you generated in your scratchpad (a chart image, or a code/text "
                   "file) to the user; it is snapshotted into the case record. Pass its path and "
                   "an optional title.",
    "parameters": {"type": "object", "required": ["path"],
                   "properties": {"path": {"type": "string"},
                                  "title": {"type": "string"}}},
}

# TOOL_SPECS + dispatch live in copilot.tools (the registry, I1) -- re-exported here so
# the F3 agent API surface (copilot.agent) is unchanged. start/end are NOT advertised:
# the loop supplies the window, the model only narrows within it (ADR-0002).


def parse_tool_calls(content: str | None) -> tuple[ToolCall, ...]:
    """Owned fallback parser (ADR-0005) for backends without native function-calling:
    a single JSON object {"name": ..., "arguments": {...}} (the ToolCall shape) in the
    completion. Returns () for plain prose, so a normal answer falls through to terminal.

    R1 hardening: the call JSON must be the WHOLE trimmed turn or sit in a ```json fence --
    NOT scanned out of the middle of prose. gpt-oss-20b (and any real model) quotes JSON inside
    an ordinary answer ("the call would be {...}"); the old first-`{...}` scan read that as a
    tool call. A tool-call turn is one where the model emits only the call, so requiring it to
    lead (bare) or be fenced removes the false positive without losing the intended format.

    ponytail: one JSON object, one key spelling -- the simplest thing a weak model reliably
    emits. Upgrade to a multi-step ReAct grammar only if a real backend needs it.
    """
    obj = _tool_call_json(content or "")
    if not isinstance(obj, dict):
        return ()
    name = obj.get("name")
    args = obj.get("arguments", {})
    if not name or not isinstance(args, dict):
        return ()
    return (ToolCall(name=str(name), arguments=args, id="parsed_0"),)


CITE_RE = re.compile(r"\[[a-z_]+:\d+\]")   # evidence id shape the tools emit ([metrics:0], [events:2])


def compact_history(history: list[dict], *, max_chars: int) -> list[dict]:
    """I6/ADR-0015 §5 history compaction (config-gated): keep the most recent turns verbatim,
    collapse older ones into ONE leading "investigation so far" note, so a long session's prompt
    stays under `max_chars`. The note retains every cite id from the dropped turns, so an answer
    that still leans on prior evidence never loses the id (acceptance). Deterministic -- no LLM
    call: ADR-0015 rejects dump-then-summarize; a compact digest is the laziest thing that bounds
    the prompt. Same sliding-window idea as WindowContext.

    ponytail: two known ceilings the bound can be exceeded by -- (1) a single recent turn larger
    than the budget is kept whole (a turn can't be split); (2) if the dropped turns' cite ids alone
    exceed the note budget they are still all kept (never drop a cited id > hold the char bound).
    Upgrade to intra-turn truncation if either ceiling ever bites.
    """
    if not history or _chars(history) <= max_chars:
        return history                       # under budget -> unchanged (no note)
    note_cap = max(200, max_chars // 5)      # reserve a slice for the digest; the rest is the tail
    tail_budget = max_chars - note_cap
    kept: list[dict] = []
    total = 0
    for msg in reversed(history):            # newest -> oldest, verbatim while under budget
        c = len(msg.get("content") or "")
        if kept and total + c > tail_budget:  # always keep at least the latest turn
            break
        kept.append(msg)
        total += c
    kept.reverse()
    dropped = history[:len(history) - len(kept)]
    if not dropped:                          # only one (oversized) turn -> nothing to collapse
        return history
    return [_digest_note(dropped, note_cap)] + kept


def _chars(msgs: list[dict]) -> int:
    return sum(len(m.get("content") or "") for m in msgs)


def _digest_note(dropped: list[dict], cap: int) -> dict:
    """One compacted note standing in for the dropped turns. EVERY cite id in the dropped turns is
    kept in full -- cites are load-bearing (the gate keys on them), so the prose is truncated to
    leave room for them, not the other way round. The note stays <= `cap` unless head + cites alone
    already exceed it (ceiling 2 in compact_history), in which case cites still win."""
    prose = " | ".join(f"{m.get('role', '?')}: {m.get('content') or ''}" for m in dropped)
    head = f"Investigation so far ({len(dropped)} earlier turns compacted): "
    cites = list(dict.fromkeys(CITE_RE.findall(prose)))   # unique, order-preserving
    tail = (" cites: " + " ".join(cites)) if cites else ""
    body = prose[: max(0, cap - len(head) - len(tail))]   # reserve room for cites -> note <= cap
    return {"role": "system", "content": head + body + tail}


def investigate(question: str, window: WindowContext, *,
                llm: LLMClient, adapter: ToolAdapter, cfg: Config,
                retriever: Retriever | None = None,
                kg: dict[str, str] | None = None,
                skills: dict[str, Skill] | None = None,
                executor: Executor | None = None,
                workspace: Workspace | None = None,
                invoke: list[str] | None = None,
                history: list[dict] | None = None,
                fault_type: str | None = None,
                abstain: bool = False,
                drift_state: str | None = None) -> Outcome:
    """Run the loop until the model answers, asks back, or a cap trips. `kg` is the optional
    curated-KG hint map (ADR-0007): additive, never load-bearing -- the caller passes it only
    when cfg.kg_enabled (get_kg), so it's None here whenever the flag is off.

    `skills` is the I5 progressive-disclosure catalog (ADR-0012): {name: Skill}, each skill's
    description sits in the base prompt while its body loads on demand (the agent's load_skill
    tool). `invoke` = skill names a human manually selected -> their bodies are preloaded into
    the system prompt up front. Both default to nothing, so the loop is unchanged when no
    skills are wired.

    `history` (R2a, ADR-0009) is the prior conversation turns of a resumed session --
    {"role","content"} messages the SessionStore reconstructs from a session's events.jsonl
    -- inserted between the system prompt and the new question so the model sees where the
    chat left off. None (default) = a fresh single-turn run.

    `fault_type` / `abstain` (R4a, ADR-0003) come from the current Prediction Record (via the
    emulator seam, extracted by the caller with copilot.emulator.fault_type / is_abstain):
    `fault_type` is a soft steer for skill selection (ADR-0012, no rigid mapping); `abstain`
    (the PA made no confident call) softens the quality gate (ADR-0008 §Nuances); `drift_state`
    (the record's `health.drift_state`, T1) FLAGS the passing answer as low-trust when the model
    has drifted past cfg.drift_distrust_at -- it never blocks. All three default to nothing, so the
    loop is byte-identical to F3 when no prediction is wired. The runtime caller that reads the
    current record and passes these is downstream (R5 forensic chat / the predict loop)."""
    skills = skills or {}
    events: list[Event] = []

    def emit(type_: str, **data) -> None:
        events.append(Event(type_, data))

    emit("user_msg", content=question)
    # progressive disclosure: descriptions always; a manually-invoked skill's body up front.
    system = SYSTEM_PROMPT
    if skills:
        system += "\n\n" + catalog(skills)
        hint = fault_type_hint(fault_type)               # R4a: the record's fault_type steers
        if hint:                                         # which skill the agent picks (ADR-0012)
            system += "\n" + hint
    for name in (invoke or ()):
        s = skills.get(name)
        if s:                                            # ponytail: an unknown manual-invoke
            system += f"\n\n[skill: {s.name}]\n{s.body}"  # name silently no-ops -- the UI picks
        #                                                  from the catalog, so a miss is rare;
        #                                                  add a warning event if humans hand-type it.
    tool_specs = (TOOL_SPECS + ([LOAD_SKILL_SPEC] if skills else [])
                  + ([BASH_SPEC] if executor else [])     # B3a: bash only when exec is wired
                  + ([PRESENT_SPEC] if workspace else []))  # B3b: present only when workspace wired
    messages = [{"role": "system", "content": system}]
    hist = history or []                   # R2a: prior turns reach the model on resume (ADR-0009)
    if cfg.history_compaction and hist:    # I6/ADR-0015 §5: flag off -> hist untouched, byte-identical
        hist = compact_history(hist, max_chars=cfg.history_max_chars)
    messages += hist
    messages.append({"role": "user", "content": question})
    tool_calls = 0
    retries = 0                                           # agentic gate retries used (ADR-0008)
    evidence: list[Cite] = []                            # structured cites gathered, for the gate
    tool_errors: list[str] = []                          # guidance errors from failed calls (gate)

    for _ in range(cfg.step_cap):
        reply = llm.chat(messages, tools=tool_specs)
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
                            min_evidence=cfg.gate_min_evidence, tool_errors=tool_errors,
                            abstain=abstain)
            # stage 2 only over stage-1 survivors (self_judge sees the sanitized transcript).
            missing = gate.missing
            if not missing:
                judged = self_judge(llm, messages, text).missing
                # R4a: an abstaining PA softens SUFFICIENCY only -- self_judge's "insufficient"
                # verdict is dropped ("no confident call, here's the evidence" is a valid answer),
                # but a CONTRADICTION (the answer conflicts with its own evidence) still blocks:
                # abstain licenses a thin answer, never a self-inconsistent one (ADR-0008 §Nuances).
                missing = (tuple(m for m in judged if m.startswith("contradiction:"))
                           if abstain else judged)
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
            emit("gate", ok=True, missing=[], retry=retries)  # R2a: gate outcome visible on pass too
            # T1 (story 14): the PA's model-health rung flags -- never blocks -- the passing answer
            # when it has drifted past cfg.drift_distrust_at (ADR-0003; drift_state read from the
            # current record's health.drift_state by the caller). No signal -> byte-identical to F3.
            banner = trust_banner(drift_state, distrust_at=cfg.drift_distrust_at)
            if banner:
                text = banner + "\n\n" + text
            emit("assistant_msg", content=text)
            return Outcome(answer=text, events=tuple(events), cites=tuple(evidence))

        if native and reply.content:                     # reasoning alongside a call
            emit("think", content=reply.content)
        # R1 hard break (ADR-0004): the requested calls MUST ride the assistant turn's
        # `tool_calls` field -- an OpenAI-compatible server rejects the following `tool`
        # message otherwise. Native reasoning stays in `content`; a parsed (owned-fallback)
        # call has none (its JSON IS the call), so content=None there.
        messages.append(_assistant_turn(reply.content if native else None, calls))

        for tc in calls:
            if tool_calls >= cfg.tool_call_cap:
                return _capped(events, "tool_call_cap")
            tool_calls += 1
            emit("tool_call", name=tc.name, arguments=tc.arguments, id=tc.id)
            artifact = None                              # B3b: set by present -> an artifact event
            if tc.name == "load_skill":                  # I5: a skill body is method, not
                observation, cites = _load_skill(skills, tc.arguments), ()  # evidence -> no
            elif tc.name == "bash" and executor:         # B3a: run code in the scratchpad; the
                observation, cites = _run_bash(executor, tc.arguments), ()  # output is action, not
            elif tc.name == "present" and workspace:     # B3b: snapshot a presented output; it
                observation, artifact = _present(workspace, tc.arguments)  # rides its own artifact
                cites = ()                               # event, and is an output, not a Cite.
            else:                                        # a load/present miss never gate-blocks.
                observation, cites = dispatch(tc.name, tc.arguments, adapter, window, retriever, kg)
                evidence.extend(cites)
                if observation.startswith("error:"):    # guidance error -> a failed call (gate)
                    tool_errors.append(observation)
            emit("tool_result", id=tc.id, name=tc.name, content=observation, n=len(cites))
            if artifact is not None:                     # B3b: the snapshotted output rides its own
                emit("artifact", **artifact)             # canonical event after the tool_result
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


def _load_skill(skills: dict[str, Skill], args: dict) -> str:
    """I5 (ADR-0012): return a named skill's body (the method), or guidance if unknown -- a
    bad name comes back AS an observation (ADR-0015), never a raise, so the model can retry."""
    name = args.get("name")
    s = skills.get(name) if name else None
    if s is None:
        return f"error: unknown skill {name!r}; pick one from the skills list"
    return s.body


def _run_bash(executor: Executor, args: dict) -> str:
    """B3a (ADR-0011/0013): run the model's `command` through the B2 executor (no-net, timeout,
    cwd=scratchpad) and render the result as one observation. A missing command / a refused run
    (no-net sandbox unavailable) comes back AS guidance (ADR-0015), never a raise. Output is the
    thing the model reads next -- exit line + stdout (+ stderr if any)."""
    command = args.get("command")
    if not command or not isinstance(command, str):
        return "error: bash needs a 'command' string to run"
    r = executor.run(command)
    if r.refused:
        return f"error: {r.stderr}"
    head = f"exit={r.returncode}" + (" (timed out)" if r.timed_out else "")
    parts = [head]
    if r.stdout:
        parts.append(r.stdout.rstrip("\n"))
    if r.stderr:
        parts.append("stderr:\n" + r.stderr.rstrip("\n"))
    return "\n".join(parts)


def _present(ws: Workspace, args: dict) -> tuple[str, dict | None]:
    """B3b (ADR-0009/0010): snapshot the model's presented `path` into the append-only artifacts/
    and return (observation, artifact-event-payload). A missing path / a file outside the scratchpad
    / an unreadable file comes back AS guidance (ADR-0015), never a raise, with a None payload (no
    artifact event emitted). The snapshot freezes the bytes now, so a later overwrite of the source
    can't change the shown artifact."""
    path = args.get("path")
    if not path or not isinstance(path, str):
        return "error: present needs a 'path' to a file in your scratchpad", None
    try:
        art = snapshot(ws, path, title=args.get("title"))
    except PathPolicyError:                              # writable()'s message talks about writes;
        return (f"error: cannot present {path!r}: present a file inside your scratchpad "  # reframe
                f"(copy it in first if it lives elsewhere)", None)
    except OSError as e:                                  # missing / not-a-file / unreadable
        return f"error: cannot present {path!r}: {getattr(e, 'strerror', None) or e}", None
    return f"presented {art.source!r} as {art.name} ({art.kind})", art.event()


def _capped(events: list[Event], why: str) -> Outcome:
    msg = f"stopped: {why} reached before a conclusion"
    events.append(Event("assistant_msg", {"content": msg}))
    return Outcome(answer=msg, events=tuple(events), stopped=why)


def _assistant_turn(content: str | None, calls: tuple[ToolCall, ...]) -> dict:
    """The assistant message that carries the model's tool calls in OpenAI wire shape (R1).
    Every `tool` message the loop appends next matches one of these `tool_calls` by id, so a
    real backend accepts the round trip (the F1 stub ignores the shape)."""
    return {"role": "assistant", "content": content, "tool_calls": [
        {"id": c.id or f"call_{i}", "type": "function",
         "function": {"name": c.name, "arguments": json.dumps(c.arguments)}}
        for i, c in enumerate(calls)]}


def _tool_call_json(text: str) -> dict | None:
    """The tool-call object only when the WHOLE turn is that call -- a bare object or a lone
    ```json fence with nothing else around it (R1). A turn that mixes prose with the JSON (an
    answer quoting an example, or reasoning followed by a fenced block) returns None and falls
    through to a terminal answer, so quoted/illustrative JSON is never misread as a call."""
    t = text.strip()
    m = re.fullmatch(r"```(?:json)?\s*(\{.*\})\s*```", t, re.DOTALL)
    if m:
        return _first_json_object(m.group(1))
    if t.startswith("{"):
        return _first_json_object(t)
    return None


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
