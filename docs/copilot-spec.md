# Spec — Copilot subsystem (Predictive NOC Copilot)

> Buildable spec for the LLM-facing half of the pipeline. Decisions + rationale live in
> `docs/adr/0001–0017`; vocabulary in `CONTEXT.md`. This doc is the source the tickets are cut from.
> Canonical copy is the tracker issue labelled `ready-for-agent`.

## Problem Statement

A NOC engineer watching an air-gapped SD-WAN-over-MPLS network gets a stream of model predictions
("congestion likely on tunnel X in ~5 min") and a flood of telemetry (metrics, logs, flows,
topology). Today there's nothing that turns a prediction into an *explained, evidence-backed*
diagnosis, and nothing that lets the engineer *ask* the network questions and get cited answers.
When a fault fires they must manually pull logs, correlate metrics, walk the topology, and remember
past incidents — under time pressure, offline, with no assistant. And when the automated part is
wrong, they have no way to see *why* or to tell that it's untrustworthy.

## Solution

An offline **Copilot subsystem** — two systems on one conversational agent core:

- **Forensic system** — fires itself when the prediction system reports a fault, freezes exactly what
  it saw, produces a root-cause **report + verdict**, and stays open for follow-up questions over that
  frozen snapshot. Reproducible postmortems.
- **Query system** — a human asks anything about the network (current or a named historical period);
  the copilot investigates real telemetry, walks the topology, retrieves runbooks and past incidents,
  and answers **with every claim cited to a specific piece of evidence** — or, when the evidence is
  thin, says exactly *what's missing* instead of guessing.

The agent works like an engineer works a ticket: it picks a tool, reads the result, decides the next
step, and the user **watches every step** (tool calls and quality checks are visible). It runs a
small local model, air-gapped, sandboxed. A small demo web app shows all of this live.

## User Stories

1. As a NOC engineer, I want a fault to auto-open an investigation the moment the system predicts it, so that I don't have to notice and react manually.
2. As a NOC engineer, I want the auto-investigation frozen to the exact moment it fired, so that my postmortem is reproducible and doesn't shift as new data arrives.
3. As a NOC engineer, I want to ask follow-up questions on a frozen case, so that I can dig deeper without the ground moving under me.
4. As a NOC engineer, I want to ask about the network right now, so that I can understand a developing situation.
5. As a NOC engineer, I want to ask about a specific past window ("last Tuesday 3–4pm"), so that I can investigate something that already happened.
6. As a NOC engineer, I want the copilot to ask me a clarifying question when my request is vague, so that it investigates the right thing.
7. As a NOC engineer, I want every claim in an answer cited to a specific log/metric/node, so that I can trust and verify it.
8. As a NOC engineer, I want the copilot to tell me what's missing instead of guessing when evidence is thin, so that I'm never misled by a confident-sounding wrong answer.
9. As a NOC engineer, I want to see the blast radius of a failing link ("what's downstream"), so that I can gauge impact.
10. As a NOC engineer, I want blast-radius enriched with live status per hop, so that I see which downstream parts are actually degraded now.
11. As a NOC engineer, I want the copilot to surface the relevant runbook for a fault, so that I know the fix/triage steps.
12. As a NOC engineer, I want the copilot to find past incidents similar to the current situation, so that I can reuse prior diagnoses.
13. As a NOC engineer, I want to watch the copilot's tool calls and checks as it works, so that I understand and trust what it's doing.
14. As a NOC engineer, I want the copilot to distrust a prediction when the model's own health signal is degraded, so that I'm warned when the automation is on shaky ground.
15. As a NOC engineer investigating concurrent faults, I want a separate chat per fault plus a master chat that combines them, so that overlapping incidents stay legible.
16. As a NOC engineer, I want to reopen and continue a past conversation, so that I can resume an investigation later.
17. As a NOC engineer, I want to read a colleague's conversation or case, so that I can pick up their work.
18. As a NOC team, we want only one person editing a conversation at a time, so that we don't clobber each other.
19. As a NOC engineer, I want the case's report and verdict stored as human-readable files on disk, so that I can browse and diff them like documentation.
20. As a NOC engineer, I want a case to keep its own frozen copy of the telemetry window, so that I can reconstruct it months later even after live data has expired.
21. As a NOC engineer, I want the copilot to run a quick analysis or draw a chart to support a diagnosis, so that I can see the evidence, not just read about it. *(Milestone B)*
22. As a NOC engineer, I want generated charts/code shown inline in the chat and preserved with the case, so that the analysis is part of the record. *(Milestone B)*
23. As a security-conscious operator, I want the copilot to be read-only against production data and unable to reach the network, so that it can't damage or leak anything.
24. As an operator, I want to swap the underlying model (interim hosted API → final on-prem model) with a config change, so that I'm not locked in and can go fully air-gapped.
25. As an operator, I want to run the whole thing before the real prediction model exists, so that I can build and demo now.
26. As an operator, I want the emulated prediction to be imperfect and realistic, so that the copilot is tested against a fallible predictor, not an oracle.
27. As an operator, I want to turn the curated knowledge graph off, so that correctness never depends on a component I don't fully trust.
28. As an operator, I want past case verdicts to optionally feed future incident search, so that the system gets smarter — with a switch to turn it off if it echoes bad conclusions.
29. As a developer, I want the copilot steered by editable skill files (how to investigate each fault), so that a weak model follows a reliable procedure.
30. As a developer, I want to manually invoke a specific diagnostic skill, so that I can direct the investigation.
31. As a developer, I want tool results to come back small and filtered, so that a small model's context never overflows.
32. As a developer, I want a demo web app that shows the agent working live, so that I can validate and present the copilot end-to-end.
33. As a developer, I want the model backend and data endpoints behind single adapters, so that changes to either don't ripple through the codebase.

## Implementation Decisions

**Architecture (ADR-0001).** Two systems — Forensic (auto-trigger → freeze → report → follow-up) and
Query (on-demand, past+live, ask-back) — share **one conversational agent core** (loop, tools,
retrieval, quality gate, memory). They differ only in *who starts them* and *the data window*.

**Prediction seam (ADR-0003).** The only boundary to the prediction stack is the **Prediction Record**
(`docs/plans/PA.md` §3.3): fault-type PMF, cumulative incidence, time-to-impact, localization, anomaly
token, calibrated decision, drift/health state. Behind an `emulate_pa` flag, a **PA-emulator** produces
full-fidelity records from `/labels` ground truth with an `error_profile` (oracle|light|heavy) and a
faked drift/health scalar, so the copilot builds and tests with zero dependency on the real model.

**Windowing (ADR-0002).** A copilot-owned `WindowContext {start, end, frozen}` threaded into every tool
call, in three cases: **Live** (rolling now−X), **Query** (the arbitrary/historical period the human
names; resolve or ask; default rolling), **Forensic** (frozen at T; reject any read past T).

**LLM backend (ADR-0004).** One OpenAI-compatible client selected by a config **profile**
(`nim`/gpt-oss-20b interim; `unsloth-local` final). Model/provider swap = one config line. Native
function-calling where supported, else an owned parser.

**Agent loop (ADR-0005).** Owned think→tool→observe loop (little-coder as reference, not a dependency),
with step + tool-call caps and ask-back.

**Tool adapter + investigation tools (ADR-0006, 0015, 0016).** One **adapter** wraps the data API (its
endpoints are not a trusted-final contract) and enforces a **mandatory filter contract** — every call
must carry a window + device/pattern + a hard low limit; unfiltered/over-broad calls are rejected;
results are small by construction with paging for more; result content is framed as **untrusted data**
(injection guard). Investigation tools ride the adapter: `query_metrics`, `search_logs`, `walk_topology_graph`,
`search_runbooks`, `search_incidents`, plus `flows` (via `/flows`). The set is **provisional** —
"five" is the core, `flows` the first candidate beyond it; pruned/merged later by measured usage.

**Retrieval (ADR-0006).** A `Retriever` interface (`add`, `search → [(doc, score, provenance)]`) backed
by **embedded LanceDB** (no server); embedder profile-swapped. Iterative retrieval; provenance on every
item.

**Graphs (ADR-0007).** Blast-radius = deterministic BFS on the **real** `/topology` + per-hop live-state
join from `/metrics`. Incident relevance = embeddings + a topology-hop proximity filter. The curated
**knowledge graph** is a backup/demo signal, feature-flagged (`kg_enabled`, default-on), **never on the
critical path** — correctness holds with it off.

**Quality gate (ADR-0008).** Two stages: a deterministic pre-gate (tool success, in-window,
entity-match, ≥N evidence) then a single **self-judge** call → `{pass, missing[], contradictions[]}`.
Fail → the agent re-enters the loop to fetch `missing[]`, up to `gate_max_retries` (2); still failing →
report what's missing. Pass → a citation check (every claim maps to an evidence id). `N` and
`gate_max_retries` are config.

**Steering (ADR-0012).** Progressive-disclosure **diagnostic skills** — markdown with `{name,
description}` frontmatter; only name+description in the base prompt, body loaded on match. Agent
auto-selects; a human can manually invoke. Distinct from runbooks (skill = method followed; runbook =
evidence cited).

**Memory (ADR-0009).** Five domains: Live Observability (queried live, ≥7d retention floor), Knowledge
Base (LanceDB + git markdown), Event Ledger (append-only SQLite), Case Archive (`cases/<id>/`,
self-contained), Session Store (`sessions/<id>/`, persisted, resumable). One **`events.jsonl` per
session**, every event timestamped, **all events user-visible** (tool_call/gate shown). A **case** is
born only from a Forensic trigger; its identity is `case.md` (human-readable, CLAUDE.md-style report +
verdict), not its chats; it holds many chats and **copies the concerned telemetry window** into itself.
Concurrent faults → n investigation chats + a master synthesis chat. Concurrency = per-conversation
single-writer lock; anyone may read any conversation. **No chat→case promotion.**

**Forensic trigger (ADR-0014).** The prediction system (or emulator) runs a ~10s predict loop writing
records; `decision.alert==true` fires the pipeline inline: freeze window → copy observability into the
case → write the record → generate the initial report → spawn chat(s). **One case per episode** (dedup
by scenario/device+fault); restart-safe via last-processed record id.

**Interface + demo (ADR-0010).** A local **FastAPI** service; the chat endpoint **streams a
timestamped step-trace** using ADR-0009's canonical event enum (`user_msg | assistant_msg | think |
tool_call | tool_result | gate | artifact`; `observation`=`tool_result`, `answer`=`assistant_msg`) so
stream and persisted log share one schema. A small **demo web app** consumes the trace
to show the agent working live (tool calls, evidence, citations, gate pass/fail), agentic-app style —
scaffolding until a real dashboard integrates through the same API.

**Coding agent (Milestone B — ADR-0011, 0013).** The agent gains a **workspace tool family**
(`read/write/edit/bash`) with little-coder invariants (read-before-edit, write-new-only, edit-exact) and
**our boundary**: writes/execution confined to a per-session persistent **scratchpad**, read-only
outside, copy-in-to-modify, no-network subprocess execution with timeouts. Presented outputs are
snapshotted into an append-only `artifacts/` and shown inline. Isolation is the file-handling policy +
subprocess — **no container**.

**Config surface.** Master config: profiles (`llm_profile`, `embed_profile`), flags (`emulate_pa`,
`kg_enabled`=on, `ledger_to_kb`=on, `history_compaction`=off), knobs (`X`, `N`, `gate_max_retries`=2,
`error_profile`, `predict_interval_s`=10). Secrets via `.env` (gitignored).

**Milestones (ADR-0017).** **A = Investigator** (read-only; complete + demoable on its own).
**B = Coding agent** (layers onto A's loop). Two disjoint file-ownership lanes so two people build in
parallel: **Lane-Investigation** (adapter/tools/retrieval/agent/skills) and **Lane-Runtime**
(llm/memory/window/emulator/forensic), converging at the API + demo.

## Testing Decisions

**What makes a good test:** assert **observable behavior**, not implementation. For the copilot the
observable behavior is *what the user sees at the API* — the streamed steps and the final cited answer
(or the "what's missing" message) — not internal function calls.

**Seams (confirmed):**
- **Behavior seam = the FastAPI HTTP endpoint.** Drive Query and Forensic end-to-end through the real
  loop, gate, window, memory, retrieval, trigger, and case archive.
- **Stub boundary 1 = the LLM client.** Scripted replies (tool-call sequences, judge verdicts, final
  text) make agent behavior deterministic without a model/GPU.
- **Stub boundary 2 = the tool adapter.** Canned telemetry makes tools testable without
  VictoriaMetrics/Loki running.
- **Stub boundary 3 (Milestone B) = the subprocess executor.** Fake results assert the workspace
  path/no-net policy without spawning code.
- Fixtures: oracle-profile Prediction Records, a tiny incident/runbook corpus, a fixture topology.

**A thin layer of real integration tests** where correctness can't be faked: a few hitting the **real
adapter query strings** (PromQL/LogQL against a seeded backend), and **1–2 real-sandbox** tests proving
no-net + timeout + path confinement actually bite (it's a security boundary).

**Manual end-to-end testing is first-class.** A small model's real answer *quality* can't be
unit-asserted; a human driving the **demo app** and reading the trace is a primary validation path for
full E2E, until the deferred eval harness (ADR-0017) automates scoring against `/labels`.

**Modules tested:** the HTTP surface (behavior), and targeted unit tests for gnarly deterministic bits
— topology BFS + `/metrics` enrichment, episode dedup, the gate's deterministic pre-gate, the
`WindowContext` three cases, the emulator's oracle↔ground-truth equivalence, the workspace path policy.

**Prior art:** assert-based validation in the style of `dataapi/check_dataset.py` (assert + `__main__`
self-checks); no heavy framework unless a module needs it (ponytail).

## Out of Scope

- The prediction stack itself — the 4 experts, meta-learner, drift detectors, conformal wrapper. A
  separate team; the copilot only consumes Prediction Records.
- The dataset/simulation generator (`research/13`, `14`) — prerequisite input, separate workstream.
- The **eval scoring harness** — deferred to post-build (ADR-0017); tool-usage data accrues free via
  `events.jsonl` meanwhile.
- The real NOC dashboard UI — later; the demo app stands in and the dashboard integrates via the API.
- Hardened sandboxing (containers/seccomp) — the file-policy + subprocess suffices until deployed
  adversarially.
- A separate critic model, reranking/query-decomposition, threshold-watcher detector, fail-closed
  air-gap gating — all explicitly rejected (see ADRs 0008/0006/0014/0004).

## Further Notes

- **Air-gap caveat:** the interim `nim` profile sends prompts to a third party — *not* air-gapped. The
  air-gap claim holds only on `unsloth-local`; any air-gap-property validation runs there.
- **Endpoints not final:** the dataapi endpoints aren't a trusted contract yet — the tool adapter is the
  only place coupled to their shape.
- **Diagnosis output format** (structured vs prose for answers and `case.md`) is left open, to be
  finalized inside its ticket against reference transcripts the user will supply.
- **`ledger_to_kb` echo risk:** feeding case verdicts back into search can reinforce an earlier wrong
  call — the reason it stays a toggle (default on).
