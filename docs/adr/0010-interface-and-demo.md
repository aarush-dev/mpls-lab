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
- **No UI in this repo.** Another team owns the NOC dashboard/UI. Copilot ships only the FastAPI
  service; its endpoints **plus a documented CORS allowance** for the UI's browser origin are the
  whole contract. See **Amended (UI descoped)** below.

## Context

The Query system is interactive/multi-turn; Forensic auto-reports then accepts follow-ups. The UI
lives in another team's dashboard, consuming this service.

## Alternatives rejected

- **CLI/REPL only** — rejected; a UI consumes the API, so the API is what it must serve.
- **API returns only final answers** — rejected; transparency (tool calls + gate visible) is a
  requirement (ADR-0009).

## Nuances

- All trace events are **user-visible** (ADR-0009); the consuming UI may *collapse* tool detail but
  the service hides nothing.

## Amended (UI descoped)

Original plan (grilling) called for a **basic UI + throwaway demo app** built here. Superseded: the
UI is owned by a **separate team**. Consequences:

- **No demo app.** The `copilot/demo/` stub is deleted; ADR-0010's demo-app mandate is struck. The
  `/chat` trace is exercised via API tests + `curl`, not an in-repo UI.
- **Boundary = the FastAPI service.** Contract for the other team = the endpoints + streamed event
  schema (ADR-0009) + a **CORS allowance** for their browser origin, which copilot owns and
  documents. Everything upstream of the service is copilot-internal.
- **Reverted Grafana wiring (`53cc26ba`, #50-#53) stays reverted** — that was copilot reaching into
  the UI, which is now out of scope. Exception: the **CORS piece of #50** is legitimately copilot's
  and is re-landed on its own (endpoint boundary, not UI code).

## Resolved (R5b / #23)

- **Diagnosis output format** — **structured header + cited prose body.** `case.md` (and a chat
  answer) is the agent's cited prose, NOT whole-answer JSON: the loop emits cited prose
  (`loop.SYSTEM_PROMPT`) and the quality gate enforces a citation per device-claim
  (`gate.CITE_RE`), so forcing structured JSON would fight both. `case.md` prepends a small
  STRUCTURED header of verdict fields the Prediction Record already carries (device, predicted
  cause/family, alert + calibrated p, abstain, model-health, frozen window, model_version) and
  appends a tool/gate trace footer. Structured where the record is authoritative, prose where the
  investigation is (`copilot/forensic/case.py:render_case_md`).
