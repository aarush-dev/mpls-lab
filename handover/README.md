# Repository handover

Generated 2026-08-07 from `main` at `f4a3027e`. Read this first in a fresh
session. It is an index and live-state snapshot, not a replacement for source,
plans, ADRs, issues, or existing manuals.

## Mission

This repository builds an offline predictive NOC lab: generated
SD-WAN-over-MPLS topology, telemetry, fault injection, labeled datasets,
Predictive Analysis (PA) integration, copilot backend, and Grafana UI.

Authoritative context:

1. `../AGENTS.md` — operating rules, verification, documentation, commit policy.
2. `../PLAN.md` — original Phases 1–2 build plan and architecture.
3. `../CONTEXT.md` + `../docs/adr/` — copilot vocabulary and accepted decisions.
4. `../HANDOFF.md` — detailed implementation history. Treat status claims as
   dated; verify against code and GitHub issues.
5. `../docs/01_PROJECT_OVERVIEW.md` through
   `../docs/05_TECHNICAL_GLOSSARY.md` — user-facing architecture and operations.
6. `../docs/PHASE0ENVIRONMENT.md` — mandatory kernel checklist before deploy.
7. `../docs/copilot-build-plan.md`, `../DATASETS.md`, and component READMEs —
   subsystem plans, dataset catalog, and commands.

Code is ground truth. Do not propagate counts or behavior from another doc
without checking the generating code.

## Current verified state

Checks run on 2026-08-07 UTC:

- `main` equals `origin/main` at `f4a3027e` before this handover commit.
- Docker: 159 running containers — 148 generated lab containers and 11
  infrastructure containers. Source topology knobs:
  `../topology-spec.yaml:12-38`; generator totals:
  `../generator/generate.py:843-848`.
- Live `/topology`: 148 nodes and 234 links. Generated topology is
  `../topology/clab.yml`; never hand-edit it (`../topology/clab.yml:3`).
- Health returned HTTP 200: Grafana `:3000`, data API `:8000`, PA `:8001`, PA
  alerts `:8002`, copilot `:8100`, VictoriaMetrics `:8428`, and Loki `:3100`.
- Data API exposes metrics, events, flows, labels, topology, datasets, fault
  control, and PA alert proxy routes (`../dataapi/app.py:76-175`).
- Canonical dataset schema has 59 columns (`../dataapi/export.py:51-100`).
  Three tracked reference Parquets are cataloged in `../DATASETS.md`; other
  generated datasets are ignored.

This proves services were reachable during this handover. It does not replace
control-plane, fault-loop, model-quality, or air-gap gates in
`../docs/04_USABILITY_CHEATSHEET.md` and `../docs/PHASE0ENVIRONMENT.md`.

## Component map

- `../generator/`, `../topology-spec.yaml`, `../frr-node/`: generate and run the
  FRR/MPLS/WireGuard lab. Generated topology has 24 P, 12 PE, 34 CE, and 78
  host containers (`../topology-spec.yaml:12-38`).
- `../controller/`, `../trafficgen/`, `../faults/`: model overlay state, produce
  traffic, inject reversible faults, and write ground-truth labels. Controller
  tunnel telemetry is model-derived, not an independent measurement
  (`../controller/controller.py:575-582`).
- `../telemetry/`: VictoriaMetrics, Loki, Telegraf/SNMP, IPFIX, controller and
  environment metrics, Kafka, and supporting services
  (`../telemetry/docker-compose.yml:22-254`).
- `../dataapi/`, `../synthetic/`, `../streaming/`: normalized read API,
  real/synthetic dataset production, and Kafka fan-out. Host Kafka clients must
  use `127.0.0.1:29092`; bridge and consumer code default to `:9092`, so set
  `KAFKA_BOOTSTRAP` or pass the CLI override
  (`../telemetry/docker-compose.yml:211-239`, `../streaming/bridge.py:51`,
  `../streaming/consume.py:48`).
- `../copilot/`: shared Query/Forensic agent core, HTTP adapter, retrieval,
  quality gate, memory, PA emulator/real-PA seam, frozen cases, workspace, and
  FastAPI SSE service. Domain map: `../CONTEXT.md:74-98`.
- `../grafana ui/`: Grafana application and copilot UI.
- `../pa_alerts/`: live PA-to-dashboard alert bridge on `:8002`
  (`../pa_alerts/service.py:153-186`).
- `../airgap/`, `../deploy/`, root service units and start scripts: packaging,
  restore, service startup, and offline verification.
- `../bah_predictive_analysis/`: ignored nested Git repository with separate
  ownership and dirty state. Do not include its files in this repository's
  commits unless explicitly requested.

## Working-tree safeguards

Pre-existing user work at handover creation:

- Modified `../copilot-up.sh`: localhost health-check retry change.
- Untracked `../.idea/`.

Preserve both. Stage only files owned by the current task. Generated topology,
runtime cases/sessions/ledger, most datasets, WireGuard keys, local `.env`, and
air-gap images are ignored or host-local; never assume a clean clone contains
them. Do not copy secret values into issues, logs, commits, or handovers.

## Verification snapshot and blockers

Successful checks:

- Python, excluding two known blockers:
  `pytest -q controller dataapi faults telemetry copilot --ignore=copilot/emulator/test_real_pa.py -k 'not test_seed_matches_a_fresh_build'`
  — 301 passed, 1 deselected.
- Grafana UI typecheck:
  `grafana ui/plugin/node_modules/.bin/tsc --noEmit` — passed.
- Live container count and seven HTTP health checks — passed as recorded above.

Known failing checks; not fixed during documentation-only handover work:

1. Full Python collection fails because `../copilot/emulator/real_pa.py:20`
   imports missing `_alert_id` from `emulate.py`.
2. After ignoring that file, `../copilot/eval/test_scenarios.py:38` reports
   stale `scenarios.jsonl` versus a fresh build.
3. Grafana Jest: 133 passed, 1 failed. Failure is
   `../grafana ui/plugin/src/data/HttpDataClient.test.ts:85`; expected
   `r1:node_cpu_pct` series is absent.

Also verify config/gate behavior before changing it: dataclass default is true,
shipped YAML is false, and current loop calls the gate path directly
(`../copilot/config.py:61`, `../copilot/config.yaml:14`,
`../copilot/agent/loop.py:316`).

## Documentation drift already observed

Do not trust these claims without repair:

- PLAN/HANDOFF say local FRR file logging is disabled, while current template
  enables `/var/log/frr/frr.log` (`../generator/templates/frr.conf.j2:5`).
- Grafana README says 180-second copilot timeout; code uses 30 minutes
  (`../grafana ui/README.md:56`, `../grafana ui/plugin/src/config.ts:13`).
- Root HANDOFF has older Milestone B and branch-status statements that conflict
  with current code/Git.
- `../latest docs/` references missing `watchdog.sh` and `copilot/detector/`.

Fix affected docs in the same commit as any code/config correction, per
`../AGENTS.md`.

## Open work

GitHub had 44 open issues when checked. Fetch live state; do not copy issue
bodies here:

```bash
gh issue list --state open --limit 100
```

Current entry points:

- PA P0: [#129](https://github.com/aarush-dev/mpls-lab/issues/129) and
  [#130](https://github.com/aarush-dev/mpls-lab/issues/130).
- Ready PA data fix: [#133](https://github.com/aarush-dev/mpls-lab/issues/133).
- Critical copilot harness findings:
  [#115](https://github.com/aarush-dev/mpls-lab/issues/115) and
  [#116](https://github.com/aarush-dev/mpls-lab/issues/116).
- Real-PA epic: [#97](https://github.com/aarush-dev/mpls-lab/issues/97).
- Salvage-mode spec: [#90](https://github.com/aarush-dev/mpls-lab/issues/90).

Dataset transfer remains blocked by realism: synthetic-versus-real AUC is
0.9999; obtain a clean live capture of at least 7 hours, recalibrate, then rerun
the discriminator (`../docs/SPEC-NOTES.md:1260-1265`,
`../docs/SPEC-NOTES.md:1293-1312`).

## Starting work

1. Read `../AGENTS.md`, this file, task issue, and affected source end to end.
2. Preserve dirty files above. Run the Phase 0 checklist before any deploy.
3. Use `../sim-up.sh` for the lab and `../copilot-up.sh` for copilot only after
   reading their mutation/lifecycle notes. `copilot-up.sh` briefly writes a
   synthetic label and removes matching ledger rows (`../copilot-up.sh:67-118`).
4. Run narrow checks first, then affected full gates. Record real evidence.
5. Update affected docs, commit only owned files, and push `main` under the
   attribution policy in `../AGENTS.md`.

## Suggested skills

- `ponytail:ponytail full` — all code/config work; smallest root-cause diff.
- `caveman:caveman` — terse interactive prose; keep committed docs normal and
  concise.
- `mattpocock-skills:diagnosing-bugs` — current Python and Jest failures.
- `mattpocock-skills:code-review` — review changes against issue and repo rules.
- `mattpocock-skills:domain-modeling` — changes to `CONTEXT.md` or ADR decisions.
- `mattpocock-skills:resolving-merge-conflicts` — only for an active merge or
  rebase conflict.
