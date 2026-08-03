"""copilot.agent.gate -- I4a stage-1 quality gate: deterministic pre-gate + citation check (ADR-0008).

Two pure, fail-fast checks between "gathered evidence" and "allowed to answer" (no LLM):
  pre_gate       -- >= N evidence items; each windowed item's ts inside the window; every
                    entity named in the question has >= 1 supporting evidence item.
  citation_check -- the answer cites no fabricated id, every device-anchored claim carries a
                    citation, and a non-empty answer cites something.
On fail each returns the `missing[]` reasons -- ADR-0008 makes that list the message (and, in
I4b, what the bounded agentic retry re-fetches). Stage-2 (the self-judge LLM) is I4b (#14).
Run:  python3 -m copilot.agent.test_gate
"""
import re
from dataclasses import dataclass

from copilot.tools import Cite

# windowed live-telemetry sources (ADR-0002): only these carry a ts that must fall INSIDE the
# investigation window. KB docs (runbook/incident) carry a HISTORICAL ts and topology nodes
# none -- exempt, else valid past-incident / structural evidence would be wrongly rejected.
WINDOWED_SOURCES = frozenset({"metrics", "events", "flows"})

# a device/entity token in lab naming (r1, pe1, ce2, rr1...). ponytail: a regex, not NER or a
# known-device set -- the lab names devices <=4 letters + digits. Upgrade to the topology node
# set (adapter) or NER if names diverge.
ENTITY_RE = re.compile(r"\b[a-z]{1,4}\d+\b", re.IGNORECASE)
CITE_RE = re.compile(r"\[([^\[\]]+)\]")


@dataclass(frozen=True)
class GateResult:
    ok: bool
    missing: tuple[str, ...] = ()


def extract_entities(question: str) -> frozenset[str]:
    """Device/entity names the question is about (lowercased)."""
    return frozenset(m.group(0).lower() for m in ENTITY_RE.finditer(question))


def pre_gate(cites, *, window, entities, min_evidence) -> GateResult:
    """Deterministic sufficiency check over gathered `Cite`s (ADR-0008 stage 1)."""
    start, end = window
    missing = []
    if len(cites) < min_evidence:
        missing.append(f"thin evidence: {len(cites)} item(s) < required {min_evidence}")
    for c in cites:
        if c.source in WINDOWED_SOURCES and c.ts is not None and not (start <= c.ts <= end):
            missing.append(f"out-of-window: {c.id} ts={c.ts} not in [{start},{end}]")
    supported = {c.device.lower() for c in cites if c.device}
    for e in sorted(entities):
        if e not in supported:
            missing.append(f"off-topic: no evidence for entity {e!r}")
    # ponytail: assert every NAMED entity is supported, NOT the reverse (every evidence device
    # in entities) -- blast-radius (ADR-0007) deliberately surfaces neighbour devices the
    # question never named, so a strict reverse check would wrongly block it. Add the reverse
    # only if real off-topic drift shows up.
    return GateResult(not missing, tuple(dict.fromkeys(missing)))


def citation_check(answer: str, valid_ids) -> GateResult:
    """Cheap deterministic citation check (ADR-0008): every claim maps to a real evidence id."""
    valid = set(valid_ids)
    cited = set(CITE_RE.findall(answer))
    missing = []
    unknown = cited - valid
    if unknown:
        missing.append(f"fabricated citation(s): {sorted(unknown)}")
    for sent in _sentences(answer):
        # a device-anchored sentence is a claim; it must carry a citation.
        if ENTITY_RE.search(sent) and not CITE_RE.search(sent):
            missing.append(f"uncited claim: {sent.strip()[:80]!r}")
    if not cited and not missing:
        missing.append("uncited answer: no [evidence-id] citations")
    return GateResult(not missing, tuple(dict.fromkeys(missing)))


def _sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?\n])\s+", text.strip()) if s.strip()]
