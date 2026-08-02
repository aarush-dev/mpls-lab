# ADR-0017 — Milestones & eval

**Status:** accepted

## Decision

### Two milestones, one loop

- **Milestone A — Investigator**: the 5 read-only investigation tools, retrieval, quality gate, two
  systems, windowing, memory/cases, skills, context management, injection guard, FastAPI + demo app.
  A complete, demoable system on its own.
- **Milestone B — Coding agent**: workspace tools, scratchpad, sandboxed execution, artifacts,
  `ledger_to_kb`. Layers onto A's loop.

Both are in scope. The user rejected **deferring the grilling** of B ("both need to be done so i
dont see a point in doing them one after the other") — so B was grilled now, not later. Build/ticket
order is still A-then-B because B layers on A's loop; that's a ticket-ordering choice, not a scope
deferral.

### Tool set is provisional

The "5 investigation tools" is a **starting set**, kept/cut/merged based on **measured performance**.
Tool-usage data accrues **free** via `events.jsonl` (`tool_call` events, ADR-0009) from day one.

### Eval — deferred to post-build

A **replay eval harness** scoring against `/labels` ground truth (fault-type, root-cause
localization, time-to-impact, citation validity, gate precision/recall, abstain correctness) + per-
tool usage stats for pruning. **Deferred until the system is finalized** (user's call). Deterministic
via `error_profile=oracle` (ADR-0003). Any air-gap-property eval runs on `unsloth-local` (ADR-0004).

## Alternatives rejected

- **Sequence B strictly after A ships** — softened; grill/spec both now, build A first.
- **Build eval alongside** — rejected by user; defer scoring, but capture tool-usage free meanwhile.

## Nuances

- Content-seeding tickets (incident corpus, runbooks, skills, eval scenarios) run **parallel /
  non-blocking**; code ships against fixtures first.

## Consequences

- A demoable investigator first; the coding agent and scoring come after, without rework.
