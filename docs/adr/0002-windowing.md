# ADR-0002 — Windowing (Live vs Forensic)

**Status:** accepted

## Decision

Windowing is a **copilot-owned `WindowContext {start, end, frozen}`** threaded into every tool call.
Not a shared service.

Three cases (one struct, different `start`/`end`/`frozen`):

- **Live monitoring** — rolling: `end=now`, `start=now−X`, recomputed each tool call.
- **Query** — the window is **whatever time period the human's question names** — an arbitrary,
  possibly historical `[start, end]` ("what happened last Tuesday 3–4pm"). The agent resolves it from
  the question (or asks a clarifying question); if none is given, it defaults to rolling `now−X`. Not
  frozen — a Query can span past *and* live. This is a **distinct case, not Live-rolling.**
- **Forensic** — frozen: `end=T_snapshot`, `start=T−X`, pinned once. The agent is **forbidden to
  pass any `end > T_snapshot`** — that's what "no live data leaks in" means, enforced at the adapter.

`X` configurable (default ~10 min; tune to cascade timescale). Single `X` for both to start; split
into `live_X` / `forensic_X` only if one proves wrong.

## Context

Every backend tool already takes `start`/`end` epoch params (`dataapi/app.py` — `/metrics`,
`/events`). So "windowing" is not a new service; it's *which `start`/`end` the agent passes*.

## Alternatives rejected

- **Shared windowing service** feeding both copilot and prediction stack — rejected; couples the two
  workstreams. The prediction stack bounds its own reads independently.

## Nuances

- Forensic freeze needs the underlying data still queryable at `T−X` later → **retention floor**
  (≥7d, ADR-0009) OR the case snapshots the window to disk at capture time. We do **both**: retention
  floor + Case Archive materializes a frozen copy (ADR-0009).
- Same sliding-window idea is reused for chat history compaction (ADR-0015).

## Consequences

- No new component — `WindowContext` is a struct + an adapter guard.
- Forensic reproducibility comes free from the frozen `end` + the on-disk window copy.
