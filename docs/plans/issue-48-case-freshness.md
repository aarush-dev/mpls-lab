# Issue #48 — Forensic case freshness

**State:** design resolved (ADR-0014) + emulator enabler built. Load-safe. Not yet committed.

## Problem
Fault episode alerts for minutes. PA emits fresher Prediction Record every tick (confidence climbs,
TTI shrinks, cause can refine `congestion`→`core_congestion`, `n_concurrent` grows). Case opens off
the **first** alerting record and freezes there — later ticks never reach it.

Why later ticks vanish: ledger idempotent by `alert_id`. Emulator minted **one** `alert_id` per
episode (`alt_{sid}`), so ticks 2..N collapse to the first row via `INSERT OR IGNORE`
(`memory/ledger.py:47`, `emulator/emulate.py`). Open case stuck on tick-1 numbers till episode end.

## Decision (ADR-0014)
Freshness = a **NEW investigation**, never a mutated case. Rejected in-place "update the open case":
- **unreachable** — later ticks never land; **no consumer** — eval scores agent answer vs `/labels`
  ground truth (`eval/scenarios.py`), not a case's running numbers.
- would break **ADR-0002 freeze** (frozen case must not pull live data) and the one-row-per-`alert_id`
  invariant R6a/R6b read against.

Open case stays **locked at first record**. A materially-fresher prediction opens a NEW case at its
own frozen T_snapshot — freshness rides the existing multi-case model. No mutation, no freeze break.

## What was built
Key the episode `alert_id` on `scenario_id` + **reported cause** (`emulate._alert_id`):

    alt_{sid}__{cause}          # e.g. alt_congestion-ce_branch1-87844aed__core_congestion

- same-cause tick (confidence/TTI/drift/concurrency churn) → **same id** → ledger no-op → case frozen.
- cause refines → **fresh id** → lands in ledger → existing Forensic trigger opens a NEW frozen case.

Zero change to ledger/trigger/case/freeze — they already do the right thing once a distinct id lands.

### Why cause-only, NOT (cause, n_concurrent)
Creating a case = **drain live adapter (HTTP→dataapi) + run an agent (LLM)** (`forensic/case.create_case`).
`n_concurrent` churns (1→2→3→2…); keying on it would re-investigate every bump → hammer dataapi/LLM
for context that doesn't change what the fault IS. `cause` refines 0–2×/episode: bounded, load-justified.
Concurrency freshness, if ever wanted, belongs behind a UI "investigate the latest" click, not auto-fire.

## Prediction interval → 3s
`predict_interval_s: 10 → 3` (`config.yaml` + dataclass default + self-check). Drives both predictor
and forensic trigger loops. Safe **because** case creation is cause-gated + idempotent — expensive
work is decoupled from tick rate.

| Work / tick | Scales at 3s? |
|---|---|
| predict + emulate (CPU) | 3.3× — negligible |
| persist (mostly IGNORE no-op; ledger doesn't grow) | ~0 real writes |
| trigger poll (bounded scan) | 3.3× — cheap |
| `/labels` GET → dataapi | **3.3× — only real new load** (~20/min, trivial localhost read) |
| **case creation (drain + agent)** | **NO — cause-gated, tick-independent** |

Benefit: fault→case detection latency ~10s → ~3s. Two overload guards: cause-gating (few fires) +
`create_case` runs synchronously in the loop (can't outrun completion).

Ceiling: if dataapi feels the `/labels` GET, cache labels with a ~15s TTL. Not needed now.

## Verified (real evidence, not asserted)
End-to-end through real trigger + ledger: 6 ticks, concurrency churned 1-2-3-2-3-1, cause refined once
→ **2 ledger rows, 2 cases**. Concurrency churn opened ZERO extra cases; only cause-refine did.
Unit + suite green: `test_emulate`, `test_case`, `test_predictor`, `test_trigger`, `test_ledger`, `config`.

## Files
- `copilot/emulator/emulate.py` — `_alert_id(sid, cause)`; explanation_ref + persist docstring.
- `copilot/emulator/test_emulate.py` — cause-only freshness test.
- `copilot/forensic/case.py` — `case_id` docstring.
- `copilot/config.yaml`, `copilot/config.py` — 3s interval.
- ADR-0014 — one-line update pending (emulator now decides precondition #2 = cause-keyed ids).

## Residual / open
- **Auto-open, not UI-gated.** Cause-refine auto-opens a case (0–2 drains+agent-runs/episode). Zero
  auto-load only via UI-gated variant (trigger doesn't fire; UI pulls latest-per-cause on click) —
  needs UI-team affordance (ADR precondition #1).
- **Real PA** (`emulate_pa=false`, `sidd20228/pa_bah`): its `alert_id` lifecycle still undefined
  (PIPELINE_SCOPE §3.3). This change decides it for the **emulator** only. If real PA reuses id
  per episode, copilot owes a latest-per-episode read path (not built).
- **`verify_full_generation.py`** contaminated by an earlier review-agent edit mixed with user's
  uncommitted work — held out of the #48 commit, needs eyeball.
