# ADR-0014 — Forensic trigger

**Status:** accepted

## Decision

The PA (or emulator, ADR-0003) runs as a **periodic predictor** — every ~10s (configurable) it
predicts on the current window and **writes a Prediction Record to the Event Ledger**. A record with
**`decision.alert == true`** fires the Forensic pipeline **inline**:

freeze `WindowContext` at `record.ts` → copy the concerned observability window into
`cases/<id>/window/` → write `prediction.json` → generate the **initial report** (`case.md`) → spawn
investigation chat(s).

## One case per episode

A fault episode emits many alerting records over minutes. **Open one case per episode** (dedup by
`scenario_id`, or device+fault_type+time-proximity), **frozen at the first alerting record**; later
ticks are idempotent no-ops (the ledger `INSERT OR IGNORE`s by `alert_id`, one per episode —
`memory/ledger.py`, `emulate.py`). Episode ends → case ready for verdict.

**#48 resolved — freshness is a NEW investigation, not a mutated case.** The earlier "later records
*update* the open case" was aspirational: no reachable path (later ticks never land in the ledger)
and no consumer. Mutating the open case in place would violate the ADR-0002 freeze (a frozen case
must not pull live data) and break the one-row-per-episode invariant R6a/R6b read against — real
work for zero measurable gain (eval scores the agent's answer vs `/labels` ground truth, not a
case's running numbers; the frozen investigation re-derives cause from its own evidence). So the
open case stays locked. When a *materially* fresher prediction arrives (cause refined, `n_concurrent`
grew — not every 10 s tick), the **UI** offers "investigate the latest", opening a NEW case at its
own frozen T_snapshot. Each case stays lock-on-first; freshness rides the existing multi-case model,
no in-place mutation, no freeze violation.

**Status: UI-led, not yet ticketed.** Two preconditions (codependency rule — the UI button is the
consumer that justifies exposing the signal): (1) the UI team commits to the affordance; (2) the PA
pins its `alert_id` lifecycle (`pa_bah` PIPELINE_SCOPE §3.3 leaves it undefined). If the real PA
mints a fresh `alert_id` per prediction, later ticks already land → the button is pure UI, zero
copilot work; if it reuses one per episode, copilot owes a small latest-per-episode read path (no
invariant change). Write that enabler ticket only when both preconditions hold (#48).

## Concurrent faults

`n_concurrent > 1` → **n investigation chats (one per fault) + a master chat that synthesizes**
(ADR-0009).

## Restart safety

The predictor/trigger tracks the **last-processed record id** in the ledger; on restart it resumes
from there, idempotent by record id — no double-fire, no missed alerts.

## Context

The PA "runs in a loop predicting every ~10s; if it finds a fault it triggers the pipeline." The
detection *is* the PA's job — there is no separate copilot-side detector.

## Alternatives rejected

- **A separate threshold-watcher** in the copilot — rejected; the PA/emulator already emits
  alerting records. Over-built.
- **One case per record** — rejected; ~30 duplicate cases for one 5-min fault.
- **Literal crontab** — impl note: system `cron` floors at ~60s, so a 10s cadence is a
  sleep-loop / systemd-timer, not crontab.

## Nuances

- Secondary trigger `abstain==true` + high novelty ("something weird, no confident call") — a config
  flag, **deferred**.

## Consequences

- Cases fire automatically off the prediction stream; the same seam works for the real PA later.
