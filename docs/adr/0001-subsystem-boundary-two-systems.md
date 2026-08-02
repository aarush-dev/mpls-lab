# ADR-0001 — Subsystem boundary & the two systems

**Status:** accepted

## Decision

The Copilot subsystem is the LLM-facing half of the pipeline. It is **two systems on one
conversational agent core**:

- **Forensic system** — auto-fires when the PA reports a fault → freezes what it saw → produces a
  root-cause report → stays open for follow-up chat.
- **Query system** — human-initiated → investigates past + live data on demand → may ask clarifying
  questions.

Both share the agent loop, tools, retrieval, quality gate, and memory. They differ only in **who
starts them** and **the data window** (rolling vs frozen — ADR-0002).

## Context

The merged plan framed "Live vs Forensic" as two *modes* of one agent. The user's actual mental
model is two *use-cases*: a self-firing postmortem system and an ask-anything system. They overlap
~90% — same core, different trigger + window.

## Alternatives rejected

- **Two separate agents/codebases** — rejected; they share almost everything, duplication.
- **Doc A's `/predict` + `/explain` two-endpoint split** (PA.md §3.1) — **killed**. The copilot *is*
  the explain path now; there is no separate explanation endpoint. Explanation is what the agent
  produces on demand inside whichever system is active.
- **Windowing as a shared service wrapping every expert** (§6 wording) — rejected as a component;
  it's a *concept*. Each consumer bounds its own reads (ADR-0002).

## Nuances

- Forensic is **also conversational** — after its auto-report it accepts follow-up Q&A over the
  frozen window. So both systems are chat; Forensic just starts frozen + auto-triggered.
- A **Query** chat that uncovers a real incident does **not** auto-become a case (no promotion —
  ADR-0009). Cases are born only from the Forensic trigger.

## Consequences

- One agent core to build (ADR-0005); two thin wrappers (trigger+freeze; chat entry).
- Clean boundary: nothing in the copilot reaches into the prediction stack — the only seam is the
  Prediction Record (ADR-0003).

## Open (resolve in ticket)

- Diagnosis output format (structured vs prose) — user to supply reference transcripts (ADR-0010).
