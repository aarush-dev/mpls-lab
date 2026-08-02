# ADR-0011 — Workspace / coding agent (Milestone B)

**Status:** accepted

## Decision

The agent can **run code, write files, generate graphs, present them as artifacts** — a workspace
tool family (`read`, `write`, `edit`, `bash`) plugged into the same loop (ADR-0005). It takes
**little-coder's invariants** and **rejects little-coder's permission model**.

### Invariants (from little-coder, verified)

- **Read-before-edit** — *"A file must be Read in the current session before you can Edit it"*
  (runtime invariant). Applies to the agent's own files too.
- **Write = new files only** — *"Write refuses on existing files"*; rejection returns the Edit call.
- **Edit = exact-string match** — whitespace included; `replace_all` or context for uniqueness.
- **bash timeout** — 30s default → 120–300s for heavy work.

### Boundary (ours — little-coder lacks this)

- **Workspace-scoped writes** — write/execute only inside the per-session `scratchpad/` (ADR-0009).
  Outside = **read-only**. Enforced at the tool layer (path check against `workspace_root`).
- **Copy-in-to-modify** — to change external data: `read` it → `write` a copy into scratchpad → work
  on the copy. Never edits external files in place.
- **No-network execution** (air-gap). (little-coder's sub-coders browse online — rejected.)

## Context

little-coder solves "make a small model edit files reliably" but grants *"full system access"* with
no isolation and network allowed. Our context is the opposite: air-gapped, read-only production
telemetry, scoped workspace. So: its discipline, our cage.

## Alternatives rejected

- **little-coder's full-access + network model** — rejected; wrong threat model for a NOC copilot.
- **Per-case workspace** — rejected; per-session only (ADR-0009).
- **Emit-only (no execution)** — rejected by user; full execute is wanted.

## Nuances

- Two tool families now: **investigation** (5, read-only, ADR-0006/0007) + **workspace** (4).
- Executor details in ADR-0013.

## Consequences

- The copilot is a NOC-investigator *and* a scoped coding-agent on one loop. Bigger than the original
  §3, but required for "run code / make graphs".
