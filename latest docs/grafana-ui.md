# Grafana UI plugin

## Purpose

A Grafana 11.1 **app plugin** (`mplslab-noccopilot-app`) that is the operator-facing UI for the air-gapped predictive-NOC copilot. It runs entirely client-side inside Grafana's iframe-free app-plugin shell: React Router pages read topology/telemetry/incidents/predictions from a `DataClient` and render them (cytoscape topology map, hand-rolled SVG time-series charts, incident/flow/log tables), plus a streaming chat surface that talks to the separate copilot `/chat` service. It sits at the very end of the pipeline — dataapi (FastAPI, `:8000`) and the copilot service (`:8100`) are the two backends this plugin's `HttpDataClient` calls; Grafana itself only proxies read-only Alertmanager queries for the native Alerting tab. There is currently exactly one `DataClient` wired in (`HttpDataClient`); a second implementation (`MockDataClient`) exists on disk but is not reachable from the app (see Gotchas).

## Entry points

Not a service — no CLI, no FastAPI routes. Entry points are the plugin's build/dev commands and the routes Grafana mounts once the plugin loads.

- **Plugin registration**: `grafana ui/plugin/src/module.tsx:4` — `AppPlugin<{}>().setRootPage(App)`. Grafana's plugin loader calls this once per page navigation into `/a/mplslab-noccopilot-app/*`.
- **Dev build** (watch mode, webpack): from `grafana ui/plugin/`, `yarn dev` → `grafana ui/plugin/package.json:7`.
- **Production build**: `yarn build` → `grafana ui/plugin/package.json:6`, outputs to `plugin/dist/`.
- **Typecheck**: `yarn typecheck` (`tsc --noEmit`) → `grafana ui/plugin/package.json:11`.
- **Unit tests**: `yarn test:ci` (jest, 4 workers, `--passWithNoTests`) → `grafana ui/plugin/package.json:9`; `yarn test` watches only-changed files.
- **Run the stack** (pre-built `dist/`, mock-mode compose comments are stale — see Gotchas): from `grafana ui/`, `docker compose up` → `grafana ui/docker-compose.yml:1`. Brings up Grafana on `:3000` and Alertmanager on `127.0.0.1:9093`.
- **Run the stack** (build-from-source compose, separate from the above): from `grafana ui/plugin/`, `yarn server` (`docker-compose up --build`) → `grafana ui/plugin/package.json:14`, `grafana ui/plugin/docker-compose.yaml:1`.
- **App routes** (registered in `grafana ui/plugin/src/plugin.json:20-85`, rendered by `grafana ui/plugin/src/App.tsx:50-58`): all under base path `/a/mplslab-noccopilot-app` (`grafana ui/plugin/src/constants.ts:3`).
  | Path | Page component |
  |---|---|
  | `/a/mplslab-noccopilot-app/` | `OverviewPage` |
  | `.../topology` | `TopologyPage` |
  | `.../node/:id` (e.g. `.../node/pe1`) | `NodeDetailPage` |
  | `.../telemetry` | `TelemetryPage` |
  | `.../incidents` | `IncidentsPage` |
  | `.../copilot` | `CopilotPage` |
  | `.../inject` | `FaultInjectionPage` |
  | `.../status` | `StatusPage` |
  Example: with Grafana up on `:3000`, open `http://localhost:3000/a/mplslab-noccopilot-app/topology`.

## Modules

### App shell / bootstrap
- `src/module.tsx` — plugin entry, registers `App` as the root page.
- `src/App.tsx` — provider nesting (`AppProvider` → `DataClientProvider` → `AlertToasterProvider` → `CopilotChatProvider`) and the `<Switch>` of 8 routes; `App.tsx:24-37`, `App.tsx:39-62`.
- `src/plugin.json` — Grafana app-plugin manifest: id, nav pages, `grafanaDependency: ">=11.1.0"` (`plugin.json:87`).
- `src/config.ts` — `AppConfig` singleton `appConfig` (hardcoded backend URLs/timeouts, not read from Grafana provisioning — see Gotchas); `config.ts:8-14`.
- `src/constants.ts` — `APP_BASE`, `nodeDetailPath(id)`, `copilotPath`; `constants.ts:3-7`.

### Data layer (`src/data/`)
- `types.ts` — every domain type shared across UI/state/data-client: `Filters`, `Overview`, `TopologyNode(Live)`, `Incident`, `Prediction`, `MetricSeries`, `MetricDescriptor`, `ChatEvent` union (7 event types) + `CopilotTurn`, `FaultScenario`/`ActiveFault`/`ForensicCase`/`InjectFaultRequest`. No runtime code.
- `DataClient.ts` — the interface every client implements: `getCapabilities/getOverview/getTopology/getTelemetry/getEvents/getFlows/getIncidents/getPredictions/chat`; `DataClient.ts:17-30`.
- `DataClientContext.tsx` — `DataClientProvider`/`useDataClient()`; always constructs `HttpDataClient` — `DataClientContext.tsx:8-17` ("No mock path exists").
- `HttpDataClient.ts` — **the live client**, backed by dataapi (`:8000`) + copilot (`:8100`). Key methods: `getTopology` (derives pop/parent + red/amber/yellow health state, `HttpDataClient.ts:263-374`), `getTelemetry` (one batched `POST /metrics/batch`, `HttpDataClient.ts:378-414`), `getEvents`/`getFlows` (`/events`, `/flows`), `getIncidents`/`getPredictions` (derived from `/labels`, `HttpDataClient.ts:453-525`), `getOverview` (`HttpDataClient.ts:529-569`), `getCapabilities` (probes VM/events/flows, `HttpDataClient.ts:573-601`), `chat` (SSE stream over fetch+ReadableStream, `HttpDataClient.ts:609-682`), plus fault-injection extras (`getScenarios/injectFault/getActiveFaults/getCases/revertFault`, `HttpDataClient.ts:686-703`) that are outside the shared `DataClient` interface and accessed via an unsafe cast (`FaultInjectionPage.tsx:66`).
- `MockDataClient.ts` — **dead code** (untracked, unreferenced, does not compile — see Gotchas). Fixture-replay implementation of `DataClient` plus mock-only extras (`setCursor`, `getActiveAlerts`).
- `errors.ts` — `makeApiError`, `normalizeError(e): ApiError` — turns a fetch rejection or FastAPI `{detail}` body into the shared `ApiError` shape; `errors.ts:8-46`.
- `metricCatalog.ts` — `METRIC_CATALOG: MetricDescriptor[]`, 32 entries, single source of truth for every PromQL query `HttpDataClient.getTelemetry` issues (name/promql template/label/unit/source/group/entityLabel); `metricCatalog.ts:15-69`. Helpers `metricInfoForName`, `catalogGroupFor`, `METRIC_GROUPS`.
- `telemetrySynth.ts` — deterministic synthetic telemetry generator for `MockDataClient` (dead-code path). `synthSeries(deviceId, role, bucketCount)`, `tunnelStressAt(...)`; pure hash-based PRNG (`hash`, `unit01`), no `Math.random`.
- `topologyStyles.ts` — role→shape/color/size registry (`roleStyles`, `styleForRole`), health-state colors (`stateColors`, `colorForState`), and the **live** stress thresholds `STRESS_LATENCY_MS`/`STRESS_JITTER_MS`/`STRESS_LOSS_PCT` consumed by `HttpDataClient.getTopology`; `topologyStyles.ts:11-53`.
- `copilotChat.ts` — pure SSE helpers: `parseSseFrames(buffer)` (incremental frame splitter) and `mapEventsToTurn(events)` (folds a `ChatEvent[]` trace into one `CopilotTurn`, extracting `[source:offset]` citations); `copilotChat.ts:14-63`.
- `faultTargets.ts` — pure target-selection helpers for the Fault Injection page: `roleOf(target)` (mirrors backend `dataapi/faults_api.py:_role_of`), `isValidTarget`, `targetOptions`; `faultTargets.ts:16-73`.

### State (`src/state/`)
- `reducer.ts` — pure `appReducer(state, action)` over `AppState { mode, range, liveWindowSec, refreshTick, filters }`. Actions: `TICK`, `REFRESH`, `SET_MODE`, `SET_RANGE`, `SET_LIVE_WINDOW`, `SET_FILTER`, `CLEAR_FILTERS`; `reducer.ts:58-120`.
- `AppContext.tsx` — `AppProvider` wraps `useReducer`, dispatches `TICK{nowMs: Date.now()}` on mount and every `LIVE_REFRESH_MS` while `mode==='live'`; exports `LIVE_REFRESH_MS`, `useAppState`, `useAppDispatch`; `AppContext.tsx:17`, `:23-41`.

### Hooks (`src/hooks/`)
- `useCopilotChat.ts` — the chat driver: per-turn state machine (`sending|done|error|aborted`), session id persisted in `localStorage['noc.copilot.session']`, thread persisted in `localStorage['noc.copilot.thread']` (artifact bytes stripped before persist), abort/retry/new-chat; `useCopilotChat.ts:89-216`.
- `CopilotChatContext.tsx` — mounts `useCopilotChat()` **once** and shares it via context so the `/copilot` tab and the global side panel are one conversation; `CopilotChatContext.tsx:10-21`.

### Utils (`src/utils/`)
- `time.ts` — `formatUtc`, `bucketToTsMs`, `slidingWindow`/`windowIndices` (mock-tape trailing-window math, dead-code path), `secondsToMs`/`msToSeconds`.
- `format.ts` — deterministic unit formatters: `bps`, `ms`, `pct`, `bytes`, `count`, `secondsToEta`.
- `metricGroups.ts` — `groupSeries(series)`: buckets a flat `MetricSeries[]` into per-panel groups keyed by catalog group/label, drops all-zero entity series, orders panels via `PANEL_ORDER`; `metricGroups.ts:9-62`.
- `source.ts` — `DataSourceKind` → label/color maps (`SOURCE_LABELS`, `SOURCE_COLORS`), used only by the unmounted `SourceBadge`.
- `topologyLayout.ts` — `computePositions(nodes)`: deterministic grid+tier layout (3-wide POP grid, role tiers, row-wrapping) so the cytoscape graph never overlaps and never re-shuffles; `topologyLayout.ts:47-88`.

### Components (`src/components/`)
- `AppShell.tsx` — top bar (title, `LabStatusBadge`, `TimeControl`, Copilot toggle button) + nav links + `FilterBar`; `AppShell.tsx:12-21`, `:24-61`.
- `CopilotPanel.tsx` — global side `Drawer` wrapping `CopilotChat`, toggled from `AppShell`.
- `CopilotChat.tsx` — thread + composer UI over `useSharedCopilotChat()`; workspace toggle defaults OFF (read-only investigation); `CopilotChat.tsx:11-25`.
- `CopilotTrace.tsx` — collapsible per-event trace cards (think/tool_call+result/gate), citation chips with hover-preview + click-to-scroll, and `ArtifactView` (raster images inline via client-typed blob URL; anything else is a download-only chip, SVG/HTML never rendered — XSS guard); `CopilotTrace.tsx:11-17`, `:169-204`.
- `PaAlertsBanner.tsx` — polls `GET {apiBaseUrl}/pa/alerts` every `POLL_MS=10000`, toasts on newly-seen `entity_id`; `PaAlertsBanner.tsx:34`, `:47-86`.
- `AlertToaster.tsx` — `AlertToasterProvider`/`useToaster()`, fixed top-right toast stack, `AUTO_DISMISS_MS=5000`; `AlertToaster.tsx:24`.
- `MetricCard.tsx` — labeled stat tile with `tone` (default/warning/error) coloring.
- `SourceBadge.tsx` — provenance pill (never mounted anywhere — deliberate, see comment `SourceBadge.tsx:8-10`).
- `FilterBar.tsx` — global POP/device `Select`s wired to `dispatch(SET_FILTER)`; options derived from one `getTopology({})` call.
- `LabStatusBadge.tsx` — polls `getCapabilities()` every `POLL_MS=10000`; hysteresis `MISS_LIMIT=2` misses before flipping to OFF, any success flips to ON immediately; `LabStatusBadge.tsx:10`, `:22-27`.
- `TimeControl.tsx` — Live/History `RadioButtonGroup` + range picker; `RANGES` presets 5m..30d; dispatches `SET_MODE`/`SET_RANGE`/`SET_LIVE_WINDOW`/`REFRESH`; `TimeControl.tsx:12-25`.
- `EmptyState.tsx` / `ErrorState.tsx` — neutral vs. explicit-failure placeholder states (error is never rendered green, per plan "stale-not-green").
- `TopologyGraph.tsx` — cytoscape wrapper. `buildElements` (nodes/edges + synthetic POP compound-parent nodes), `applyLayout` (bakes `computePositions` output onto compound children, `cy.fit`), hover→`edge-hl` class + mini-card callback, click→`onSelectNode`; `TopologyGraph.tsx:25-60`, `:69-82`, `:192-200`.
- `TimeSeriesPanel.tsx` — hand-rolled SVG multi-series line chart (no charting lib): linear `px`/`py` mappers, null-gap-aware path segmentation, optional fault-overlay `<rect>` bands; fixed `width=640`; `TimeSeriesPanel.tsx:25`, `:33-53`, `:67-84`.
- `IncidentTable.tsx` — prediction strip + sortable incident table; `sortIncidents` ranks by `STATUS_RANK` (active<open<resolved<unknown) then `SEVERITY_RANK` (high<medium<low<unknown) then ascending TTI; `IncidentTable.tsx:18-19`, `:27-37`.
- `IncidentDetail.tsx` — `Drawer` with affected scope / evidence / root-cause / recommended-actions.
- `InterfaceTable.tsx` — pivots interface-scoped `MetricSeries` (`<device>:<iface>:<metric>` keys) into one row per interface; `lastValue` walks a series backward for the last non-null point; `InterfaceTable.tsx:12-23`, `:59-94`.
- `FlowTable.tsx` — flow list + total bytes/packets/top-talker stat cards; `MAX_ROWS=200`, `VISIBLE_ROWS=20`.
- `LogTerminal.tsx` — syslog-style scrolling terminal; `cleanLine` strips RFC5424 structured-data prefix through `" - - - "`; severity bucketing (`error|warning|info|debug`); auto-scroll only when already near bottom (`<48px`); `LogTerminal.tsx:26-30`, `:32-44`, `:76-79`.

### Pages (`src/pages/`)
- `OverviewPage.tsx` — 6 `MetricCard`s from `getOverview` + open/active incident list; refetches on `refreshTick`+`filters`.
- `TopologyPage.tsx` — search box, health legend, `TopologyGraph`, hover mini-card (`NodeHoverCard`) that fetches one `getTelemetry` snapshot per hovered device.
- `NodeDetailPage.tsx` — per-device drilldown: telemetry panels (`groupSeries`), `InterfaceTable`, neighbor links (from topology adjacency), `FlowTable`, `LogTerminal`; live search filters visible sections; events window widened to 60m in live mode so router boot bursts don't scroll out of the 900s metric window (`NodeDetailPage.tsx:60-63`).
- `TelemetryPage.tsx` — device + multi-metric-key picker, grouped `TimeSeriesPanel`s with fault-overlay bands built from `getIncidents`.
- `IncidentsPage.tsx` — thin wrapper over `IncidentTable`/`IncidentDetail`.
- `CopilotPage.tsx` — thin wrapper over `CopilotChat` (shared hook, same thread as the side panel).
- `StatusPage.tsx` — maps `Capabilities.sources` keys to product-facing feed names via `FEED_LABELS` (mock kind intentionally never surfaced); ingest stat tiles from `getOverview`.
- `FaultInjectionPage.tsx` — scenario/target/severity/duration form → `POST /faults/inject`; active-fault registry polled every `LIVE_REFRESH_MS` with a `mutSeq` guard against stale-response resurrection after an optimistic revert (`FaultInjectionPage.tsx:104-120`, `:190-199`); `phaseView` derives a live countdown label per phase (buildup/impact/reverting) from cached ISO timestamps + a local 1s tick (no network); forensic-case list links into the copilot tab.

### Alerting (`src/alerting/`)
- `alertPublisher.ts` — `buildAmAlerts` (pure descriptor→Alertmanager-v2-payload mapper) and `publishAlerts` (POSTs to `{protocol}//{hostname}:9093/api/v2/alerts`, swallows failures); **exported and unit-tested but never called from the app** (see Gotchas); `alertPublisher.ts:29-71`.

### Fixtures (`src/fixtures/`)
Static JSON consumed only by `MockDataClient` (dead code — see Gotchas). Contract documented in `src/fixtures/README.md`; not duplicated in prose here beyond the Config & schemas section below, per instructions to document code, not restate docs.

## Parameters

| name | default | env-var/flag | units | what it controls | source |
|---|---|---|---|---|---|
| `apiBaseUrl` | `http://127.0.0.1:8000` | none (hardcoded) | URL | dataapi base for topology/metrics/events/flows/labels/faults | `grafana ui/plugin/src/config.ts:9` |
| `requestTimeoutMs` | `8000` | none | ms | abort timeout for every dataapi fetch/POST | `grafana ui/plugin/src/config.ts:10` |
| `copilotBaseUrl` | `http://127.0.0.1:8100` | none | URL | copilot `/chat` and `/cases` base | `grafana ui/plugin/src/config.ts:12` |
| `copilotTimeoutMs` | `180000` | none | ms | abort timeout for a chat turn (investigations can run ~3min) | `grafana ui/plugin/src/config.ts:13` |
| `LIVE_REFRESH_MS` | `30000` | none | ms | live-mode auto-refresh cadence (`TICK` interval); also `FaultInjectionPage`'s active-fault poll | `grafana ui/plugin/src/state/AppContext.tsx:17` |
| `labelsCache` TTL | `2000` | none | ms | dedupe window for concurrent `/labels` fetches (per device key) | `grafana ui/plugin/src/data/HttpDataClient.ts:243` |
| telemetry query `step` | `30` | none | s | PromQL step for `/metrics/batch` | `grafana ui/plugin/src/data/HttpDataClient.ts:387` |
| default telemetry window | `toMs - 15*60*1000` | none | ms | fallback `fromMs` when caller passes no `timeRange` | `grafana ui/plugin/src/data/HttpDataClient.ts:384` |
| `STRESS_LATENCY_MS` | `80` | none | ms | live tunnel-latency yellow-state base threshold | `grafana ui/plugin/src/data/topologyStyles.ts:51` |
| `STRESS_JITTER_MS` | `50` | none | ms | live tunnel-jitter yellow-state base threshold | `grafana ui/plugin/src/data/topologyStyles.ts:52` |
| `STRESS_LOSS_PCT` | `1.2` | none | % | live tunnel-loss yellow-state base threshold | `grafana ui/plugin/src/data/topologyStyles.ts:53` |
| degree multiplier | `1 + degree/5` | none | ratio | scales the 3 STRESS_* thresholds up per node connection count | `grafana ui/plugin/src/data/HttpDataClient.ts:309` |
| `DEFAULT_LIVE_WINDOW_SEC` | `900` (15m) | none | s | initial/live window length | `grafana ui/plugin/src/state/reducer.ts:29` |
| min live window | `30` | none | s | floor clamp on `SET_LIVE_WINDOW` | `grafana ui/plugin/src/state/reducer.ts:98` |
| `AUTO_DISMISS_MS` | `5000` | none | ms | toast lifetime | `grafana ui/plugin/src/components/AlertToaster.tsx:24` |
| `POLL_MS` (lab status) | `10000` | none | ms | `getCapabilities` poll interval | `grafana ui/plugin/src/components/LabStatusBadge.tsx:10` |
| `MISS_LIMIT` | `2` | none | polls | consecutive misses before Lab badge flips OFF | `grafana ui/plugin/src/components/LabStatusBadge.tsx:22` |
| `POLL_MS` (PA banner) | `10000` | none | ms | `/pa/alerts` poll interval | `grafana ui/plugin/src/components/PaAlertsBanner.tsx:34` |
| PA prediction alert window | `remainingSec ∈ (0,300]` | none | s | mock-only T-minus threshold for `NodeDownPredicted` (dead code path) | `grafana ui/plugin/src/data/MockDataClient.ts:463` |
| `MAX_ROWS` (flows) | `200` | none | rows | flow-table row cap | `grafana ui/plugin/src/components/FlowTable.tsx:13` |
| `VISIBLE_ROWS` (flows) | `20` | none | rows | flow-table visible-height sizing | `grafana ui/plugin/src/components/FlowTable.tsx:14` |
| `VISIBLE_ROWS` (log) | `20` | none | rows | log-terminal visible-height sizing | `grafana ui/plugin/src/components/LogTerminal.tsx:14` |
| `DEFAULT_DURATION` | `90` | none | s | default fault-injection duration before scenario catalog loads | `grafana ui/plugin/src/pages/FaultInjectionPage.tsx:20` |
| duration presets | `[15,30,60,90,180,300,600]` | none | s | fault-injection duration `Select` options | `grafana ui/plugin/src/pages/FaultInjectionPage.tsx:241` |
| chart width | `640` | none | px | fixed SVG viewBox width for `TimeSeriesPanel` | `grafana ui/plugin/src/components/TimeSeriesPanel.tsx:31` |
| chart height | `200` | none | px | default `TimeSeriesPanel` height (prop-overridable) | `grafana ui/plugin/src/components/TimeSeriesPanel.tsx:28` |
| `CLUSTER_W`/`CLUSTER_H` | `520`/`460` | none | px | topology layout POP-cluster cell size | `grafana ui/plugin/src/utils/topologyLayout.ts:15-16` |
| `CLUSTER_COLS` | `3` | none | count | POPs per row in the topology grid | `grafana ui/plugin/src/utils/topologyLayout.ts:17` |
| `MAX_PER_ROW` | `12` | none | nodes | wrap threshold within one role tier | `grafana ui/plugin/src/utils/topologyLayout.ts:21` |
| Grafana port | `3000` | `docker-compose.yml` ports | port | Grafana web UI | `grafana ui/docker-compose.yml:16` |
| Alertmanager port | `127.0.0.1:9093` | `docker-compose.yml` ports | port | native Alerting datasource + browser POST target | `grafana ui/docker-compose.yml:47` |
| `GF_PLUGINS_ALLOW_LOADING_UNSIGNED_PLUGINS` | `mplslab-noccopilot-app` | env var | — | allow-lists this unsigned plugin id to load | `grafana ui/docker-compose.yml:23` |
| `GF_AUTH_ANONYMOUS_ORG_ROLE` | `Admin` | env var | — | anonymous-viewer role (air-gapped lab, no login) | `grafana ui/docker-compose.yml:30` |
| Grafana version | `11.1.0` | image tag / `grafanaDependency` | — | pinned Grafana image + plugin manifest floor | `grafana ui/docker-compose.yml:14`, `grafana ui/plugin/src/plugin.json:87` |

## Data flow

- **Topology**: `GET {apiBaseUrl}/topology` (raw nodes/links, no pop/parent/state) → `HttpDataClient.getTopology` joins in `GET /labels` (ground-truth fault windows → red/amber-candidate sets), 3× instant PromQL vectors (`sdwan_tunnel_{latency,jitter,loss}_ms/pct` → per-device stress) and derives `pop`/`parent` from physical adjacency (P/PE anchor on id pattern, CE→uplink PE, host→its CE) → `TopologyNodeLive[]` + `TopologyLink[]` → `TopologyGraph`/`TopologyPage`/`NodeDetailPage`/`FaultInjectionPage` (`HttpDataClient.ts:263-374`).
- **Telemetry**: `METRIC_CATALOG` (32 descriptors) → one `POST {apiBaseUrl}/metrics/batch` with all 32 PromQL queries (device id substituted for `$dev`) over `[fromMs,toMs]` at `step=30s` → per-metric `PromResp[]` → flattened into `MetricSeries[]` keyed `<device>[:<entity>]:<metricName>` → `groupSeries()` buckets into chart panels → `TimeSeriesPanel`/`InterfaceTable` on `NodeDetailPage`/`TelemetryPage`/`TopologyPage`'s hover card (`HttpDataClient.ts:378-414`, `metricGroups.ts:25-62`).
- **Events/flows**: `GET {apiBaseUrl}/events?device&start&end&limit=1000` / `GET {apiBaseUrl}/flows?...&limit=500` → raw rows (`ts` string parsed to `tsMs`, flows' space-separated no-tz timestamp is coerced to UTC by appending `Z`) → sorted newest-first → `LogTerminal`/`FlowTable` (`HttpDataClient.ts:418-449`).
- **Incidents/predictions**: both derive from the same `GET {apiBaseUrl}/labels[?device=]` ground-truth fault-window rows (cached 2s per device key) → `getIncidents` buckets each row into open/active/resolved by comparing `Date.now()` to `t_start`/`t_impact`/`t_end`; `getPredictions` keeps only pre-impact rows and computes a confidence ramp → `IncidentTable`/`IncidentDetail`/`OverviewPage`/`TelemetryPage` overlay bands (`HttpDataClient.ts:453-525`).
- **Overview**: fan-out of `/topology`, `/labels`, `getPredictions`, plus 3 instant PromQL scalars (`count(...)` for reporting devices/total tunnels/degraded tunnels) → `Overview` → `OverviewPage`/`StatusPage` (`HttpDataClient.ts:529-569`).
- **Capabilities**: 3 probes (`vector(1)` on `/metrics`, `/events?limit=1`, `/flows?limit=1`) — only the first (`vmOk`) actually gates `sources.{measured,simulated,modelled}`; `ground_truth`/`prediction` are hardcoded `true`, `mock` hardcoded `false` → `Capabilities` → `LabStatusBadge` (10s poll, 2-miss hysteresis) and `StatusPage` (`HttpDataClient.ts:573-601`).
- **Chat**: user types in `CopilotChat` → `useCopilotChat.send()` builds a `ChatRequest` (question, `sessionId` from `localStorage`, `workspace` toggle, optional `start`/`end` snapshot of the History window) → `HttpDataClient.chat` does `POST {copilotBaseUrl}/chat`, reads the response body as an SSE byte stream via `ReadableStream` reader + `parseSseFrames`, calls `onEvent` per decoded `ChatEvent` (patches thread state live) → on stream end, `mapEventsToTurn` folds the full event list into one `CopilotTurn` (last `assistant_msg` = answer, citations extracted, gate/tool_result maps built) → `CopilotTrace` renders it; thread persisted to `localStorage['noc.copilot.thread']` once no turn is `sending` (`useCopilotChat.ts:123-152`, `copilotChat.ts:41-63`, `HttpDataClient.ts:609-682`).
- **PA alerts**: `PaAlertsBanner` polls `GET {apiBaseUrl}/pa/alerts` every 10s (a dataapi endpoint that proxies the separate `pa_alerts` service scoring live topology through the graph-v2 model) → new `entity_id`s in `alerts[]` fire a toast via `AlertToaster`; cleared entities drop from the seen-set so a re-alert re-notifies (`PaAlertsBanner.tsx:47-86`).
- **Fault injection**: `FaultInjectionPage` reads `GET {apiBaseUrl}/faults/scenarios` (catalog + `valid_roles`) and topology (for target options via `faultTargets.targetOptions`) → operator picks scenario/target/severity/duration → `POST {apiBaseUrl}/faults/inject` → dataapi hands off to the orchestrator (`docker exec` into the lab) → page polls `GET {apiBaseUrl}/faults/active` every `LIVE_REFRESH_MS` for the true buildup→impact→reverting lifecycle, and `GET {copilotBaseUrl}/cases` for forensic cases the predictor/trigger pipeline opened (`FaultInjectionPage.tsx:82-120`).
- **Alertmanager (native Grafana Alerting)**: `alertPublisher.publishAlerts` would POST synthetic alerts straight to `{origin-host}:9093/api/v2/alerts` (bypassing Grafana's read-only AM proxy) — currently dead code, nothing calls it (see Gotchas); Grafana's own read path is the provisioned `Alertmanager` datasource (`grafana/provisioning/datasources/datasources.yaml:24-32`) feeding the native Alerting UI.

## Calculations

- **POP from device id** — `popOf(id)`: regex `^(pe|p)(\d+)$`; `per = 4` for P-role, `2` for PE-role; `pop = ceil(n/per)` → `pop{n}`. `HttpDataClient.ts:130-139`.
- **Node health state (live)** — precedence red > yellow(stress) > amber(candidate) > green:
  - `red`: device id has a ground-truth label row with `t_start ≤ now ≤ t_end` where `now ≥ t_impact` (i.e. mid-incident, past impact). `HttpDataClient.ts:284-291`.
  - `amberCandidate`: label row with `t_start ≤ now < t_impact` (buildup, not yet impacted) — only used as a fallback, never preempts yellow. `HttpDataClient.ts:288-290`.
  - `yellow` (stressed): `degree = physical+tunnel link count on that node`; `mult = 1 + degree/5`; true if `latency > STRESS_LATENCY_MS*mult` OR `jitter > STRESS_JITTER_MS*mult` OR `loss > STRESS_LOSS_PCT*mult`. `HttpDataClient.ts:298-318`, thresholds `topologyStyles.ts:51-53`.
- **Incident status (live)** — for a label row with `s=t_start, i=t_impact, e=t_end` (all epoch s): `status = open if now<i, active if now<e, else resolved`; rows with `now<s` are dropped entirely. `HttpDataClient.ts:465-471`.
- **Prediction confidence (live)** — only rows with `s ≤ now < i` are surfaced. `lead = lead_time_s if >0 else (i-s)`. `confidence = clamp((now-s)/lead, 0, 1)`. `timeToImpactSeconds = max(0, i-now)`. `HttpDataClient.ts:507-519`.
- **Overview `highestRisk`/`nearestTimeToImpactSeconds`** — over the current predictions list: `highestRisk = argmax(confidence)`; `nearestTimeToImpactSeconds = min(timeToImpactSeconds)`, or `null` if no active predictions. `HttpDataClient.ts:551-557` (live path); mirrored on the dead-code mock path at `MockDataClient.ts:547-556`.
- **`IncidentTable` sort key** — `(STATUS_RANK[status], SEVERITY_RANK[severity], timeToImpactSeconds ?? +Inf)` ascending, where `STATUS_RANK = {active:0, open:1, resolved:2, unknown:3}` and `SEVERITY_RANK = {high:0, medium:1, low:2, unknown:3}`. `IncidentTable.tsx:18-19, 27-37`.
- **Topology layout position** — for node `n` in POP `p` at grid index `pi`: `originX = (pi % 3) * 520`, `originY = floor(pi/3) * 460`; within its role tier `t` (`p=0, pe=1, ce_*=2, host=3`, unknown→2): `baseY = originY + 40 + t*92`; nodes wrap into rows of ≤12, each row evenly spread across the 520px cluster width (`x = originX + (col+1)/(countInRow+1) * 520`), rows stacked `+34px` apart. `topologyLayout.ts:58-83`.
- **`TimeSeriesPanel` coordinate mapping** — `px(t) = PAD.left + (t-xMin)/(xMax-xMin) * (width-PAD.left-PAD.right)`; `py(v) = PAD.top + (1 - (v-yMin)/(yMax-yMin)) * (height-PAD.top-PAD.bottom)`; if `yMin===yMax` the axis is padded ±1 to avoid a zero-span chart. `TimeSeriesPanel.tsx:40-51`.
- **`secondsToEta`** — `h=floor(s/3600), m=floor((s%3600)/60), sec=s%60`; renders only the non-zero-leading parts, e.g. `3725 → "1h 2m 5s"`, `45 → "45s"`. `format.ts:58-73`.
- **`humanRate`/`bps`/`bytes`** — repeated `/1000` (bps→Kbps→Mbps→Gbps[→Tbps]) or `/1024` (B→KiB..TiB) while `v≥1000/1024`, capped at the largest unit; 0 decimals at the base unit, 2 elsewhere. `format.ts:12-23, 39-50`; `InterfaceTable.humanRate` (`InterfaceTable.tsx:25-34`) and `FlowTable.humanBytes` (`FlowTable.tsx:17-27`) duplicate this independently (not shared with `utils/format.ts` — see Gotchas).
- **Mock-only sliding window** (dead-code path) — trailing window of `windowBuckets` samples ending at `cursor`, clamped at 0; once the tape has looped, wraps the window start into the tail so it always holds exactly `windowBuckets` samples (`slidingWindow`/`windowIndices`, `time.ts:50-90`).
- **Mock-only degree-scaled tunnel stress** (dead-code path) — same `mult = 1 + degree/5` shape as the live path but against separate mock thresholds `MOCK_STRESS_LATENCY_MS=70`, `MOCK_STRESS_JITTER_MS=10`, `MOCK_STRESS_LOSS_PCT=3` (intentionally not the same scale as `topologyStyles.STRESS_*`, sized above the synthetic series' own noise range). `telemetrySynth.ts:17-19, 139-153`.

## Config & schemas

- **`plugin/src/plugin.json`** — Grafana app manifest. `id: mplslab-noccopilot-app` (must match every docker-compose mount path and `GF_PLUGINS_ALLOW_LOADING_UNSIGNED_PLUGINS` value). `includes[]`: 8 page entries, each `{type:"page", name, path, role:"Viewer", addToNav, defaultNav}`. `dependencies.grafanaDependency: ">=11.1.0"`. `%VERSION%`/`%TODAY%`/`%PLUGIN_ID%` are webpack-time template substitutions (`plugin.json:1-90`).
- **`grafana ui/docker-compose.yml`** (root, pre-built dist) — 2 services: `grafana` (image `grafana/grafana:11.1.0`, bind-mounts `./plugin/dist` → `/var/lib/grafana/plugins/mplslab-noccopilot-app`, `./grafana/grafana.ini` read-only, `./grafana/provisioning`, `./grafana/dashboards`) and `alertmanager` (image `prom/alertmanager:v0.27.0`, config `./alertmanager/alertmanager.yml`, bound to `127.0.0.1:9093` only). `extra_hosts: host.docker.internal:host-gateway` so the browser-side `HttpDataClient` (which runs on the operator's host, not in a container) can be told to hit `host.docker.internal:8428/3100` if repointed at the provisioned datasources — but see Gotchas, nothing currently uses that hostname from the plugin code itself.
- **`grafana ui/plugin/docker-compose.yaml`** (build-from-source, separate stack) — builds `./plugin/.config`'s `Dockerfile` (`grafana_image` default `grafana-enterprise`, `grafana_version` default `11.1.0`), mounts `./dist` and `./provisioning`; no Alertmanager service here.
- **`grafana ui/grafana/grafana.ini`** — one override: `[navigation.app_sections] mplslab-noccopilot-app = monitoring` (lifts the app out of "Apps" into a top-level "Monitoring" nav section).
- **`grafana ui/grafana/provisioning/datasources/datasources.yaml`** — 3 datasources: `VictoriaMetrics` (uid `victoriametrics`, prometheus proxy, `http://host.docker.internal:8428`, `isDefault`, `httpMethod: POST`), `Loki` (uid `loki`, `http://host.docker.internal:3100`), `Alertmanager` (uid `noc-alertmanager`, `http://alertmanager:9093`, `handleGrafanaManagedAlerts: false`, `editable: false`). None of these are queried by the plugin's `HttpDataClient` — it talks to dataapi/copilot directly over plain `fetch`; these datasources exist only for Grafana's own native panels/Alerting UI.
- **`grafana ui/grafana/provisioning/dashboards/dashboards.yaml`** — one file-provider (`noc-dashboards`), path `/var/lib/grafana/dashboards`, `foldersFromFilesStructure: true`, 30s update interval.
- **`grafana ui/grafana/provisioning/plugins/apps.yaml`** — enables the app (`type: mplslab-noccopilot-app`, `disabled: false`) with no `jsonData`.
- **`grafana ui/plugin/provisioning/plugins/apps.yaml`** — a second, differently-shaped provisioning file (used only by the `plugin/docker-compose.yaml` stack): sets `jsonData.apiUrl: http://default-url.com` and `secureJsonData.apiKey: secret-key`. **Unread by the plugin code** — `App.tsx`'s root component receives `AppRootProps` but never destructures `meta.jsonData`; all backend URLs come from the hardcoded `appConfig` in `config.ts` instead (see Gotchas).
- **`grafana ui/alertmanager/alertmanager.yml`** — `route.receiver: blackhole` (only receiver, no email/webhook/slack — nothing leaves the air-gapped stack), `group_wait: 1s`, `group_interval: 5s`, `repeat_interval: 1h`.
- **Fixtures (`plugin/src/fixtures/*.json`)** — read only by the dead-code `MockDataClient`; schema fully specified in `plugin/src/fixtures/README.md` (not restated here — see that file). One line each on what's live from `meta.json` (verified by reading the file directly): `bucketMs=30000`, `bucketCount=152`, `windowBuckets=50`, `deviceIds.length=70`, `telemetryDeviceIds.length=27`.
- **`localStorage` keys** (browser, not a file) — `noc.copilot.session` (session id string, `useCopilotChat.ts:26`) and `noc.copilot.thread` (JSON `{sid, items: Turn[]}`, artifact bytes stripped, `useCopilotChat.ts:27, 200-210`).

## Gotchas

- **`MockDataClient.ts` is dead code that doesn't compile.** It's untracked in git (new, uncommitted), imports types (`Conversation`, `CopilotMessage`, `CopilotResponse`, `Citation`, `CreateConversationRequest`, `SendMessageRequest`, `SendMessageResponse`, `CopilotFeedbackRequest`) that no longer exist in `types.ts` (`MockDataClient.ts:41-49` vs. `types.ts` — grep confirms none of those identifiers are exported there). Nothing in the app imports `MockDataClient` outside its own test file. `DataClientContext.tsx:8-17` always constructs `HttpDataClient` and its comment says explicitly "No mock path exists." Do not trust `FilterBar.tsx:11`'s comment ("...actually honors (MockDataClient)") — it's stale; the live client is what's wired.
- **`alertPublisher.publishAlerts`/`buildAmAlerts` are dead code.** Exported and unit-tested (`alerting/alertPublisher.test.ts`) but never called from any component — `grep -rn "publishAlerts"` outside tests hits only the definition. `App.tsx:28`'s comment confirms: "no consumers since fake-alert publishing was removed (#87); kept for #84 to rewire to real alerts." `MockDataClient.getActiveAlerts()` (the only source that used to feed it) is itself unreachable per above.
- **`LIVE_REFRESH_MS` comment contradicts its own value.** The doc-comment says "pull fresh data from the backend every 5s" and "5s drains fine," but the exported constant is `30000` (30s), not 5000. `AppContext.tsx:11-17`. Anyone tuning refresh cadence should trust the `30000` value, not the prose.
- **`docker-compose.yml`'s file header is stale.** It's headed "MOCK MODE, AIR-GAPPED: ... The app plugin generates its own mock data client-side" (`docker-compose.yml:1-11`) — true of an earlier build, false now: the plugin always talks to a live dataapi/copilot backend (`DataClientContext.tsx`). The Alertmanager-publishing description in the same header is also stale per the previous point.
- **`getCapabilities` probes 3 endpoints but only uses 1.** `Promise.all([probe(vmQuery), probe(events), probe(flows)])` destructures only the first element (`vmOk`); the events/flows probe results are computed and then discarded. `sources.{measured,simulated,modelled}` are all set from `vmOk` alone — a VM-up-but-Loki-down state still reads fully healthy. `HttpDataClient.ts:583-589`.
- **Provisioning `jsonData`/`secureJsonData` in `plugin/provisioning/plugins/apps.yaml` (`apiUrl`, `apiKey`) are never read.** All backend URLs/timeouts come from the hardcoded `AppConfig` in `config.ts:8-14`; `App.tsx:39` receives but never destructures `AppRootProps.meta`. Changing that YAML has zero effect on the running app.
- **Two independent unit-formatting implementations.** `utils/format.ts` (`bps`, `bytes`) and `components/InterfaceTable.tsx:25-34` (`humanRate`) / `components/FlowTable.tsx:17-27` (`humanBytes`) each reimplement the same base-1000/1024 scaling loop independently — a threshold/rounding tweak in one won't propagate to the others.
- **`HttpDataClient`'s fault-injection methods (`getScenarios`, `injectFault`, `getActiveFaults`, `getCases`, `revertFault`) are outside the shared `DataClient` interface.** `FaultInjectionPage.tsx:66` accesses them via an unchecked cast (`dataClient as unknown as FaultApi`) and optional-chains every call (`faultApi.injectFault?.(...)`) — if a future second `DataClient` implementation is wired in without these methods, the page silently degrades to a visual-only list rather than erroring.
- **`NodeDetailPage`'s live poll cadence was deliberately capped at `LIVE_REFRESH_MS` (30s), not lowered, because of un-abortable fetches.** `AppContext.tsx:12-16`: a 1s tick previously fanned out ~38 requests/sec (32 metrics + topology + 3×labels + events + flows) that `fetchJson` never aborts on effect cleanup, starving the browser's ~6-connections-per-origin pool and hanging the page on "Loading…". Any future sub-30s liveness change needs abort-on-cleanup added first.
- **`SourceBadge` and provenance labels exist but are deliberately unmounted.** `SourceBadge.tsx:8-10` and `utils/source.ts:1-3` both note the demo is meant to read as a real, live product — no "mock"/"simulated" markers shown to the viewer, even though `DataSourceKind` provenance is tracked throughout the type system.
