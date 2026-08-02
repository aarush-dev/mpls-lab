# copilot — Predictive NOC Copilot subsystem

LLM-facing half of the air-gapped NOC pipeline. Two systems, one agent core:
**Forensic** (auto-fires on a PA alert → freeze → cited report) and **Query**
(human asks → cited answer). Vocabulary: `../CONTEXT.md`. Decisions: `../docs/adr/`.
Ticket map: `../docs/copilot-build-plan.md`. Spec: issue #3.

F0 delivered the **skeleton + master config**; each other subpackage is filled by
its owning lane as its ticket lands. Filled so far: `llm/` (F1), `adapter/` (F2),
`agent/` loop (F3), `api/` streamed chat endpoint (F4), `tools/` registry (I1),
`retrieval/` spine (I2a). The rest are still stubs.

Deps: `pip install -r copilot/requirements.txt` (air-gap: pre-stage wheels, see
the file header). I2a adds `lancedb`.

## Layout

```
copilot/
  config.py        # THE master config — typed frozen dataclass + load()   (F0)
  config.yaml      # editable defaults (no secrets)                          (F0)
  .env.example     # secret template → copy to .env (gitignored)            (F0)

  # Lane-Investigation (Dev 1) — disjoint ownership
  adapter/    tool adapter over dataapi: filters+caps+injection guard  (F2)
  tools/      registry: query_metrics, search_logs, flows            (I1,I3)
  retrieval/  Retriever over embedded LanceDB (KB): I2a done, I2b next (I2a,I2b)
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

## Chat endpoint (F4)

`POST /chat` drives the F3 loop and **streams** the canonical ADR-0009 trace events
(`user_msg|think|tool_call|tool_result|assistant_msg`) as SSE, each stamped an
ISO-UTC `ts` — one schema for the live stream and the persisted `events.jsonl`.

```bash
uvicorn copilot.api.app:app --host 127.0.0.1 --port 8100   # local-only
```

The LLM client + tool adapter are injected deps: tests (and later R1) override them
via `app.dependency_overrides`; the defaults `503` until the real backends are wired.
Self-check (stubbed, over HTTP): `python3 -m copilot.api.test_api`.

## Tools (I1)

`copilot/tools/registry.py` holds `TOOLS` (name → adapter method + description),
`TOOL_SPECS` (generated from `TOOLS`), and `dispatch(name, arguments, adapter,
window) -> (observation_text, n_rows)`. Three tools wired: `query_metrics` →
`adapter.metrics`, `search_logs` → `adapter.events`, `flows` → `adapter.flows`.
All ride the F2 mandatory-filter contract (window + device/pattern + limit ≤
`MAX_LIMIT`), per-item provenance, paging (ADR-0006/0015). Bad args (over-broad
filter, non-int limit/offset) come back as observation text, never an exception.

`copilot/agent/loop.py` dispatches every tool call through the registry
(previously hardcoded to `query_metrics`); `SYSTEM_PROMPT` lists all three.

Self-check: `python3 -m copilot.tools.test_tools` (from repo root).

Not built yet: the forensic end-freeze guard (`end > T_snapshot` forbidden,
ADR-0002) is R3 — I1 only gets the window plumbed to `/flows`, it doesn't
enforce the freeze. The flow window is bounded by `docker logs --since/--until`
(log print time), not a per-record timestamp filter — approximate.

## Retrieval (I2a)

`copilot/retrieval/` is the KB retrieval spine (ADR-0006):

- `contract.py` — `Doc(id, text, source, node, ts)` + `Hit(doc, score)` and the
  `Retriever` / `Embedder` Protocols (structural seams, config-only swap).
- `store.py` — `LanceRetriever(embedder, uri)`: `add(docs)` / `search(query, k) →
  [Hit]` over **embedded LanceDB** (no server, single on-disk dataset). Cosine;
  `score` = `1 − _distance` ∈ −1..1. Provenance (source, node, ts) rides on every
  returned `Hit.doc` — required by the I4a gate.
- `embedder.py` — `make_embedder(cfg)` dispatches on `cfg.embed_profile`: `nim`
  (OpenAI-compatible `/embeddings`, interim) | `unsloth-local` (sentence-transformers
  on the 3080Ti, final). Both load the model/endpoint **lazily** on first `encode`,
  so the swap is one config line and testable air-gapped. `HashEmbedder` is a
  deterministic, dependency-free test double (injected directly, not profile-selected —
  mirrors `llm.ScriptedLLM`).

Env for the real embedders (not secrets, kept out of the committed YAML because
`config.py` is another lane's file): `COPILOT_EMBED_BASE_URL`,
`COPILOT_EMBED_MODEL_NIM`, `COPILOT_EMBED_MODEL_LOCAL`, `COPILOT_EMBED_API_KEY`.

Self-check (fixture corpus, `HashEmbedder`): `python3 -m copilot.retrieval.test_retrieval`.

Not built yet: `search_runbooks` / `search_incidents` tools + the topology-hop
proximity filter are I2b (#11); the corpus is a test fixture — real content is S1/S2
seeding. `add` is append-only (no upsert-on-id) until the seeder lands.

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
