# copilot — Predictive NOC Copilot subsystem

LLM-facing half of the air-gapped NOC pipeline. Two systems, one agent core:
**Forensic** (auto-fires on a PA alert → freeze → cited report) and **Query**
(human asks → cited answer). Vocabulary: `../CONTEXT.md`. Decisions: `../docs/adr/`.
Ticket map: `../docs/copilot-build-plan.md`. Spec: issue #3.

F0 delivered the **skeleton + master config**; each other subpackage is filled by
its owning lane as its ticket lands. Filled so far: `llm/` (F1), `adapter/` (F2),
`agent/` loop (F3), `api/` streamed chat endpoint (F4), `tools/` registry (I1 +
I2b retrieval tools), `retrieval/` spine (I2a). The rest are still stubs.

Deps: `pip install -r copilot/requirements.txt` (air-gap: pre-stage wheels, see
the file header). I2a adds `lancedb`.

## Layout

```
copilot/
  config.py        # THE master config — typed frozen dataclass + load()   (F0)
  config.yaml      # editable defaults (no secrets)                          (F0)
  .env.example     # secret template → copy to .env (gitignored)            (F0)

  # Lane-Investigation (Dev 1) — disjoint ownership
  adapter/    tool adapter over dataapi: filters+caps+injection guard  (F2); HttpAdapter = real read over live dataapi (A1)
  tools/      registry: query_metrics/search_logs/flows + search_runbooks/search_incidents (I1,I2b)
  retrieval/  Retriever over embedded LanceDB (KB) + search tools     (I2a,I2b)
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

The LLM client + tool adapter are injected deps: tests override them via
`app.dependency_overrides`. `get_adapter` now returns the real `HttpAdapter`
(`cfg.dataapi_url`, env `COPILOT_DATAAPI_URL` wins) — A1 replaced the F2 stub; the LLM
default still `503`s until R1. Self-check (stubbed, over HTTP): `python3 -m copilot.api.test_api`.

## Tools (I1)

`copilot/tools/registry.py` holds `TOOLS` (read tools → adapter method),
`RETRIEVAL_TOOLS` (KB tools → source filter, I2b), `TOOL_SPECS`, and
`dispatch(name, arguments, adapter, window, retriever) -> (observation, n_rows)`.
Read tools: `query_metrics` → `adapter.metrics`, `search_logs` → `adapter.events`,
`flows` → `adapter.flows` — all ride the F2 mandatory-filter contract (window +
device/pattern + limit ≤ `MAX_LIMIT`), per-item provenance, paging (ADR-0006/0015).
Retrieval tools (I2b): `search_runbooks` / `search_incidents` search the I2a
`Retriever` scoped by provenance; `search_incidents` also takes a focus `device` →
topology-hop proximity filter (see below). Bad args (over-broad filter, non-int
limit/k/hops, missing query) come back as observation text, never an exception.
A dataapi **transport** fault (`AdapterError`: refusal / 5xx) also comes back as an
observation, not a raise out of `investigate()` — and, for `walk_topology_graph`, it
beats the empty-walk "unknown device" path so an outage never asserts a false fact (A1).

`adapter/http.py` (A1) is the ONE place coupled to endpoint shapes (ADR-0006): it
normalises event ISO / flow `stamp_updated` ts → epoch int (or the gate's numeric
`start <= ts <= end` would `TypeError`), synthesises PromQL from `Filters` for
`/metrics` (per-series latest sample → one Evidence), and does `/events` `pattern`+`offset`
adapter-side (fetch-then-filter). Everything after the fetch is F2's shared `serve_rows`.
Self-check: `python3 -m copilot.adapter.test_http`.

`copilot/agent/loop.py` dispatches every tool call through the registry
(previously hardcoded to `query_metrics`); `SYSTEM_PROMPT` lists all five.

Self-check: `python3 -m copilot.tools.test_tools` (from repo root).

Not built yet: the forensic end-freeze guard (`end > T_snapshot` forbidden,
ADR-0002) is R3 — I1 only gets the window plumbed to `/flows`, it doesn't
enforce the freeze. The flow window is bounded by `docker logs --since/--until`
(log print time), not a per-record timestamp filter — approximate.

## Retrieval (I2a) + search tools (I2b)

`copilot/retrieval/` is the KB retrieval spine (ADR-0006):

- `contract.py` — `Doc(id, text, source, node, ts)` + `Hit(doc, score)` and the
  `Retriever` / `Embedder` Protocols (structural seams, config-only swap).
- `store.py` — `LanceRetriever(embedder, uri)`: `add(docs)` / `search(query, k,
  source, nodes) → [Hit]` over **embedded LanceDB** (no server, single on-disk
  dataset). Cosine; `score` = `1 − _distance` ∈ −1..1. Provenance (source, node,
  ts) rides on every returned `Hit.doc` — required by the I4a gate. `source` and
  `nodes` **prefilter** (before the ANN scan), so the top-k is taken *within* scope
  — a nearby-but-weaker hit isn't lost to a global top-k that a post-filter would trim.
- `embedder.py` — `make_embedder(cfg)` dispatches on `cfg.embed_profile`: `nim`
  (OpenAI-compatible `/embeddings`, interim) | `unsloth-local` (sentence-transformers
  on the 3080Ti, final). Both load the model/endpoint **lazily** on first `encode`,
  so the swap is one config line and testable air-gapped. `HashEmbedder` is a
  deterministic, dependency-free test double (injected directly, not profile-selected —
  mirrors `llm.ScriptedLLM`).

The **topology-hop filter** (I2b, ADR-0007): `search_incidents` with a focus
`device` calls `adapter.hops_within(device, hops)` (default 2) → the set of nodes
within N hops, and passes it as the `nodes` prefilter, so only incidents on nearby
devices come back. The adapter owns the `/topology` `{source,target}` shape
(`hops_within_links` BFS in `adapter/contract.py`) — the registry never touches raw
link dicts (ADR-0006). I3's `walk_topology_graph` builds on the same wiring.

Env for the real embedders (not secrets, kept out of the committed YAML because
`config.py` is another lane's file): `COPILOT_EMBED_BASE_URL`,
`COPILOT_EMBED_MODEL_NIM`, `COPILOT_EMBED_MODEL_LOCAL`, `COPILOT_EMBED_API_KEY`.
`COPILOT_KB_URI` points `/chat` at a seeded LanceDB so the search tools work over
the HTTP seam; unset → the KB is absent and the tools report "backend not available".
`COPILOT_SKILLS_DIR` points `get_skills` at the seeded diagnostic-skills dir
(`copilot/skills/content`, S3) so the loop advertises `load_skill` + a catalog;
unset → no steering (byte-identical to a skills-free run, ADR-0012).

Self-checks (fixture corpus, `HashEmbedder`): `python3 -m copilot.retrieval.test_retrieval`,
`python3 -m copilot.tools.test_tools`, and `python3 -m copilot.skills.content.test_content`
(9 seeded skills load + disclose).

Not built yet: the KB corpus is a test fixture — real content is S1/S2 seeding.
`add` is append-only (no upsert-on-id) until the seeder lands.

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
