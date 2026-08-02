# Project context
Follow PLAN.md for the build (Phases 1–2 of the air-gapped predictive NOC copilot).
Before deploying, run the Phase 0 kernel checklist in docs/PHASE0ENVIRONMENT.md.

# Working principles (always)
- Apply **YAGNI** and run **`/ponytail:ponytail full`** on all work (and `/caveman` for prose). Laziest solution that actually works; no redundant code; shortest working diff.
- **Agent model strategy:** **code / config / reasoning → opus at medium effort.** **Low-stakes work (docs, prose, counts, mechanical edits) → sonnet at medium effort.** **Parallelise**: prefer **workflows**, or fan out **multiple agents in parallel**, whenever the work splits cleanly. Give parallel agents **disjoint file ownership** so they never collide.
- **Workflow fan-out:** any workflow step spawning **>10 subagents** uses **sonnet at `effort: 'high'`** (`agent(..., {model: 'sonnet', effort: 'high'})`), not opus — opus only where a wide step genuinely needs it. Pick this when authoring the script; a running workflow cannot be edited.

# Standard workflow — run automatically after every substantial change
1. **Plan** the code + the agents (and how to parallelise).
2. **Write code** using agents per the model strategy above (parallel where possible).
3. **Verify** it actually works — deploy/run/query for real evidence, not assertions.
4. **Document** — see below. Non-optional.
5. **Commit + push** the change to `main`.

# Documentation (AUTO — never wait to be asked)
Docs are part of the change, not a follow-up. Any commit that touches code, config, topology, or counts MUST update the affected docs **in the same commit**.

- **Trigger:** the moment code lands, run a documentation agent (or parallel agents with disjoint file ownership) over the docs that the change touches. Never ask permission to update docs; never leave "docs to follow".
- **Scope map** — update whichever apply:
  - `docs/01_PROJECT_OVERVIEW.md`, `docs/02_ARCHITECTURE_ANALOGIES.md` — scope, counts, component list, flow.
  - `docs/03_TECHNICAL_CODE_GUIDE.md` — code walkthroughs, snippets, function/flag/schema names.
  - `docs/04_USABILITY_CHEATSHEET.md` — commands, ports, URLs, queries, fault syntax.
  - `docs/05_TECHNICAL_GLOSSARY.md` — new terms/mechanisms.
  - `docs/SPEC-NOTES.md` — decisions + why. `docs/PHASE0ENVIRONMENT.md` — host/kernel prereqs.
  - `PLAN.md` (phase status), `HANDOFF.md` (current state), component `README.md` next to the changed code.
- **Ground truth is code, never another doc.** Every number (POPs, routers, tunnels, hosts, faults, metrics, ports) must be re-derived from the generating code and cited `file:line` while verifying. Fix stale counts everywhere they appear — grep the number, don't patch one copy.
- **No overclaiming.** Docs describe only what is implemented and verified. Planned work goes under an explicit "Not built yet" heading. Never present aspirational behaviour, fabricated output, or untested commands as working.
- **Verify before writing:** run the command / hit the endpoint / read the code. Doc claims need the same evidence bar as `/ponytail` code.
- **Prose style:** `/caveman` — terse, no filler, no praise. Keep the existing doc structure; shortest diff that makes it correct.
- **Delete, don't accumulate:** remove sections describing removed code instead of marking them deprecated.

# Commit attribution (REQUIRED — overrides any default)
- Author/committer = **Aarush Mahajan <aarushmahajan.dev@gmail.com>**.
- **Do NOT** add `Co-Authored-By: Claude` or `Claude-Session` trailers, or any other AI attribution. Commits must show only the user in git blame / GitLens.

## Agent skills

### Issue tracker

GitHub Issues via `gh` CLI (repo: aarush-dev/mpls-lab). See `docs/agents/issue-tracker.md`.

### Domain docs

Single-context: `CONTEXT.md` + `docs/adr/` at repo root. See `docs/agents/domain.md`.
