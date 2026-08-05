# 10 — Future Prospects

**What remains to build, the known-open items, and the roadmap.**

← [09 Deployment & Demo](09_DEPLOYMENT_AND_DEMO.md) · [00 Project Overview](00_PROJECT_OVERVIEW.md)

---

## 1. Where the project stands

The **data foundation is done and verified live**: the 148-container network, the telemetry
pipeline, fault injection with ground-truth labels, the Data API contract, the synthetic generator,
and air-gap packaging. The **intelligence layer is mostly built**: the copilot agent core,
its tools, retrieval, memory, the quality gate, the PA-emulator (a stand-in for the real prediction
engine), and the forensic chain all run. An end-to-end pass has already answered real questions
with citations.

What's left splits into **major workstreams** (whole capabilities not yet built) and **known-open
items** (specific, tracked gaps in what exists). This document is deliberately conservative — it
lists only what is genuinely outstanding, sourced from `HANDOFF.md`, `PLAN.md`, and
`docs/SPEC-NOTES.md`.

---

## 2. Major future workstreams

### 2.1 The real prediction stack
The predictive ML stack — feature/representation learning, the expert models (forecast / hazard /
classification / graph), the meta-learner (the model that combines the expert models' outputs into
one decision), drift detectors, and conformal calibration (a method for turning raw scores into
confidence bounds with a proven error rate) — is a **separate workstream and is not built in this
repository.** Its design exists (`docs/plans/PA.md`), and the connection point to the copilot is
*ready*: the copilot consumes exactly one JSON **Prediction Record**, and the full-fidelity
**PA-emulator** stands in behind `emulate_pa`. Wiring the real stack in is a config switch —
`emulate_pa=false` routes to `real_pa()`, which today just raises an error, because that producer
doesn't exist yet (`copilot/emulator/emulate.py:330-334`). *(Per the project owner, no
documentation is written for this stack until it is implemented.)*

### 2.2 Closing the air-gap on inference
The data/telemetry stack's air-gap (no internet access) is verified, but the copilot's default LLM
and embedder (the model that turns text into vectors for search) use the **network-hosted `nim`
profile** (NVIDIA's public endpoint) — the interim path chosen in ADR-0004. The `unsloth-local`
profile already exists as a configured, code-complete alternative; what's still needed is a
**fully-offline end-to-end run** (local quantized model + local embeddings) and packing those
artifacts into the air-gap bundle. Until then, air-gap compliance is proven for the lab, but not
for inference.

### 2.3 Completing Milestone B (the coding agent)
The sandboxed workspace is built — the path cage, the no-network executor, the little-coder
read/write/edit tools, and artifact `present` all exist and are tested (doc 07 §7). What remains is
**broad use**: running the coding agent on real investigation tasks and hardening the tool
family beyond the seam tests.

### 2.4 Live streaming paths and an inject-time signal
The Kafka fan-out is verified in **replay**; its live `noc.events` (Loki) and `noc.topology` paths
need a running lab and haven't been exercised yet. Separately, the copilot's incident view is
**retrospective** — the orchestrator writes a fault label only when the fault is *reverted*, so a
`noc.faults` record means the fault has already ended (`HANDOFF.md` open item 9). The fix is to
publish the orchestrator's existing `campaign_inject` JSON to `noc.events` at the moment the fault
is injected.

### 2.5 A clean capture and recalibration (fixes the realism gap)
A real/synthetic discriminator (a classifier trained to tell real data from synthetic data) scores
**AUC ≈ 0.9999** (near-perfect — meaning it can tell them apart almost every time) — so the
synthetic distribution would not transfer as-is to real conditions (doc 02 §6). The root cause is
the **real capture's shortcomings**, not flaws in the synthetic data: the 24-minute capture had the
VPNv4 dataplane down and used default-calibrated fields. The needed fix (item **G5**) is a
**7-hour-plus clean capture and recalibration** of `profile.json`, which then unblocks the
full-scale synthetic run.

### 2.6 Real hardware
Several signals simply can't exist in a container lab and are reserved for real hardware: the three
interface error counters (`if_in_errors`/`if_in_discards`/`if_out_errors`), which the literature
ranks as the top failure signal, and a truly *measured* tunnel round-trip time (today the fault
term is read back from the netem qdisc config — a Linux kernel setting used to simulate network
conditions — rather than observed on the wire; doc 01 §6). The OIDs (the SNMP identifiers for these
metrics) are wired correctly and will start populating once deployed on real hardware.

---

## 3. Known-open items

Tracked escalations from the repair pass (`HANDOFF.md` §Known-open), condensed:

| # | Item | Nature |
|---|---|---|
| 1 | Tunnel RTT is modelled, not measured (needs a WireGuard endpoint over the L3VPN) | Realism |
| 2 | `bgp_cascade` can't use `vm_threshold` until a per-device BGP metric exists | Labeling |
| 3 | `t_impact` should be null on `probe_unavailable`; `export.py` would `TypeError` first | Correctness |
| 4 | Generator should emit `p_pe_ifaces` directly, instead of the orchestrator inferring them | Cleanliness |
| 5 | Telegraf's SNMP agent list and the generator have no single shared source of truth | Drift risk |
| 6 | The airgap image list is duplicated across 3 scripts | Maintainability |
| 7 | Synthetic `flow_bytes`/`flow_packets` are modelled, not calibrated from real flows | Realism |
| 8 | New deps (`jsonschema`, `kafka-python`) must be added to the offline wheel bundle | Air-gap |
| 9 | Copilot has no inject-time signal (see §2.4) | Integration |
| 10 | Interface error counters can't exist in containers (see §2.6) | Hardware |
| 11 | The live-path `ramp_derived` `t_impact` is untested against a lab (needs a long `--duration` run) | Verification |
| 12 | The real capture's labels were re-joined, not re-measured (predates `ramp_derived`) | Data provenance |

**Copilot-side gaps** (`CONTEXT.md`, `HANDOFF.md`):
- **Missing ticket #49** — the emulator collapses concurrency down to one record plus a count; a
  real emitter that produces *n* distinct per-device Prediction Records is not built.
- **Milestone C** (C1) and broad Milestone B use are not started.
- The KB seeder does not set `node` on incidents, so searching incidents by device
  (`search_incidents`) returns nothing (S1 follow-up); the default `/chat` still needs a seeded
  `COPILOT_KB_URI`.

---

## 4. Roadmap (competition phases)

The build plan (`PLAN.md`) frames the whole effort as six phases. This repository delivers Phases
1–2 (data foundation) plus the copilot subsystem that consumes Phase 3's output:

| Phase | Scope | State |
|---|---|---|
| **1** | Network simulation (SD-WAN over MPLS) | ✅ built, live-verified |
| **2** | Telemetry pipeline + fault labels + Data API | ✅ built, live-verified |
| **3** | Predictive modelling (forecast/hazard, lead-time targets) | ❌ separate workstream, not in this repo |
| **4** | Offline LLM deployment + RAG (retrieval-augmented generation) over a local corpus | ◑ copilot + RAG built; **offline LLM still open** (§2.2) |
| **5** | Copilot integration (predictions → LLM context → structured response) | ✅ copilot + forensic + UI built; consumes the emulator |
| **6** | Scenario validation (4 mandated + 3 adversarial, end-to-end lead-time/quality) | ◑ scenarios injectable; full end-to-end scoring still waits on the real stack |

---

## 5. Honest closing

**Proven today:** a realistic, reproducible, air-gapped network that emits labeled precursor
telemetry; a clean data contract; and a copilot that investigates that data and answers with
citations — or an honest "here's what's missing" — shown working end-to-end on real backends.

**Not yet proven:** the predictive models themselves (a separate workstream), fully-offline
inference (the LLM still calls out over the network), and the full inject-to-remediation loop
scored end-to-end. Each of these is scoped, and the connection points between them — the
Prediction Record, the `unsloth-local` profile, the inject-time event — already exist. What's left
is filling them in, not redesigning around them.

---

← Back to [00 — Project Overview](00_PROJECT_OVERVIEW.md)
