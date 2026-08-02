# ADR-0008 — Quality gate

**Status:** accepted

## Decision

A **two-stage gate** between "gathered evidence" and "allowed to answer":

1. **Deterministic pre-gate** (pure code, fail-fast): every tool call succeeded / non-empty; each
   evidence item's timestamp ∈ `WindowContext`; evidence device/entity ∈ the question's entities;
   ≥ `N` items and every named entity has ≥1 support.
2. **Single self-judge LLM call** over survivors → `{pass, missing[], contradictions[]}` for the
   semantic side (relevance / sufficiency / consistency).

On **fail** → the agent **re-enters the loop to fetch the `missing[]`**, up to **`gate_max_retries`
(2)**; if still failing → report what's missing (the `missing[]` list *is* the message). On **pass**
→ the agent answers and every claim must map to an evidence id (a cheap deterministic citation
check; uncited claim → back to the agent).

`N` and `gate_max_retries` in config.

## Context

A rigorous "is this evidence sufficient/consistent" checker is open research. A small model bluffs;
the gate catches the bluff. Split cheap-deterministic from the one judgment call.

## Alternatives rejected

- **Separate critic model** from day one — deferred; **self-judge for v1**.
  `# ponytail:` self-judge; upgrade to a separate critic if it rubber-stamps.
- **Per-claim NLI / a framework** — rejected; one structured call.
- **Give up on fail** — rejected by user; the retry loop is redundancy for the small model's misses.

## Nuances

- The retry loop reuses the agent loop (ADR-0005). Retry cap + tool-call cap prevent runaway.
- `abstain==true` from the Prediction Record softens the gate — "anomalous, no confident call, here's
  the evidence" is a valid answer.

## Consequences

- Honest failure ("here's what I'd need") instead of forced guesses; every passed answer is cited.
