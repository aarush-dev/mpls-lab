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
`scenario_id`, or device+fault_type+time-proximity); later records **update** the open case, never
spawn duplicates. Episode ends → case ready for verdict.

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
