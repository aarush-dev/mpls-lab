"""copilot.agent.gate -- I4a stage-1 quality gate: deterministic pre-gate + citation check (ADR-0008).

Pure, fail-fast checks between "gathered evidence" and "allowed to answer" (no LLM):
  tool_calls_ok  -- every tool call succeeded (a guidance error means the model tried to
                    gather evidence and failed).
  pre_gate       -- >= N evidence items; each windowed item's ts inside the window; every
                    entity named in the question has >= 1 supporting item, and no windowed
                    read is off-topic (on a device the question never named).
  citation_check -- the answer cites no fabricated id, every device-anchored claim carries a
                    citation, and a non-empty answer cites something.
  run_gate       -- the three combined; its `missing[]` is the message on fail.
On fail ADR-0008 makes `missing[]` the message (and, in I4b, what the bounded agentic retry
re-fetches). Stage-2 (the self-judge LLM call) + that retry are I4b (#14), not here.
Run:  python3 -m copilot.agent.test_gate
"""
import re
from dataclasses import dataclass

from copilot.tools import Cite

# windowed live-telemetry sources (ADR-0002): only these carry a ts that must fall INSIDE the
# investigation window AND must be on-topic. KB docs (runbook/incident) carry a HISTORICAL ts
# and cover hop-relevant devices; topology nodes carry no ts and cover blast-radius neighbours
# (ADR-0007) -- both legitimately concern devices the question never named, so both are exempt.
WINDOWED_SOURCES = frozenset({"metrics", "events", "flows"})

# a device/entity token in lab naming (r1, rr1, pe3, p1, ce2, asbr1). ponytail: a role-prefix
# whitelist, not a device registry -- it must NOT over-match, or a protocol/interface token
# (as65001, ge0, v4, pop3) becomes a "required entity" the gate can never see evidence for,
# wedging every such question. Upgrade to intersecting the adapter's topology node set if
# device naming outgrows these prefixes.
ENTITY_RE = re.compile(r"\b(?:rr|r|pe|p|ce|asbr)\d+\b", re.IGNORECASE)
CITE_RE = re.compile(r"\[([^\[\]]+)\]")


@dataclass(frozen=True)
class GateResult:
    ok: bool
    missing: tuple[str, ...] = ()


def _result(missing) -> GateResult:
    return GateResult(not missing, tuple(dict.fromkeys(missing)))


def extract_entities(question: str) -> frozenset[str]:
    """Device/entity names the question is about (lowercased)."""
    return frozenset(m.group(0).lower() for m in ENTITY_RE.finditer(question))


def tool_calls_ok(errors) -> GateResult:
    """ADR-0008 check 1: every tool call succeeded. A guidance error (ADR-0015) from a call
    means the model tried to gather evidence and failed -> block (I4b re-fetches it on retry)."""
    return _result([f"failed tool call: {e}" for e in errors])


def pre_gate(cites, *, window, entities, min_evidence) -> GateResult:
    """Deterministic sufficiency + topicality check over gathered `Cite`s (ADR-0008 stage 1)."""
    start, end = window
    missing = []
    if len(cites) < min_evidence:
        missing.append(f"thin evidence: {len(cites)} item(s) < required {min_evidence}")
    for c in cites:
        if c.source not in WINDOWED_SOURCES:             # KB / topo exempt (see WINDOWED_SOURCES)
            continue
        if c.ts is None or not (start <= c.ts <= end):   # windowed evidence MUST be in-window
            missing.append(f"out-of-window: {c.id} ts={c.ts} not in [{start},{end}]")
        if entities and c.device and c.device.lower() not in entities:
            missing.append(f"off-topic: {c.id} on {c.device} not in question entities")
    supported = {c.device.lower() for c in cites if c.device}
    for e in sorted(entities):
        if e not in supported:
            missing.append(f"off-topic: no evidence for entity {e!r}")
    return _result(missing)


def citation_check(answer: str, valid_ids) -> GateResult:
    """Cheap deterministic citation check (ADR-0008): every claim maps to a real evidence id."""
    cited = set(CITE_RE.findall(answer))
    missing = []
    unknown = cited - set(valid_ids)
    if unknown:
        missing.append(f"fabricated citation(s): {sorted(unknown)}")
    for sent in _sentences(answer):
        # a device-anchored sentence is a claim; it must carry a citation.
        if ENTITY_RE.search(sent) and not CITE_RE.search(sent):
            missing.append(f"uncited claim: {sent.strip()[:80]!r}")
    if not cited and not missing:
        missing.append("uncited answer: no [evidence-id] citations")
    return _result(missing)


def run_gate(answer: str, cites, *, window, question, min_evidence, tool_errors=()) -> GateResult:
    """I4a stage-1 gate (ADR-0008): tool-call success + deterministic pre-gate + citation check,
    combined. Stage-2 (self-judge LLM) + the bounded agentic retry on fail are I4b (#14)."""
    calls = tool_calls_ok(tool_errors)
    pre = pre_gate(cites, window=window, entities=extract_entities(question),
                   min_evidence=min_evidence)
    cite = citation_check(answer, {c.id for c in cites})
    return _result([*calls.missing, *pre.missing, *cite.missing])


def _sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?\n])\s+", text.strip()) if s.strip()]
