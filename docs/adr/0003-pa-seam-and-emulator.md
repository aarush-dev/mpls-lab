# ADR-0003 — PA seam & emulator

**Status:** accepted

## Decision

The **only** seam between the copilot and the prediction stack is the **Prediction Record** — the
JSON in `DOCS/plans/PA.md` §3.3 (`/v1/predict` response). The copilot consumes records; how they're
produced lives behind the seam.

While the real PA is unbuilt, a **PA-emulator** produces records, gated by the **`emulate_pa`** flag:
- `emulate_pa=true` → emulator feeds records.
- `emulate_pa=false` → copilot reads the real PA endpoint.

Flip the flag, no code change.

## Emulator behavior

- Trigger source: `/labels` fault starts → wait `delay_s` (stands in for detection/lead-time) → emit
  a record (ADR-0014 covers the firing loop).
- **Full §3.3 fidelity** — complete `risk` / `forecast` (incl. quantile arrays) / `localization` /
  `anomaly` / `decision` blocks, derived from ground truth. "As close to a real PA as possible."
- Imperfection is realism, via `error_profile`:
  - `oracle` — perfect (deterministic tests)
  - `light` (default) — TTI jitter, occasional confusable cause, occasional abstain
  - `heavy` — stress the quality gate
- **`health.drift_state`** faked (R0–R5 + rising `codebook_novelty`) so the trust gate has something
  to distrust.
- Config-driven so it's updatable when the real PA spec moves (the plan is out of date; output shape
  is largely stable).

## Context

The plan puts the agent *after* the prediction, but the prediction stack is out of scope. A frozen
record contract lets every copilot ticket build + test against fixtures with zero dependency on the
other team.

## Alternatives rejected

- **Copilot reaches into the live prediction stack** — rejected; blocks all tickets on another team.
- **Emit only a copilot-relevant subset** (collapse forecast to a scalar) — rejected by user; keep
  full fidelity so the emulator faithfully mirrors a real PA.

## Nuances — "drift" is two different things

1. **Prediction error** — the model is imperfectly right (`error_profile`).
2. **Model-health drift** — `research/07`'s R0–R5 ladder + `codebook_novelty` + drift-attribution
   "for the operator and the LLM" (§7.5). A signal the copilot reads to decide whether to *trust* the
   prediction (PA.md §3.5). The emulator fakes the **output scalar only** — the real 6-signal detector
   + LoRA ladder is the prediction stack's job.

## Consequences

- Copilot is buildable + testable now against fixture records.
- `error_profile=oracle` gives deterministic eval later (ADR-0017).
