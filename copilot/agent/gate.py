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

# ADR-0003 model-health degradation ladder (research/07): R0 healthy .. R5 fully degraded.
# ponytail: a frozen 6-rung taxonomy, mirrored (not shared) across gate/emulator/config -- config
# can't import this (config <- gate <- tools <- retrieval <- embedder <- config cycle), and the
# gate stays decoupled from any one producer (a real PA emits these same rungs). Single source of
# truth is ADR-0003; a rung never gets added.
DRIFT_LADDER = ("R0", "R1", "R2", "R3", "R4", "R5")


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


def pre_gate(cites, *, window, entities, min_evidence, soft=False) -> GateResult:
    """Deterministic sufficiency + topicality check over gathered `Cite`s (ADR-0008 stage 1).

    `soft` (ADR-0008 §Nuances: the Prediction Record's `abstain==true`) drops the SUFFICIENCY
    checks -- the evidence floor + the "every named entity has support" requirement -- because
    "anomalous, no confident call, here's the evidence" is a valid answer that by definition
    lacks a full case. The INTEGRITY checks stay on: whatever the answer does cite must still be
    in-window and on-topic, so a soft gate never licenses out-of-window or off-topic evidence."""
    start, end = window.start, window.end            # R3: window is a WindowContext, not a tuple
    missing = []
    if not soft and len(cites) < min_evidence:       # sufficiency: relaxed on abstain
        missing.append(f"thin evidence: {len(cites)} item(s) < required {min_evidence}")
    for c in cites:
        if c.source not in WINDOWED_SOURCES:             # KB / topo exempt (see WINDOWED_SOURCES)
            continue
        if c.ts is None or not (start <= c.ts <= end):   # integrity: in-window, even when soft
            missing.append(f"out-of-window: {c.id} ts={c.ts} not in [{start},{end}]")
        if entities and c.device and c.device.lower() not in entities:
            missing.append(f"off-topic: {c.id} on {c.device} not in question entities")
    if not soft:                                         # sufficiency: every entity supported
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


def run_gate(answer: str, cites, *, window, question, min_evidence, tool_errors=(),
             abstain=False, prior_cites=()) -> GateResult:
    """I4a stage-1 gate (ADR-0008): tool-call success + deterministic pre-gate + citation check,
    combined. Stage-2 (self-judge LLM) + the bounded agentic retry on fail are I4b (#14).

    `abstain` (R4a): the Prediction Record abstained -> soften the pre-gate's sufficiency checks
    (ADR-0008 §Nuances). Integrity (tool-call success, in-window, on-topic, citation) is unmoved.

    `prior_cites` (R6b, ADR-0014): evidence a master synthesis inherits from its sub-chats. A
    synthesis merges FINDINGS, not telemetry, so it gathers no `Cite`s of its own -- without this
    it fails the sufficiency floor (0 items) and every device sentence reads as an uncited claim.
    Promoted to FIRST-CLASS, not exempted: prior cites carry the sub-chat's real source/ts/device,
    so they ride the SAME in-window/on-topic integrity checks as gathered cites -- a synthesis can
    no more cite out-of-window evidence than an ordinary answer can. Empty for a single-fault
    investigation, so that path is byte-identical (the regression the ticket asks for)."""
    all_cites = (*cites, *prior_cites)
    calls = tool_calls_ok(tool_errors)
    pre = pre_gate(all_cites, window=window, entities=extract_entities(question),
                   min_evidence=min_evidence, soft=abstain)
    cite = citation_check(answer, {c.id for c in all_cites})
    return _result([*calls.missing, *pre.missing, *cite.missing])


def trust_banner(drift_state, *, distrust_at) -> str | None:
    """T1 / spec #3 story 14: distrust a degraded model. When the PA's model-health rung
    (`health.drift_state` -- ADR-0003's R0-R5 ladder, the location #20 resolved: ADR-0003 folds
    it INTO the record over PA.md §3.5's separate surface) has climbed to/past `distrust_at`,
    return a banner flagging the answer as resting on a degraded model; a healthy rung, no signal,
    or an unrecognized one returns None, leaving the answer byte-identical.

    Sibling to pre_gate/citation_check but it FLAGS, never BLOCKS: a drifting model still answers,
    it just says so -- refusing to answer whenever drift climbs would be worse than a caveated
    answer. The loop (copilot.agent.loop) prepends the banner to the passing answer."""
    try:
        rung, floor = DRIFT_LADDER.index(drift_state), DRIFT_LADDER.index(distrust_at)
    except ValueError:
        return None                                      # no/unknown signal -> no fabricated distrust
    if rung < floor:
        return None
    return (f"⚠ low trust: model health has drifted to {drift_state} (≥ {distrust_at}); "
            f"verify this prediction independently before acting.")


def _sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?\n])\s+", text.strip()) if s.strip()]
