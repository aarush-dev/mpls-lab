# ADR-0013 — Sandbox

**Status:** accepted

## Decision

The "semi-sandbox" is the **file-handling policy + a constrained subprocess**, enforced at the tool
layer. **No container.**

- **Read-only outside the scratchpad**; writes/execution confined to the per-session `scratchpad/`
  (ADR-0011). Path checks reject out-of-bounds writes.
- **Data tools are read-only** — no tool mutates the network or the stores; the agent writes only its
  own session/case folder.
- **Execution** = subprocess, `cwd = scratchpad`, **no network**, timeout (ADR-0011), absolute paths,
  whitelisted libs (pandas/matplotlib).

## Context

The agent runs model-written code. The user's position: the file-handling policy (scratchpad scoping
+ read-only-outside + copy-in) **is** the isolation needed — the folder structure is the policy.

## Alternatives rejected

- **Container / seccomp per run** — rejected now.
  `# ponytail:` path-allowlist + subprocess; harden to container/seccomp only if deployed
  adversarially.
- **Full system access (little-coder default)** — rejected (ADR-0011).

## Nuances

- "Semi" = the trust boundary is the **tool layer**, not a kernel jail.
- Exec has no network even in dev; the *tools* reach the local data API, not the exec subprocess.

## Consequences

- Model-written code can't touch production data, reach the network, or hang the box, without
  container overhead.
