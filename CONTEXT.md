# CONTEXT — Predictive NOC Copilot subsystem

Domain glossary + component map for the **Copilot subsystem** (the LLM-facing half of the
air-gapped predictive NOC pipeline). Prediction stack (4 experts + meta-learner) is a **separate
workstream** — this doc covers only what consumes its output.

Decisions live in `docs/adr/`. This file is the vocabulary; ADRs are the reasoning.

---

## What this subsystem is

Two systems on **one conversational agent core**:

- **Forensic system** — fires itself when the PA (prediction) system reports a fault, freezes what
  it saw, produces a root-cause report, then stays open for follow-up chat.
- **Query system** — a human asks anything about the network; it investigates past + live data on
  demand, asking clarifying questions when the request is vague.

Both share: the agent loop, the tools, retrieval, the quality gate, memory. They differ only in
**who starts them** and **the data window**.

Built in two milestones (ADR-0017): **A = Investigator** (read-only), **B = Coding agent**
(executes code in a scoped workspace). Same loop; B layers on.

---

## Glossary

Use these terms exactly; don't drift to synonyms.

| Term | Definition |
|---|---|
| **Copilot subsystem** | The LLM-facing half: agent core + tools + retrieval + trust gate + memory + the two systems. NOT the prediction stack. |
| **Prediction stack / PA** | The 4-expert + meta-learner model that emits **Prediction Records**. Out of scope; a separate team owns it. "PA" = Predictive Analysis system. |
| **Prediction Record** | The one JSON object the PA emits per prediction (`docs/plans/PA.md` §3.3): fault-type PMF, cumulative incidence, time-to-impact, localization, anomaly token, calibrated decision, drift/health state. The **only seam** between copilot and prediction stack. |
| **PA-emulator** | Stand-in that produces Prediction Records from `/labels` ground truth while the real PA is unbuilt. Behind the `emulate_pa` flag. Full §3.3 fidelity. (ADR-0003) |
| **Forecast** | Part of a Prediction Record: quantile telemetry trajectory. |
| **Agent core / loop** | The owned `think → pick tool → run → observe → decide → cited answer` loop. ~150 lines, not a framework. (ADR-0005) |
| **Investigation tools** | Read-only tools on the adapter: `query_metrics`, `search_logs`, `walk_topology_graph`, `search_runbooks`, `search_incidents`, plus `flows` (via `/flows`). **Provisional set** — pruned/merged by measured use (ADR-0017); "five" is the core, `flows` the first candidate beyond it. |
| **Workspace tools** | The 4 coding tools (Milestone B): `read`, `write`, `edit`, `bash`. little-coder invariants, scoped to the scratchpad. (ADR-0011) |
| **Tool adapter** | One layer wrapping the data API so endpoint changes move the adapter, not the agent — the dataapi endpoints are **not a trusted-final contract** yet, so nothing hard-couples to them. Enforces mandatory filters + caps. (ADR-0006, ADR-0015) |
| **WindowContext** | `{start, end, frozen}` threaded into every tool call. Three cases: Live = rolling `now−X`; **Query = the arbitrary/historical period the human names** (agent resolves or asks; defaults to rolling); Forensic = frozen at `T`. Copilot-owned, not a shared service. (ADR-0002) |
| **Retriever** | The interface over the corpus, backed by embedded LanceDB. (ADR-0006) |
| **Knowledge Base (KB)** | Runbooks + incidents + vector index. What the agent looks things up in. (memory domain) |
| **Topology graph** | The **real** network wiring from `/topology` (`topology-spec.yaml`). Trustworthy. Blast-radius runs on this. (ADR-0007) |
| **Knowledge graph (KG)** | Curated graph attaching incidents/runbooks to devices. Low-confidence; **feature-flagged, default-on, never critical-path**. (ADR-0007) |
| **Quality gate** | Two-stage check before answering: deterministic pre-gate + self-judge LLM call → `{pass, missing[]}`. Fail → agentic retry (≤2) → else report missing. Pass → cite everything. (ADR-0008) |
| **Runbook** | Knowledge *about* a fault (symptoms, triage). Retrieved as **evidence**, cited. |
| **Diagnostic skill** | Instructions on *how to investigate*. Progressive-disclosure files (Claude-Code style). Agent-selected or human-invoked. The **method** the agent follows, vs a runbook's evidence. (ADR-0012) |
| **Detector / trigger** | What fires the Forensic system = a Prediction Record with `alert==true` arriving. No separate threshold-watcher. (ADR-0014) |
| **Episode** | One fault occurrence. The PA emits many records over an episode; **one case per episode** (dedup by `scenario_id`/device+fault), not one per record. (ADR-0014) |
| **Case** | A reproducible investigation of one episode. Self-contained folder; its **report + verdict (`case.md`) is its identity**, not its chats — `case.md` is human-readable markdown on disk, **CLAUDE.md-style** (browsable, git-diffable). Born only from a Forensic trigger. (ADR-0009) |
| **Session** | A resumable conversation. Persisted. A normal Query chat is a standalone session; a case owns several. (ADR-0009) |
| **Scratchpad** | Per-session persistent working dir the agent writes/executes in (Milestone B). Never auto-wiped. (ADR-0011, ADR-0013) |
| **Artifact** | A presented output (chart/code/file), snapshot-copied at present-time, referenced by an `artifact` event. (ADR-0009) |
| **Event Ledger** | Append-only SQLite record of Prediction Records + journal + gate outcomes. (memory domain) |
| **Deployment mode** | `emulate_pa`, `kg_enabled`, LLM/embedder profile (`nim` / `unsloth-local`), `ledger_to_kb`, `history_compaction` — all master-config flags. |

---

## The five memory domains (ADR-0009)

| Domain | Purpose | Store |
|---|---|---|
| **Live Observability** | raw network truth, now | Loki / VictoriaMetrics / nfacctd — **live, never copied**; ≥7d retention floor |
| **Knowledge Base** | what the agent looks up | LanceDB + markdown in git; human/verdict-curated |
| **Event Ledger** | the system's timeline | SQLite, append-only |
| **Case Archive** | reproducible postmortem bundles | `cases/<id>/` immutable files |
| **Session Store** | working memory of conversations | `sessions/<id>/`, persisted, resumable |

---

## Component map

```
PA / PA-emulator ──Prediction Records──▶ Event Ledger
                                             │
                          (alert==true) ─────┤ Forensic trigger (10s loop, episode dedup)
                                             ▼
   human ──▶ Query system ──┐        Forensic system ──▶ freeze window ──▶ Case
                            ▼                                                │
                      Agent core (loop)  ◀── WindowContext ── both ─────────┘
                            │
          ┌─────────────────┼─────────────────┐
          ▼                 ▼                 ▼
   Investigation tools   Retriever(LanceDB)  Workspace tools (B)
   (5, filtered, capped)  KB: runbooks/incidents  scratchpad exec (sandboxed)
          │                 │
          ▼                 ▼
      Tool adapter ──▶ dataapi (/metrics /events /flows /topology)
          │
          ▼
   Quality gate (deterministic + self-judge, ≤2 retry) ──▶ cited answer / "what's missing"
          │
          ▼
   FastAPI service (streamed timestamped trace) ──▶ demo app / dashboard
```

---

## Out of scope

- The prediction stack itself (4 experts, meta-learner, drift detectors, conformal) — separate team.
- Dataset/simulation generator (`research/13`,`14`) — prerequisite input, separate workstream.
- Eval scoring harness — deferred to post-build (ADR-0017).
