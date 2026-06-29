# Agent Handoff — Air-Gapped Predictive NOC Copilot (network + data foundation)

You are taking over an in-progress project. This file orients you. **Read the linked docs — do not re-derive what they already record.**

## What this repo is
A reproducible, air-gapped **Containerlab SD-WAN-over-MPLS** lab that produces realistic, labeled NOC telemetry for an ISRO BAH 2026 entry. Scope here = the network simulation + telemetry + data foundation (Objectives/Phases 1–2). The AI/ML/RAG modelling is a separate team's job; this repo is their **data source + clean API**.

## Read first, in this order
1. **`CLAUDE.md`** — the operating rules (authoritative, overrides defaults). Working principles, agent-model strategy, the standing 5-step workflow, and the commit-attribution policy. Follow it exactly.
2. **`PLAN.md`** — the full design: target architecture, phases, decisions, reuse list, verification checklist.
3. **`DOCS/01_PROJECT_OVERVIEW.md` → `05_TECHNICAL_GLOSSARY.md`** — the 5-doc set written for AI/ML readers with zero networking background: overview, analogies, code/data-API guide, copy-paste cheatsheet, glossary.
4. **`DOCS/SPEC-NOTES.md`** — addressing scheme + generator production rules + the per-site netem design.
5. **`DOCS/PHASE0ENVIRONMENT.md`** — kernel prerequisites (run before deploying on a fresh host).
6. **Component READMEs**: `generator/`, `controller/`, `trafficgen/`, `faults/`, `synthetic/`, `airgap/` (and `dataapi/schema/`).

## How it's built (component map)
- **`generator/`** — `generate.py` + Jinja2 templates render the entire lab (`clab.yml` + all per-node FRR/snmpd/qos/wireguard config + the telemetry node-mappings) from one spec, `topology-spec.yaml`. Idempotent; `--check` guards addressing. Owns the single-source geography formula `site_netem()`. **Scale by editing the spec's top knobs + re-running.**
- **`frr-node/`** — the node image (FRR 10.5.1 + snmpd + pmacctd + tc + wireguard-go + rsyslog). Tag `frr-node:0.1`.
- **`telemetry/`** — `docker-compose.yml` stack on docker net `clab`: Telegraf (SNMP + scrape controller) → VictoriaMetrics; Grafana (NOC dashboard); pmacctd → nfacctd (IPFIX flows); FRR rsyslog → promtail → Loki. **Universal join key = `device`.**
- **`controller/`** — simulated SD-WAN controller; **measures** per-tunnel RTT (`ping -I wg0`) + models congestion; Prometheus on :9362.
- **`trafficgen/`** — diurnal per-VRF traffic (nc backend) so counters/flows move.
- **`faults/`** — `injectors.py` (netem/flap/BGP/kill/rekey/drift, each reversible) + `orchestrator.py` (single scenarios + `--campaign` mode) writing the ground-truth **labels timeline** (joinable on device+time).
- **`dataapi/`** — FastAPI (localhost): `/metrics /events /flows /labels /topology /datasets`; `export.py` joins everything → canonical Parquet (schema in `dataapi/schema/`). `ragcorpus/` seeds the RAG team.
- **`synthetic/`** — `calibrate.py` (profile from real captures) + `generate.py` (ML-scale labeled time-series in the SAME canonical schema; `--scale`/`--days`).
- **`airgap/`** — `pull-and-save` / `load-offline` / `verify-airgap` (zero runtime egress).

## Current state (as of commit `3a2702a`)
- **All PLAN phases done + acceptance-verified**, then **scaled to 130 containers** (8 P, 10 PE, 24 branch, 6 hub, 4 dc = 52 FRR + 78 hosts). Converges ~87s.
- **MPLS depth:** PE dual-homing (2 P uplinks each for underlay failure coverage); BFD 300ms detect on all core OSPF links (bfdd running on all P+PE nodes); MP-BGP route reflectors (pe1+pe2 as RR, pe3–pe10 as clients — 17 sessions vs. 45-session full mesh); hub-hub WG links between adjacent hub pairs (hub1↔hub2, hub3↔hub4, hub5↔hub6).
- **Telemetry fixes:** frr-snmp AgentX broken — replaced by `noc-ldp-metrics` sidecar (docker+vtysh push to VM); IPFIX `pcap_filter=(ip or ip6) and not mpls` fixes NULL flows; Promtail pipeline_stages extract BGP/LDP/OSPF events as structured labels.
- **Faults:** 12 named scenarios (was 7); added: MplsUnderlayFailure, LdpSessionFlap, HubSpokeCongest, BgpCascade, ControllerDrift. Controller drift: `POST /fault/drift` suppresses failover; `sdwan_controller_drift_active` metric tracks it.
- **Realism:** per-tunnel latency **measured** from per-site `eth0` netem (branch ~41ms > hub ~17ms > dc ~12ms) + modelled congestion; per-VRF app profiles; diurnal + weekly envelope; fault campaign mode.
- **Synthetic:** stochastic `lead_time` (Gamma dist.), cascade co-occurrence model (12%), 2 new fault types; ~8.9M-row labeled dataset, concat-compatible with real exports.
- **Telemetry:** all 4 pillars flowing for 52 devices, normalized to one `device`-joined schema.
- The lab + stack are typically left **running**; `containerlab inspect -t topology/clab.yml` and `docker compose -f telemetry/docker-compose.yml ps` show state.

## How to run / verify
- Regenerate: `cd generator && python3 generate.py` (then `--check`). Deploy: `cd topology && sudo containerlab deploy -t clab.yml`. Bring up stack: `cd telemetry && docker compose up -d`.
- Redeploy after a topology/image change: stop stack (`docker compose stop`), `containerlab destroy` + `deploy`, then `docker compose down && up -d` (re-resolves the clab network).
- Verification commands (control plane, WG, telemetry, faults, data API, air-gap) are in **`DOCS/04_USABILITY_CHEATSHEET.md`** and PLAN.md's Verification section. Use them — don't guess.

## How to work here (from CLAUDE.md — non-negotiable)
- Apply **YAGNI + `/ponytail:ponytail full`** (and `/caveman` for prose). No redundant code; shortest working diff.
- **opus** for code/reasoning/agents, **sonnet** for menial; **parallelise** (workflows with sonnet agents, or fan out parallel agents with disjoint file ownership).
- After every substantial change run: **plan → code (agents) → verify (real evidence) → document (update `DOCS/` + component READMEs) → commit + push**.
- **Commits:** author = `Aarush Mahajan <aarushmahajan.dev@gmail.com>`; **never** add `Co-Authored-By: Claude` or `Claude-Session` trailers.

## Known ceilings / outstanding (not blockers)
- Tunnel **congestion** dynamics are still modelled in the controller (the propagation baseline is now real/measured). Deepest upgrade = move WG onto the dataplane underlay so congestion is on-path too (Option A — big rewrite, deferred).
- Air-gap `verify-airgap.sh` proves zero egress but full image-save is large; run `airgap/pull-and-save.sh` on a connected host before going offline.

## Git
- Remote: `github.com/aarush-dev/mpls-lab`, branch `main` @ `3a2702a`. Generated artifacts (`topology/`, datasets, `airgap/images/`, WG keys, `refs/`) are gitignored — reproduce via the generators.
