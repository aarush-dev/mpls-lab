# Issue #48 — The exact solution

## The fix — one line

**Before:**
```python
"alert_id": f"alt_{sid}"                    # one id per episode
```
**After:**
```python
"alert_id": _alert_id(sid, ftype)          # -> f"alt_{sid}__{cause}"
```
`sid` = scenario_id (the episode). `ftype` = the **reported** cause (post-`_confuse`, honest to the
error profile). That is the whole code change to the fix mechanism. Zero logic added to the ledger,
trigger, case creation, or freeze — they already do the right thing once the id changes.

## Why that one line fixes it — the causal chain

The bug: the ledger is idempotent by `alert_id` (`INSERT OR IGNORE` on PK `id`, `memory/ledger.py`).
With one id per episode, ticks 2..N of a fault collapsed to the first row and never reached case
creation. The case froze on tick-1 forever.

Now the id carries the cause, so two paths diverge:

**Immaterial tick** (confidence drifts, TTI shrinks, drift_state changes, `n_concurrent` grows — cause
unchanged):
```
same cause  ->  same alert_id  ->  INSERT OR IGNORE = no-op
            ->  no new ledger row  ->  trigger sees nothing new  ->  no new case
```
Open case stays frozen at its first record. ADR-0002 freeze intact.

**Material tick** (cause refines, e.g. `congestion` -> `core_congestion`):
```
new cause  ->  new alert_id  ->  new ledger row lands
           ->  trigger's _new_alerts yields it (not in cursor's fired set)
           ->  fires handle -> create_case
           ->  freezes ITS OWN window at ITS OWN T_snapshot -> NEW case dir
```
`case_id(record)` = sanitized `alert_id`, so distinct id => distinct case dir. Freshness = a second
frozen snapshot, never a mutation of the first.

## The one design choice inside the fix

Materiality = **cause only**, deliberately *not* `(cause, n_concurrent)`. Opening a case runs the
expensive path — `create_case` drains the live adapter (HTTP->dataapi) + runs an agent/LLM
investigation. `n_concurrent` churns (1->2->3->2...); keying on it would fire a fresh investigation on
every bump -> hammer dataapi/LLM for context that doesn't change what the fault IS. `cause` refines
0-2x/episode: bounded, and each refine genuinely changes the answer.

## The separate, related change

`predict_interval_s: 10 -> 3` (`config.yaml` + dataclass default + self-check). Safe *because* of the
above — case creation is cause-gated and idempotent, so it is decoupled from tick rate. 3s just
multiplies cheap work (predict / persist-noop / poll) and buys ~3s fault->case detection latency.

## What proved it

Real trigger + ledger, 6 ticks, concurrency churned 1-2-3-2-3-1, cause refined once -> **2 ledger
rows, 2 cases**. Churn opened zero; only the cause-refine opened a new case.

**In one sentence:** identity moved from "the scenario" to "the scenario *at its current reported
cause*," so the ledger's existing idempotency automatically collapses noise and forks a new frozen
investigation exactly when the diagnosis changes — no new subsystem, one key.
