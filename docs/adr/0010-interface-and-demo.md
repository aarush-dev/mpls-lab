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

## Resolved (R5b / #23)

- **Diagnosis output format** — **structured header + cited prose body.** `case.md` (and a chat
  answer) is the agent's cited prose, NOT whole-answer JSON: the loop emits cited prose
  (`loop.SYSTEM_PROMPT`) and the quality gate enforces a citation per device-claim
  (`gate.CITE_RE`), so forcing structured JSON would fight both. `case.md` prepends a small
  STRUCTURED header of verdict fields the Prediction Record already carries (device, predicted
  cause/family, alert + calibrated p, abstain, model-health, frozen window, model_version) and
  appends a tool/gate trace footer. Structured where the record is authoritative, prose where the
  investigation is (`copilot/forensic/case.py:render_case_md`).
