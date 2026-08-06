# Latest Docs — Reference Manual

This folder is a code-derived reference manual for the air-gapped predictive-NOC copilot: for
each subsystem, the actual params (name, default, env var, unit, meaning, file:line), data
sources, and calculations, re-derived from the code itself. It complements `docs/` at the repo
root, which is narrative/design (ADRs, architecture, plans) — this folder answers "what does this
knob do and where does this number come from," `docs/` answers "why does this exist."

## Table of contents

| Doc | Subsystem | What it answers |
|---|---|---|
| [copilot-adapter-tools.md](copilot-adapter-tools.md) | copilot/adapter, copilot/tools | How the agent's tool calls reach dataapi (StubAdapter/HttpAdapter), tool registry + dispatch |
| [copilot-agent.md](copilot-agent.md) | copilot/agent | The F3 investigate() loop and the two-stage answer gate (I4a/I4b) |
| [copilot-api.md](copilot-api.md) | copilot/api | The `/chat` SSE service, trace event schema, `/cases` and artifact routes |
| [copilot-detector.md](copilot-detector.md) | copilot/detector | Anomaly-detector stub status, fixture builder, contract-vs-fixture mismatch |
| [copilot-emulator.md](copilot-emulator.md) | copilot/emulator | Prediction Record emulation from ground-truth labels, real-PA client, predictor loop |
| [copilot-forensic-e2e.md](copilot-forensic-e2e.md) | copilot/forensic, copilot/e2e | Case creation/replay on alert, multi-chat synthesis, the live-stack E2E harness |
| [copilot-llm-retrieval-memory.md](copilot-llm-retrieval-memory.md) | copilot/llm, copilot/retrieval, copilot/memory | LLM client/profile swap, RAG over LanceDB, SessionStore + Event Ledger |
| [copilot-window-eval-skills.md](copilot-window-eval-skills.md) | copilot/window, copilot/eval, copilot/skills, copilot/workspace | PA tensor builder, WindowContext, eval seed, skill loader, sandboxed workspace |
| [faults.md](faults.md) | faults/ | Fault injection scenarios, orchestrator state machine, `t_impact`/signature math, labels.jsonl schema |
| [pa_alerts.md](pa_alerts.md) | pa_alerts/ | Prediction-to-alert bridge, rank vs threshold mode, calibration |
| [dataapi.md](dataapi.md) | dataapi/ | HTTP contract over VictoriaMetrics/Loki/nfacctd/labels/topology, the export.py ML join, faults proxy |
| [telemetry.md](telemetry.md) | telemetry/ | 4-pillar collection stack (metrics/logs/flows/environmental), VictoriaMetrics + Loki wiring |
| [synthetic-generator-topology.md](synthetic-generator-topology.md) | generator/, topology/, trafficgen/, synthetic/, ragcorpus/ | Topology generation, deployed output, traffic gen, synthetic dataset pipeline, RAG corpus |
| [grafana-ui.md](grafana-ui.md) | grafana ui/plugin | Grafana app plugin: pages, DataClient contract, topology/health math, config |
| [infra-deploy-airgap.md](infra-deploy-airgap.md) | deploy/, airgap/, controller/, frr-node/, streaming/ | Bring-up/teardown, air-gap image bundling, SD-WAN controller sim, router image, Kafka bridge |

## Subsystem map

- **Copilot** (8 docs: adapter-tools, agent, api, detector, emulator, forensic-e2e,
  llm-retrieval-memory, window-eval-skills) — the LLM investigation stack. `api` is the network
  entry point, calls `agent` (loop+gate), which calls `adapter-tools` (reads dataapi),
  `llm-retrieval-memory` (model + RAG + session state), and `window-eval-skills` (time-range
  struct, diagnostic skills). `emulator` feeds Prediction Records to `agent`'s gate and to
  `forensic-e2e`'s trigger, which fires cases back through `api`. `detector` is an unused stub, not
  wired into this chain yet.
- **Fault/PA simulation** (faults, pa_alerts) — `faults/orchestrator.py` injects faults into the
  live lab and writes ground-truth `labels.jsonl`. `pa_alerts` polls the PA prediction service and
  turns risk scores into alerts for a Grafana panel; independent of copilot chat.
- **Data plane** (dataapi, telemetry, synthetic-generator-topology) — `telemetry` producers
  (SNMP/syslog/IPFIX/controller) land in VictoriaMetrics/Loki. `dataapi` fronts that stack plus
  labels/topology with one HTTP contract and joins it into the ML Parquet dataset.
  `synthetic-generator-topology` generates the lab topology config that everything else targets,
  and separately calibrates/generates the offline synthetic training corpus.
- **UI** (grafana-ui) — the Grafana app plugin's `HttpDataClient` is the only DataClient wired in;
  it calls dataapi (`:8000`) for topology/telemetry/incidents and the copilot `/chat` (`:8100`) for
  the chat surface.
- **Infra** (infra-deploy-airgap) — provisions the host, packages images for air-gapped deploy,
  runs the simulated SD-WAN controller (that faults' overlay-flagged scenarios POST to) and the
  FRR router image every topology node uses, and bridges telemetry into Kafka.

Data flow: `synthetic-generator-topology` (generator) renders `topology/` -> `infra-deploy-airgap`
stands up the lab (controller, frr-node) -> `faults` injects faults + writes labels, `telemetry`
collects metrics/logs/flows -> `dataapi` joins telemetry + labels -> `grafana-ui` and `pa_alerts`
read dataapi; `copilot` (8 docs) reads dataapi via its adapter and consumes PA Prediction Records
via its emulator.

## Where to find X

| Question | Doc |
|---|---|
| What does `PA_RISE_MARGIN` do? | [pa_alerts.md](pa_alerts.md) |
| How is `t_impact` computed? | [faults.md](faults.md) |
| Where does tunnel latency data come from? | [telemetry.md](telemetry.md) |
| What are the 21 fault scenarios? | [faults.md](faults.md) |
| HttpDataClient vs MockDataClient? | [grafana-ui.md](grafana-ui.md) |
| What is the `/chat` SSE trace event schema? | [copilot-api.md](copilot-api.md) |
| How does the agent decide to abstain / gate an answer? | [copilot-agent.md](copilot-agent.md) |
| How does StubAdapter differ from HttpAdapter? | [copilot-adapter-tools.md](copilot-adapter-tools.md) |
| How are Prediction Records emulated without a real PA model? | [copilot-emulator.md](copilot-emulator.md) |
| What happens when a Prediction Record alerts (forensic case)? | [copilot-forensic-e2e.md](copilot-forensic-e2e.md) |
| Where does the LLM endpoint/profile get swapped (NIM vs local)? | [copilot-llm-retrieval-memory.md](copilot-llm-retrieval-memory.md) |
| What is the 59-column ML dataset Parquet join? | [dataapi.md](dataapi.md) |
| How does the synthetic dataset calibrate against real captures? | [synthetic-generator-topology.md](synthetic-generator-topology.md) |
| What does the air-gap verifier prove about container egress? | [infra-deploy-airgap.md](infra-deploy-airgap.md) |
| Is the anomaly detector implemented? | [copilot-detector.md](copilot-detector.md) |
| How does the PA tensor `(L=168, C=28)` get built? | [copilot-window-eval-skills.md](copilot-window-eval-skills.md) |
