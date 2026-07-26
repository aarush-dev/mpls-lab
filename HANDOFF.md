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
- **`generator/`** — `generate.py` + Jinja2 templates render the entire lab (`clab.yml` + all per-node FRR/snmpd/qos/wireguard config + the telemetry node-mappings) from `topology-spec.yaml`. Reads only `ce_asn_base` / `wg_overlay_subnet` / `wg_port` from the spec's addressing/underlay/overlay blocks — the rest of those blocks are reference-only comments, everything else is derived from node indices in `generate.py` itself (`generator/generate.py:22-30`). Idempotent; `--check` guards addressing.
- **`frr-node/`** — the node image (FRR 10.5.1 + snmpd + pmacctd + tc + wireguard-go + rsyslog). Tag `frr-node:0.1`.
- **`telemetry/`** — `docker-compose.yml`, 9 services on docker net `clab`: Telegraf (SNMP + scrape controller) → VictoriaMetrics; Grafana (11-panel NOC dashboard); pmacctd → nfacctd (IPFIX flows); FRR rsyslog → promtail → Loki. **Universal join key = `device`.**
- **`controller/`** — simulated SD-WAN controller. Tunnel RTT/loss/jitter is **modelled**, read back from the site's netem qdisc config, not independently measured (see escalation 1 below); Prometheus on :9362.
- **`trafficgen/`** — diurnal per-VRF traffic (nc backend) so counters/flows move.
- **`faults/`** — `injectors.py` (netem/flap/BGP/kill/rekey/drift, each reversible) + `orchestrator.py` (single scenarios + `--campaign` mode) writing the ground-truth **labels timeline** (joinable on device+time). 21 scenarios.
- **`dataapi/`** — FastAPI (localhost): `/metrics /events /flows /labels /topology /datasets`; `export.py` joins everything → canonical 40-column Parquet (schema in `dataapi/schema/`). `ragcorpus/` seeds the RAG team.
- **`synthetic/`** — `calibrate.py` (profile from real captures) + `generate.py` (ML-scale labeled time-series in the same canonical schema; `--scale`/`--days`).
- **`airgap/`** — `pull-and-save.sh` / `load-offline.sh` / `verify-airgap.sh` (zero runtime egress).

## Current state
A 105-finding repair pass just landed (commits through `ed06dd8a`) fixing bugs across the generator, controller/faults, dataapi, synthetic, and telemetry/airgap subsystems. Full detail: the repair notes covering every changed behaviour. Topology and counts, re-derived from code:

- **148 containers** = 70 FRR (24 P + 12 PE + 34 CE: 24 branch / 6 hub / 4 dc) + 78 hosts (`generator/generate.py:846-848`, `topology-spec.yaml:14-27`).
- **Core:** 6 POPs × 4 P routers, multi-area OSPF (intra-POP mesh areas 1–6 cost 10, inter-POP ring+chords in area 0 cost 100, ABR–ABR link forced into area 0). 12 PE routers, 2 per POP, dual-homed.
- **iBGP:** route reflection — pe1+pe2 as RRs, pe3–pe12 clients = 21 sessions (full mesh would be 66).
- **VPNv4 RD** is per-PE (`<pe_loopback>:<vrf>`), not a shared `65000:<vrf>`.
- **Overlay:** 168 spoke-hub WireGuard tunnels + 3 hub-hub, 28 spokes across 6 hubs.
- **VRFs:** CORP / VOICE / GUEST. **ASNs:** branch 65101–65124, hub 65201–65206, dc 65301–65304.
- **Faults:** 21 scenarios. 4 lost their probes and are now `impact_method: modelled` (not `vm_threshold`): `hub_spoke_congest`, `bgp_cascade`, `brownout`. New `impact_method: probe_unavailable` for empty-probe windows.
- **Telemetry:** 9 compose services, 70 SNMP agents (all FRR nodes), 11 Grafana panels — all verified against actual metric emitters this pass.
- **Data schema:** 40 Parquet columns (was 21 pre-repair).
- **Host:** 19 cores / 108 GB RAM / 1007 GB disk.

## What is verified vs. not
- Verified this pass (by reading/re-deriving from code): the counts above, the fault `impact_method` changes, the dataset schema column count, the airgap compose `pull_policy: never` keys, Grafana panel-to-metric mapping.
- **NOT verified against a running lab.** The lab containers are currently down (`docker ps -a` returns none) and `dataapi/datasets/` does not exist on disk. Nothing in the repair pass — the fixes above or the counts confirming them — has been checked end-to-end against a live deploy. Next agent must deploy (`containerlab deploy`, `docker compose up -d`) and run the verification steps in `PLAN.md` before trusting any of this in production.
- The four previously shipped `datasets/*.parquet` (referenced in older docs) predate the 40-column schema and now fail `check_dataset.py`; they are not present in the current tree and need a full rebuild against a live stack.

## How to run / verify
- Regenerate: `cd generator && python3 generate.py` (then `--check`). Deploy: `cd topology && sudo containerlab deploy -t clab.yml`. Bring up stack: `cd telemetry && docker compose up -d`.
- Redeploy after a topology/image change: stop stack (`docker compose stop`), `containerlab destroy` + `deploy`, then `docker compose down && up -d` (re-resolves the clab network).
- Verification commands (control plane, WG, telemetry, faults, data API, air-gap) are in **`DOCS/04_USABILITY_CHEATSHEET.md`** and PLAN.md's Verification section. Use them — don't guess.

## How to work here (from CLAUDE.md — non-negotiable)
- Apply **YAGNI + `/ponytail:ponytail full`** (and `/caveman` for prose). No redundant code; shortest working diff.
- **opus** for code/reasoning/agents, **sonnet** for menial; **parallelise** (workflows with sonnet agents, or fan out parallel agents with disjoint file ownership).
- After every substantial change run: **plan → code (agents) → verify (real evidence) → document (update `DOCS/` + component READMEs) → commit + push**.
- **Commits:** author = `Aarush Mahajan <aarushmahajan.dev@gmail.com>`; **never** add `Co-Authored-By: Claude` or `Claude-Session` trailers.

## Known-open items (outstanding escalations from the repair pass — not fixed, recorded here)
1. **Tunnel RTT is modelled, not measured.** Genuine measurement needs the WireGuard endpoint repointed at a CE loopback over the L3VPN (`generator/generate.py:456-458`).
2. `bgp_cascade` cannot use `vm_threshold` until a device-scoped BGP metric exists in the telemetry pillar.
3. `t_impact` should be null on `probe_unavailable`, but `dataapi/export.py:206` would raise `TypeError` on that — needs an export change first.
4. Generator should emit `p_pe_ifaces` directly rather than the orchestrator inferring P-PE links from link ordering.
5. Telegraf's SNMP agent list and the generator have no single source of truth.
6. The airgap image list is triplicated across `airgap/pull-and-save.sh`, `airgap/load-offline.sh`, `airgap/verify-airgap.sh` — cannot be derived from one place.
7. Synthetic `flow_bytes`/`flow_packets` are null — needs new profile keys and a `calibrate.py` re-run against a live capture.
8. New dependency `jsonschema` (`dataapi/requirements.txt:10`) must be added to the offline bundle.
9. The four previously shipped `datasets/*.parquet` are stale (21 columns vs. the current 40) and fail `check_dataset.py`; need a rebuild against a live stack.
10. **The lab containers are currently down** — nothing in this repair pass was verified against a running lab.

## Git
- Remote: `github.com/aarush-dev/mpls-lab`. Current branch `sidd` @ `ed06dd8a` (tracks `origin/sidd`); `main` is behind at `0460ec5c`. Generated artifacts (`topology/`, `dataapi/datasets/`, `airgap/images/`, WG keys, `refs/`) are gitignored — reproduce via the generators.
