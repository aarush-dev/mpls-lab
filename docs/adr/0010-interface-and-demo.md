# ADR-0010 — Interface & demo app

**Status:** accepted

## Decision

- **FastAPI service, local-only** (matches `dataapi`, `127.0.0.1`) is the core interface. The real
  NOC dashboard integrates through it later.
- The chat endpoint **streams a structured, timestamped step-trace** — `think → tool_call →
  observation → gate verdict → answer` — not just final text. This is the persisted `events.jsonl`
  stream (ADR-0009) surfaced live, **using ADR-0009's canonical event enum** (`user_msg |
  assistant_msg | think | tool_call | tool_result | gate | artifact`). The stage words above are
  readable aliases, not new types: `observation`=`tool_result`, `answer`=`assistant_msg`. Stream and
  store share ONE schema so every streamed event round-trips into the log unchanged.
- A **demo app** consumes that trace to *show what the agent is doing* (live tool calls, evidence,
  citations, gate pass/fail), inspired by agentic apps. Throwaway-ish until the dashboard exists.

## Context

The Query system is interactive/multi-turn; Forensic auto-reports then accepts follow-ups. A UI is
made later inside an existing dashboard; until then a small demo app shows all abilities.

## Alternatives rejected

- **CLI/REPL only** — rejected; a UI is coming, the API is what it will call.
- **API returns only final answers** — rejected; transparency (tool calls + gate visible) is a
  requirement (ADR-0009).

## Nuances

- All trace events are **user-visible** (ADR-0009); the UI may *collapse* tool detail but hides
  nothing.
- `# ponytail:` demo app is scaffolding; the real UI is the dashboard integration.

## Open (resolve in ticket)

- **Diagnosis output format** — structured fields vs prose for answers and `case.md`. User to supply
  Claude/large-model transcripts as a reference; finalize inside the ticket.
