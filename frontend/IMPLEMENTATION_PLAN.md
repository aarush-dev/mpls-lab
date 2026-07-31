# Frontend Implementation Plan — Grafana NOC Copilot App Plugin

> **Scope (now): frontend + mock data only.** A modular React + TypeScript **Grafana App Plugin**
> (`mplslab-noccopilot-app`) that runs fully offline on bundled deterministic fixtures. No ML model,
> no copilot backend, no network lab, no telemetry backend, **no live API integration** — all
> represented by clearly-labelled mock data.
> **Later (not built now):** an `api` mode that connects the same DataClient to the real `dataapi`
> endpoints. The seam for that is kept thin (see §2) so it costs little later, but nothing live is
> implemented in this scope.
> Plan-first + milestone-gated per `CLAUDE.md`. Code is ground truth over docs.

---

## 0. Repository state at plan time (verified)

| Check | Finding |
|---|---|
| Plugin source exists? | **No.** `frontend/` did not exist; created fresh under `frontend/plugin/`. |
| Git | Clean, on `main` @ `58e96522`, up to date with `origin/main`. |
| `.tools/` | Absent. Not touched, not committed. |
| Committed Parquet | `dataapi/datasets/dataset_1785032386_1785033870_30s.parquet` (real, 599 KB); `synthetic/output/synthetic_1781481600_d1.0_s30_x3.0.parquet` + `_seed7.parquet` (~48 MB each). |
| Grafana core | Not forked, not edited. Folder-local Compose only. |

### Ground-truth notes (code beats the prompt)

1. **`frontend/README.md` / `API_CONTRACT.md` / `INTEGRATION_GUIDE.md` did not exist.** Authored here. `API_CONTRACT.md` documents the real endpoints from `dataapi/app.py` as the **future** api-mode target, marked not-yet-wired.
2. **Real metric/label names captured now** so mock fixtures match real shapes (see below) — makes the future api swap a data-mapping job, not a redesign.
3. **`/topology`** reads gitignored generated `topology/clab.yml`; **`/labels`** reads gitignored `faults/labels/*.jsonl`. Both absent on a fresh clone — irrelevant to mock scope; noted for the future api mode.
4. **Real capture = 10 fault types / 391 rows; synthetic = all 21.** Mock fixtures draw the 12 required scenarios from synthetic where the real capture lacks them, tagged by provenance.

### Verified real metric names (so fixtures/types match reality)

`interface_ifHCInOctets`, `interface_ifHCOutOctets`, `sdwan_tunnel_latency_ms`,
`sdwan_tunnel_jitter_ms`, `sdwan_tunnel_loss_pct`, `sdwan_tunnel_rekeys_total`,
`sdwan_controller_drift_active`, `sdwan_path_active`, `sdwan_path_changes_total`,
`ospf_neighbor_state`, `ospf_spf_last_duration_ms`, `ospf_lsa_count`,
`mpls_lsp_count`, `mpls_ldp_session_state`, `bgp_peer_established`,
`bgp_vrf_prefix_count`, `bgp_msg_rx_total`, `bgp_msg_tx_total`, `rib_routes`,
`cpu_pct`, `mem_pct`, `q_rows_b`, `q_rows_d`, `xcvr_temp_c`, `xcvr_rx_power_dbm`,
`xcvr_tx_bias_ma`, `device_temp_c`, `device_power_watts`, `device_fan_rpm`,
`device_psu_voltage_v`. Labels: `device`, `interface`, `tunnel`, `hub`, `peer`, `vrf`.
Note: tunnel metrics are keyed by `tunnel`/`hub`, not `device` — the query catalog reflects this
for the future api mode.

---

## 1. Data shapes the fixtures mirror (from `dataapi/app.py`, ground truth)

Fixtures reproduce these JSON shapes so the mock client returns real-shaped data and the future
api swap is a mapping job. Endpoints themselves are **not called** in this scope.

| Logical source | Row shape mirrored in fixtures |
|---|---|
| metrics | Prometheus vector/matrix; values are **strings** |
| events | `{ts, device, app, severity, line}` |
| flows | `{ts, device, ip_src, ip_dst, port_src, port_dst, proto, bytes, packets}` |
| labels → incidents | ground-truth fault rows grouped by `scenario_id` |
| topology | node `{id, role, site_type?, vrfs?}`; link `{source, target, source_if, target_if}` |

FastAPI error shape `{ "detail": "..." }` is recorded in `errors.ts` for the future api mode only.

---

## 2. Architecture (mock-first, api-ready seam)

- **App Plugin**, id `mplslab-noccopilot-app`, React + TS, `@grafana/ui` + `@grafana/data`.
- **One mode decision at init** (`config.ts`): `AppConfig { mode: 'mock'|'api'; apiBaseUrl;
  requestTimeoutMs; showDemoBadge }`. **`mode` defaults to `'mock'`; `'api'` is a future path, not
  wired to a live backend now.** Never scatter mode checks in components.
- **DataClient boundary** — the seam that makes "connect later" cheap. All pages depend on the
  interface, never on fixtures/endpoints/PromQL:
  `getCapabilities, getOverview, getTopology, getTelemetry, getEvents, getFlows, getIncidents,
  getPredictions, getConversation, createConversation, sendMessage, submitFeedback`.
  - **`MockDataClient` (built now, default):** bundled fixtures, small deterministic delays, can
    simulate success/empty/stale/partial/error; never needs a backend.
  - **`HttpDataClient` (future, not built now):** a thin stub only — interface present so pages
    compile against it, but no live wiring, kept out of active milestones.
- **State:** React context + reducer (`state/`). No Redux, no custom design system.
- **Query catalog** (`data/queryCatalog.ts`): the single home for PromQL, populated now from the
  verified metric names so it is ready when api mode is built. Not executed in mock scope.
- **Provenance:** `DataSourceKind = mock|measured|simulated|modelled|ground_truth|prediction`;
  `SourceBadge` on every panel; **Demo data** marker whenever mock predictions/copilot are visible.
  In this scope everything is `mock` or (for incident labels) `ground_truth` from the sample data.

---

## 3. Pages (7 sections in Grafana)

Routes under `/a/mplslab-noccopilot-app`:

| # | Section | Route | Content |
|---|---|---|---|
| 1 | Network Overview | `/` | reporting/expected/degraded devices, total+degraded tunnels, active/recent incidents, highest mock risk, nearest time-to-impact, recent events, risk-ordered incident list, compact trends, global filters (time/POP/site-type/device/VRF/hub). |
| 2 | Interactive Topology | `/topology` | data-driven expandable graph; roles P/PE/CE-branch/hub/dc/host; link+tunnel state; search/filter; selected-node drawer (neighbors, VRFs, related incidents/predictions, recent evidence). |
| 3 | **Node Details** | `/node/:id` | opens **in Grafana** on node click; preserves time range + filters; that node's health/freshness, interfaces+throughput, errors/discards/queues, CPU/mem, BGP/OSPF/LDP/RIB/MPLS, tunnels (latency/jitter/loss/rekeys), env+optical, events/flows/incidents/predictions, neighbor links. **"Open raw dashboard"** → provisioned `$device` dashboard. |
| 4 | Telemetry Explorer | `/telemetry` | detailed metric charts; device/interface/tunnel/VRF/POP/time filters; ground-truth fault overlays; measured/simulated/modelled/mock labels. |
| 5 | Incidents & Predictions | `/incidents` | active+historical; predicted fault, confidence, time-to-impact; affected scope; timeline; evidence; root-cause hypotheses; recommended actions; provenance. Answers: what may fail / when / why / what's affected / what to do. |
| 6 | Copilot | `/copilot` | context-aware mock chat; conversation create/history; message state machine; evidence/actions/citations separated from prose; retry/error; Demo disclosure. |
| 7 | Data & Integration Status | `/status` | mode indicator (mock), dataset coverage + provenance, data freshness. |

Nav flow: **Overview → Topology → (click node) Node Details → Incident / Telemetry / Copilot.**

---

## 4. Data-driven expandable topology (design)

**Topology is data, never code.** Adding nodes / links / roles later = updated fixture (or, in the
future api mode, updated `/topology`) — zero UI change.

- `TopologyGraph { nodes: TopologyNode[]; links: TopologyLink[] }`. `TopologyNode.role` is a
  **string, not a locked enum** — unknown roles render via a fallback style.
- **Role→style registry** (`data/topologyStyles.ts`): shape/color/icon per role + a default.
  New role → renders with default until a style row is added. No component rewrite.
- **No hardcoded counts.** Node/link/POP counts derive from array length. The 148/70/6 numbers
  live only in fixtures + docs.
- **Clustering / progressive rendering:** nodes carry optional `pop` + `parent`. POPs render as
  compound/cluster nodes (collapse → one bubble, expand → its P/PE). Hosts collapse under their
  CE until expanded. Keeps 148→N usable.
- **Library:** `@grafana/ui` has no embeddable interactive graph. Per prompt allowance, add **one**
  graph dep — **cytoscape.js** (pan/zoom, compound nodes, performant at hundreds of nodes,
  offline-installable, MIT), wrapped in `TopologyGraph.tsx` so it is swappable. Only non-Grafana
  runtime dep. Pinned + lockfile.

---

## 5. Node Details in Grafana (design — decided: page + dashboard)

Click node → two views of one node, time range + filters preserved:

- **Primary — app page** `/a/mplslab-noccopilot-app/node/:id`: renders all panels via **DataClient**
  (mock now). Works with **no datasource**. When api mode is built later the same page hits
  `/metrics` etc. — page code unchanged.
- **Secondary — provisioned dashboard** `frontend/grafana/dashboards/node-detail.json` with a
  `$device` template var; a drawer button deep-links `/d/node-detail?var-device=<id>`. Native
  Grafana panels. Needs a live datasource, so it is the **future** api-mode bonus — shipped as the
  JSON file now, exercised only once api mode exists.
- Metric panels use `@grafana/ui` timeseries; queries come from the query catalog, never inline.

---

## 6. Domain types (frontend-owned, `data/types.ts`)

`Filters, TimeRange, Capabilities, Overview, MetricPoint, MetricSeries, TopologyNode,
TopologyLink, TopologyGraph, NetworkEvent, FlowRecord, Incident, Prediction, Evidence,
RecommendedAction, Citation, Conversation, CopilotMessage, CopilotResponse, ApiError,
DataSourceKind`.

- Incident labels from the sample data → `Incident { source:'ground_truth', status:'resolved' }`.
  Never present ground truth as prediction.
- Unknown enums (status/severity/site_type/role) fall back to `unknown`, never crash.
- `ApiError` defined now (for the future api mode), unused in mock scope.

---

## 7. Fixtures (`scripts/generate_fixtures.py`)

Reads committed Parquet → compact deterministic JSON. Selects representative devices/entities,
builds chart-ready series + incident summaries grouped by `scenario_id`, preserves ts / device /
entity_type / VRF / fault_type / severity / lead_time / concurrency, **provenance on every fixture**,
no million-row copies, deterministic on rerun. Topology fixture synthesized from
`topology-spec.yaml` knobs.

Required scenarios: healthy, congestion precursor, BGP flap, tunnel degradation, policy drift,
core/POP failure, concurrent faults, empty source, stale source, partial-source failure, copilot
success, copilot timeout/error+retry. Every fabricated prediction + copilot reply → `source:'mock'`
+ Demo marker.

---

## 8. Copilot contract (mock only, this scope)

Non-streaming only. Message state: `draft → sending → complete`, `sending → error → retry →
sending`. Client-generated user-message IDs, duplicate-send guard, context
(deviceIds/incidentId/timeRange) preserved, user message preserved on failure + retry action,
evidence/actions rendered separately from prose, never fake citations to real docs, all mock
messages marked Demo. `CopilotResponse { summary, predictedIssue?, confidence?,
timeToImpactSeconds?, affectedScope, evidence, rootCauseHypotheses, recommendedActions, citations,
disclaimer? }`. Mock replies deterministic from selected scenario/device + intent match. The
proposed copilot HTTP endpoints are documented in `API_CONTRACT.md` as **proposed/future**;
`MockDataClient` implements them locally.

---

## 9. Failure-state handling (simulated by the mock client)

The mock client can drive every state so the UI is proven without a backend: empty source, stale
metrics (show last timestamp, **never green**), partial-source failure, source-unavailable banners,
copilot timeout+retry, "No ground-truth labels in this window". **Missing data never becomes a
healthy zero.** (Real per-source live failure belongs to the future api mode.)

---

## 10. Docker Compose (folder-local, air-gap, mock)

`grafana/grafana:11.1.0`, mount `plugin/dist/` into Grafana plugins dir, mount folder-local
provisioning + dashboards, `GF_PLUGINS_ALLOW_LOADING_UNSIGNED_PLUGINS=mplslab-noccopilot-app`,
disable external analytics/update checks, port 3000, **no lab, no Data API, no proxy** — mock mode
needs nothing else. No Grafana binaries, no `node_modules`, no secrets in git. (The api-mode proxy
service is deferred with api mode.)

---

## 11. Tests (`plugin/tests/`)

Unit: numeric-string parse, UTC/epoch convert, unknown-enum fallback, fixture/schema validity,
mock client behavior, filter behavior, partial-source render, empty/stale/error states, incident
risk ordering, topology node selection, copilot send/success/error/retry, context preservation,
demo-data disclosure. (Prometheus vector/matrix mapping + `{detail}` normalization tests are
written alongside the mapper code but the mapper is exercised by fixtures now; live parsing tests
land with api mode.)

Integration (one flow): Overview (mock) → high-risk device → its incident → copilot → "why at
risk?" → evidence + actions → retry a simulated failure → Demo markers persist.

Gates: lint, typecheck, unit, build, Compose startup, plugin-load, visual QA @1920×1080 + 1366×768.

---

## 12. Milestones (each ends with a user check-in per CLAUDE.md)

- **M1 Foundation — DONE.** Scaffolded plugin, folder-local mock Compose, nav + 7 routes,
  `types.ts`, `DataClient` interface, `config.ts` (mode defaults `mock`). Gate met: plugin loads,
  empty pages render.
- **M2 Fixtures + mock — DONE.** `frontend/scripts/generate_fixtures.py` (pandas+pyarrow, reuses
  `dataapi/export.py` helpers) generates `frontend/plugin/src/fixtures/*.json`: composite playback
  tape bucketCount=152, deviceIds=70, incidents=28, predictions=69, faultTypes=21 (all covered),
  topologyNodes=148, topologyLinks=361, windowBuckets=50 (trailing sliding window). Fabricated
  predictions mirror real labelled faults + confidence ramp 0.6→0.95 + 1 seeded late call + 1
  false-alarm, ordered calm→incidents→calm for a seamless loop. `MockDataClient.ts` implements
  `DataClient` over the fixtures, cursor-aware (`setCursor(n)`); `DataClientContext.tsx` selects it
  when `appConfig.mode==='mock'`. Global demo clock (`state/AppContext.tsx`, `state/reducer.ts`):
  autoplay + loop, TICK/PLAY/PAUSE/SEEK/SET_SPEED/SET_BOUNDS, no `Date.now` (deterministic).
  `PlaybackControls.tsx`, `AppShell.tsx`, `MetricCard.tsx` built; `SourceBadge.tsx` exists but
  intentionally not rendered (no visible demo markers). OverviewPage live: 6 MetricCards + incident
  list recompute every clock tick. Plugin builds green (webpack prod, dist/module.js 2.24 MiB),
  `tsc --noEmit` clean, plugin.json version 1.0.2, serves in Grafana 11.1.0 at
  `/a/mplslab-noccopilot-app`. **Gate met:** rerun of `generate_fixtures.py` is byte-identical
  (sha256 match); synced fabric animates (degraded devices + active incidents vary per bucket
  across 90/152 buckets).
- **M3 Operator UI + topology/node-details — DONE.** Topology page: live cytoscape map
  (cytoscape@3.30.2, offline/pinned), POP clustering via compound nodes, role→style registry with
  default fallback (expandable — new fixture node, zero code change), node search, health legend,
  per-cursor red/amber/green coloring without re-layout, click node → Node Detail. Node Detail page
  `/node/:id`: header (role/POP/siteType), telemetry charts, neighbor links, active
  incident/prediction health line; router nav preserves the demo clock. Telemetry Explorer: device
  + metric multi-select, charts grouped by family, fault-overlay bands, honesty caption for 0
  interface-error counters. Incidents & Predictions: risk-ordered table (active>open>resolved,
  severity, soonest time-to-impact), predictions strip, detail drawer, load error renders as error
  not empty/green. Data & Integration Status page: feeds/dataset window/ingest stats. Global
  FilterBar (POP + device) in nav, reducer-held, all pages refetch on cursor/filter change. Charts
  are native SVG (`TimeSeriesPanel`), no charting dep. Shared `EmptyState`/`ErrorState`,
  `constants.ts`. Build green, `tsc --noEmit` exit 0, plugin.json 1.0.3, serves in Grafana 11.1.0.
  **Gate met:** click node → Node Detail w/ preserved demo clock ✓; topology data-driven with
  default role style, new fixture node needs no code change ✓.
- **M4 Copilot UI (mock) — DONE.** `CopilotPage.tsx` + `CopilotChat.tsx` live (was a stub). Message
  state machine draft→sending→complete, sending→error→retry; client-generated message ids via a
  counter ref (no `Date.now`/`Math.random`); retry reuses the same id; send disabled while
  in-flight. Context = top live incident at the current cursor (active>open, then severity),
  passed as `context` to `sendMessage`, kept in sync with the demo clock; banner shows the live
  incident or "Network nominal". All 21 fault types have a seeded reply (fixture conversations)
  with citations, evidence, root-cause hypotheses, recommended actions, rendered as a structured
  card separate from prose; citations point only to existing `ragcorpus/runbook-tunnel-latency-high.md`,
  `runbook-bgp-adjacency-down.md`, `topology-map.md`, `incident-template.md`. Suggested-question
  buttons seed the first turn. **Not added:** the plan's optional 3 new ragcorpus runbook stubs
  (congestion/node-failure/policy-drift) — fixtures cite only the 4 existing docs, new stubs would
  be unreferenced (YAGNI). Build green, `tsc --noEmit` exit 0, plugin.json 1.0.5, serves in Grafana
  11.1.0. **Gate met:** send/success/error/retry.
- **M5 QA + handoff — next.** Tests, build, Compose startup, visual QA, doc sync, air-gap review,
  acceptance checklist.

**Future (NOT in this scope):** api mode — build `HttpDataClient`, wire the 6 real `dataapi`
endpoints via a Compose-service proxy, execute the query catalog, per-source live failure, live
copilot endpoints. Kept unblocked by the DataClient seam above.

---

## 13. Documentation deliverables

`frontend/README.md` (build/run mock, config, fixture regen, troubleshooting, folder-sharing),
`frontend/API_CONTRACT.md` (real endpoints as the **future** api-mode target + proposed copilot
endpoints, all marked not-yet-wired), `frontend/INTEGRATION_GUIDE.md` (actual plugin structure,
config keys, mock networking, handoff, and how to add api mode later). Repo docs per `CLAUDE.md`:
`PLAN.md` (Phase 7), `HANDOFF.md` (frontend entry). No unverified behavior documented as working.

---

## 14. Risks / accepted constraints

- cytoscape.js = one added runtime dep (justified, wrapped, pinned, offline).
- api mode + live backend are explicitly out of scope now; the DataClient interface + query catalog
  + `node-detail.json` are the only forward-compatibility investments, all cheap.
- Everything visible is mock/ground-truth-sample; no live telemetry, no real predictions.
