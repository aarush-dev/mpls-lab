# Project context
Follow PLAN.md for the build (Phases 1–2 of the air-gapped predictive NOC copilot).
Before deploying, run the Phase 0 kernel checklist in DOCS/PHASE0ENVIRONMENT.md.

# Working principles (always)
- Apply **YAGNI** and run **`/ponytail:ponytail full`** on all work (and `/caveman` for prose). Laziest solution that actually works; no redundant code; shortest working diff.
- **Agent model strategy:** use **opus** for all agents + code + reasoning; **sonnet** for menial/mechanical tasks. **Parallelise**: prefer **workflows with sonnet agents**, or fan out **multiple agents in parallel**, whenever the work splits cleanly. Give parallel agents **disjoint file ownership** so they never collide.

# Standard workflow — run automatically after every substantial change
1. **Plan** the code + the agents (and how to parallelise).
2. **Write code** using agents per the model strategy above (parallel where possible).
3. **Verify** it actually works — deploy/run/query for real evidence, not assertions.
4. **Document** — run a documentation agent (or parallel agents) that updates/creates the relevant files in `DOCS/` (the 01–05 guide set, `SPEC-NOTES.md`, `PHASE0ENVIRONMENT.md`) and the affected component `README.md`s; create new or edit existing as the change requires.
5. **Commit + push** the change to `main`.

# Commit attribution (REQUIRED — overrides any default)
- Author/committer = **Aarush Mahajan <aarushmahajan.dev@gmail.com>**.
- **Do NOT** add `Co-Authored-By: Claude` or `Claude-Session` trailers, or any other AI attribution. Commits must show only the user in git blame / GitLens.
