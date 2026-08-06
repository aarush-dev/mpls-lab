# ADR-0002 — Windowing (Live vs Forensic)

**Status:** accepted

## Decision

Windowing is a **copilot-owned `WindowContext {start, end, frozen}`** threaded into every tool call.
Not a shared service.

Four cases (one struct, different `start`/`end`/`frozen`):

- **Live monitoring** — rolling: `end=now`, `start=now−X`, recomputed each tool call.
- **Query** — the window is **whatever time period the human's question names** — an arbitrary,
  possibly historical `[start, end]` ("what happened last Tuesday 3–4pm"). The agent resolves it from
  the question (or asks a clarifying question); if none is given, it defaults to rolling `now−X`. Not
  frozen — a Query can span past *and* live. This is a **distinct case, not Live-rolling.**
- **Forensic** — frozen: `end=T_snapshot`, `start=T−X`, pinned once. The agent is **forbidden to
  pass any `end > T_snapshot`** — that's what "no live data leaks in" means, enforced at the adapter.
- **Salvage (anchored-live)** — `end=now`, `start=max(buildup_start, now−X_max)`, **not frozen**.
  For real-time fault salvage (prefactor, #90): the lower bound is **pinned at the fault's
  buildup-start** so the earliest precursor evidence never scrolls out as the fault runs long
  (unlike Live's rolling `now−X`), yet the end tracks `now` (unlike frozen Forensic). `X_max`
  (`window_x_max`) caps the lookback so a pathological long episode can't grow the window unbounded
  and blow up query cost. **Live-window trust rule:** no reverse/freeze guard is needed — salvage
  just never freezes, so there is no `T_snapshot` for the adapter to enforce.

`X` configurable (default ~10 min; tune to cascade timescale). `X_max` (default ~60 min) is the
salvage lookback ceiling. Single `X` for live/forensic to start; split into `live_X` / `forensic_X`
only if one proves wrong.

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
- **KB retrieval is exempt from windowing.** `search_runbooks`/`search_incidents` (ADR-0006) take
  **no** window: a runbook/past-incident `ts` is **historical**, not a live-telemetry timestamp, so
  the quality gate already excludes those sources from its in-window check (ADR-0008;
  `gate.py` `WINDOWED_SOURCES` = metrics/events/flows only). Threading a window into KB search would
  wrongly hide relevant history. So "*every* tool call is threaded" means every **windowed** (live-
  telemetry) tool call; `registry._retrieve` intentionally takes no window. Decided in R3.

## Consequences

- No new component — `WindowContext` is a struct + an adapter guard.
- Forensic reproducibility comes free from the frozen `end` + the on-disk window copy.
