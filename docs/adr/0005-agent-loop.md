# ADR-0005 — Agent loop

**Status:** accepted

## Decision

The agent loop is **owned code (~150 lines)**: `think → pick tool → run → observe → decide → …→
cited answer`. Patterns borrowed from **little-coder** as a *reference*, not a vendored dependency.

- Tool-call parsing: native OpenAI function-calling when available, else an owned JSON/ReAct parser.
- Guards: a per-investigation **step cap** and **tool-call cap** (config) to prevent runaway.
- The loop also supports **ask-back** — responding to the human with a clarifying question instead of
  a tool call (Query system, ADR-0001).

## Context

A loop over ~9 fixed tools with an HTTP model backend is a prompt template + a parser + a dispatch
table + a step cap. That's less code than vendoring and auditing a framework for air-gap.

## Alternatives rejected

- **Adopt a framework** (little-coder / a "ReGain" RAG framework) as installed dependencies —
  rejected. "ReGain" is a **phantom** — no such package/paper exists; searching agentic-RAG
  literature found nothing. Its intended value dissolved into concrete features (ADR-0006, ADR-0007).
  little-coder is real but is a *coding* agent — we take its invariants (ADR-0011), not its runtime.

## Nuances

- The gate's retry loop (ADR-0008) reuses this same loop machinery — no separate subsystem.
- Milestone B's workspace tools plug into this exact loop later (ADR-0011).

## Consequences

- No framework to vendor air-gapped; nothing external to die on.
- Full control over prompt, parsing, and tool shapes for a weak model.
