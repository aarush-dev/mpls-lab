# ADR-0015 — Context management

**Status:** accepted

## Decision

Keep the small model's context from overflowing via **small-by-construction tools**, not
dump-then-summarize:

1. **Mandatory filter system.** Investigation-tool signatures **require** narrowing — time window
   (from `WindowContext`), a device/entity or pattern, and a **hard low `limit` cap**. The adapter
   **rejects** unfiltered / over-broad calls with guidance ("specify a device or pattern"). So
   `search_logs` cannot return 1000 rows.
2. **Small footprint by design** — compact fields, low default limit. **No automatic summarization**
   of tool results (may add as an *optional* later).
3. **Drill-down / paging** for when more is genuinely needed.
4. **Frozen forensic window is queried via the same filtered tools**, never loaded whole.
5. **History compaction** — config-gated (`history_compaction`; **default off** — the user asked for
   it as a config setting and said it's "not needed... something we could look into", so off is the
   conservative default they implied, not one they stated): summarize older turns
   into a running "investigation so far" note, keep recent turns verbatim. Same sliding-window idea
   as `WindowContext`.

## Context

Retrieval chunks the *corpus*, but the overflow sources are **raw tool results** (`/events` ≤1000
rows, `/metrics` arrays), **chat history**, and the **forensic window**. A weak model won't
self-limit; the system must bound it cheaply.

## Alternatives rejected

- **Dump raw results then LLM-summarize** — rejected by user; prefer tools that return a small
  footprint by construction.
- **Rely on a bigger context window** — rejected; the model is small on purpose.

## Nuances

- The model must know *what* to filter on → a **diagnostic skill** ("how to query narrowly",
  ADR-0012) + iterative retrieval (ADR-0006).
- Enforcement = required params + caps at the tool adapter (ADR-0006).

## Consequences

- The model always sees small results + handles, fetching detail deliberately. This is what makes a
  small model survive big telemetry.
