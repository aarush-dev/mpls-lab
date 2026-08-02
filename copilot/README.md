# copilot — Predictive NOC Copilot subsystem

LLM-facing half of the air-gapped NOC pipeline. Two systems, one agent core:
**Forensic** (auto-fires on a PA alert → freeze → cited report) and **Query**
(human asks → cited answer). Vocabulary: `../CONTEXT.md`. Decisions: `../docs/adr/`.
Ticket map: `../docs/copilot-build-plan.md`. Spec: issue #3.

F0 delivered the **skeleton + master config**; each other subpackage is filled by
its owning lane as its ticket lands. Filled so far: `llm/` (F1), `adapter/` (F2),
`agent/` loop (F3). The rest are still stubs.

## Layout

```
copilot/
  config.py        # THE master config — typed frozen dataclass + load()   (F0)
  config.yaml      # editable defaults (no secrets)                          (F0)
  .env.example     # secret template → copy to .env (gitignored)            (F0)

  # Lane-Investigation (Dev 1) — disjoint ownership
  adapter/    tool adapter over dataapi: filters+caps+injection guard  (F2)
  tools/      investigation tools (query_metrics, search_*, flows…)    (F3,I1,I3)
  retrieval/  Retriever over embedded LanceDB (KB)                     (I2a,I2b)
  agent/      agent loop + two-stage quality gate                      (F3,I4)
  skills/     progressive-disclosure diagnostic skills                 (I5)

  # Lane-Runtime (Dev 2) — disjoint ownership
  llm/        OpenAI-compatible client, profile-selected               (F1,R1)
  memory/     Session Store + Event Ledger (events.jsonl, cases)       (R2)
  window/     WindowContext live/query/forensic                        (R3)
  emulator/   PA-emulator: /labels ground truth → Prediction Record    (R4)
  forensic/   forensic trigger: predict loop, dedup, case creation     (R5,R6)

  # Convergence
  api/        FastAPI, streamed step-trace                             (F4)
  demo/       demo web app consuming the trace                         (C1)
```

Rule: **touch only your lane's subpackage.** Cross-lane coupling points are the
only shared edits (see build-plan "Cross-lane edges").

## Config

One YAML (`config.yaml`) → typed `Config`; secrets from `.env`/env, never git.

```python
from copilot import load
cfg = load()          # defaults ← config.yaml ← env secrets, validated
```

Self-check: `python3 copilot/config.py`. Fields + ADR provenance: `config.py` docstring.

## Where the rest of the system slots in

The copilot shares this repo with the network sim + the (future) PA stack + the
front end. Nothing here duplicates them; it **consumes** them.

- **PA / prediction stack** (separate team, `../docs/plans/PA.md`) — out of scope
  for this subpackage. Its only seam is the **Prediction Record** (§3.3). Until it
  ships, `copilot/emulator/` produces full-fidelity records from `/labels` ground
  truth behind `emulate_pa=true`. When the real PA lands it writes the same record
  to the Event Ledger (`copilot/memory/`) and the flag flips to `false` — no
  copilot code change. If the real PA grows its own package, it lives **beside**
  `copilot/`, not inside it (the seam is the record, not an import).
- **Kafka** — reuse the running bridge (`../streaming/bridge.py`), do not add a
  broker. It already publishes `noc.{metrics,events,faults,topology}` to two
  consumer groups: **`noc-predictive`** (PA stack) and **`noc-copilot`** (this
  subsystem). The copilot reads live truth via the dataapi adapter; the emulator
  reads `/labels`. No new topic needed for F0.
- **Grafana / front end** — observability dashboards already live at
  `../telemetry/grafana/` (Loki/VictoriaMetrics). Those stay put. The **copilot**
  front end is `copilot/demo/` (scaffold) and, later, the real NOC dashboard —
  both integrate through **`copilot/api/`** (the streamed trace, ADR-0010), never
  by reaching into copilot internals.
