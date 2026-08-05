# API_CONTRACT.md

The `DataClient` interface (`plugin/src/data/DataClient.ts`) is the contract between UI and data layer. Domain types live in `plugin/src/data/types.ts`.

One implementation: `HttpDataClient` (`plugin/src/data/HttpDataClient.ts`, live `dataapi` + copilot). No mock mode.

## Interface

```ts
interface DataClient {
  getCapabilities(): Promise<Capabilities>;
  getOverview(filters: Filters): Promise<Overview>;
  getTopology(filters: Filters): Promise<TopologyGraph>;
  getTelemetry(request: TelemetryRequest): Promise<MetricSeries[]>;
  getEvents(filters: Filters): Promise<NetworkEvent[]>;
  getFlows(filters: Filters): Promise<FlowRecord[]>;
  getIncidents(filters: Filters): Promise<Incident[]>;
  getPredictions(filters: Filters): Promise<Prediction[]>;
  chat(request: ChatRequest, onEvent: (event: ChatEvent) => void): Promise<CopilotTurn>;
}
```

`setCursor`/`getActiveAlerts` don't exist — those were mock-only hooks on the deleted `MockDataClient`. `HttpDataClient` never implemented them; a live backend has its own notion of "now". Callers still feature-detect: `if ('setCursor' in client) ...`.

## Shared types (`types.ts`)

- `Filters { timeRange?: TimeRange; pop?: string; siteType?: string; device?: string; vrf?: string; hub?: string }`
- `TimeRange { fromMs: number; toMs: number }`
- `DataSourceKind = 'mock' | 'measured' | 'simulated' | 'modelled' | 'ground_truth' | 'prediction'`

## Methods

### `getCapabilities(): Promise<Capabilities>`
No request args.
Response: `Capabilities { sources: Record<string, boolean>; datasetWindow: TimeRange }`.
`HttpDataClient`: no matching endpoint. Derived by probing `/metrics` (`vector(1)`), `/events`, `/flows` in parallel; `sources.measured/simulated/modelled` all track VM reachability, `ground_truth`/`prediction` are always `true`, `mock` always `false`. `datasetWindow` is `[now - 30d, now]` (VM's retention). Drives the header `LabStatusBadge`.

### `getOverview(filters: Filters): Promise<Overview>`
Response: `Overview { reportingDevices, expectedDevices, degradedDevices, totalTunnels, degradedTunnels, activeIncidents: number; highestRisk?: { deviceId: string; score: number }; nearestTimeToImpactSeconds?: number | null }`.
`HttpDataClient`: derived client-side, no direct endpoint. `expectedDevices` from `/topology` node count; `reportingDevices`/`totalTunnels`/`degradedTunnels` from instant PromQL scalars against `/metrics`; `degradedDevices`/`activeIncidents` from devices with an active `/labels` row; `highestRisk`/`nearestTimeToImpactSeconds` from `getPredictions`.

### `getTopology(filters: Filters): Promise<TopologyGraph>`
Response: `TopologyGraph { nodes: TopologyNode[]; links: TopologyLink[] }` where `TopologyNode { id, role, siteType?, pop?, parent?, vrfs? }` and `TopologyLink { source, target, sourceIf?, targetIf?, kind?: 'physical' | 'tunnel' }`.
`HttpDataClient`: `GET /topology` (`sources.topology_graph()`), direct match by name/shape. `state` (red/amber/green, not part of the base type but read by the UI as `TopologyNodeLive`) is derived from active `/labels` rows at request time. Live `/topology` has no `pop`/`parent` — both are derived from link adjacency: `p`/`pe` get `pop` from the device id (`p1-4`→`pop1`, `p5-8`→`pop2`, `pe1-2`→`pop1`, etc — `popOf()`); `ce` gets `parent` = its uplink `pe` (lowest id if redundant) and inherits that `pe`'s `pop`; `host` gets `parent` = its `ce` and inherits that `ce`'s `pop`.

### `getTelemetry(request: TelemetryRequest): Promise<MetricSeries[]>`
Request: `TelemetryRequest { deviceId?: string; keys?: string[]; timeRange?: TimeRange }`.
Response: array of `MetricSeries { key, label, unit?, source: DataSourceKind; points: MetricPoint[] }`, `MetricPoint { tMs: number; value: number | null }`.
`HttpDataClient`: one `GET /metrics` range query per descriptor in `data/metricCatalog.ts` (11 metric groups, 29 metrics), run in parallel, PromQL templated with `$dev` → `request.deviceId`. `request.keys` is not used to filter — all catalog metrics are always queried; a metric absent on a given device role just returns an empty series. `step` is fixed at 30s. A per-metric fetch failure yields an empty series rather than failing the whole call.

### `getEvents(filters: Filters): Promise<NetworkEvent[]>`
Response: `NetworkEvent { tsMs, device?, app?, severity?, line: string }`.
`HttpDataClient`: `GET /events` (Loki log rows, `sources.events_rows`), `{ rows: [...] }`, filtered by `filters.device`/`timeRange`, sorted newest-first.

### `getFlows(filters: Filters): Promise<FlowRecord[]>`
Response: `FlowRecord { tsMs, device?, ipSrc?, ipDst?, portSrc?, portDst?, proto?, bytes?, packets? }`.
`HttpDataClient`: `GET /flows` (nfacctd flow records, `sources.flow_rows`), `{ rows: [...] }`, filtered by `filters.device`/`timeRange`, sorted newest-first.

### `getIncidents(filters: Filters): Promise<Incident[]>`
Response: `Incident { id, status: 'open'|'active'|'resolved'|'unknown', faultType, severity: 'low'|'medium'|'high'|'unknown', source: 'ground_truth'|'prediction'|'mock', deviceIds: string[], startedAt, impactAt?, endedAt?, summary, confidence?, timeToImpactSeconds?, evidence: Evidence[], affectedScope: string[], rootCauseHypotheses: string[], recommendedActions: RecommendedAction[] }`.
`HttpDataClient`: derived client-side from `GET /labels` (ground-truth fault timeline, `sources.label_rows()`) — no `/incidents` endpoint. Status derived from now vs. each row's `t_start`/`t_impact`/`t_end` (`open` before impact, `active` during, `resolved` after); rows not yet started are skipped. `source` is always `'ground_truth'`.

### `getPredictions(filters: Filters): Promise<Prediction[]>`
Response: `Prediction { id, deviceId, faultType, confidence, timeToImpactSeconds, source: 'mock', issuedAtMs }`.
`HttpDataClient`: derived client-side from `GET /labels` — no ML/prediction endpoint exists. Only rows in their pre-impact window (`t_start <= now < t_impact`) are surfaced; `confidence` ramps linearly from 0 at `t_start` to 1 at `t_impact`. These are ground-truth-derived, not model output — `source` stays `'mock'` on the shared type since no real predictor exists.

### `chat(request: ChatRequest, onEvent: (event: ChatEvent) => void): Promise<CopilotTurn>`
Request: `ChatRequest { question: string; start?: number; end?: number; skills?: string[]; sessionId: string; workspace: boolean }`. `sessionId` is always sent (multi-turn memory); `workspace` gates the shell/artifact tools (default false = read-only). History mode sets `start`/`end` (epoch seconds); Live mode omits both so the backend rolls its own window.
`HttpDataClient`: `POST ${copilotBaseUrl}/chat` (separate service, default `http://127.0.0.1:8100`, not `dataapi`'s `:8000`) with body `{ question, session_id, workspace, start?, end? }`. Streams the response as SSE via `fetch` + a `ReadableStream` reader (not `EventSource`, which is GET-only). `copilotTimeoutMs` (180s) backstops the whole call; an unreachable service rejects via `normalizeError` — the UI shows an honest error, never a fake reply.
Each SSE frame is one `event_wire` dict (ADR-0009): `ChatEvent = UserMsgEvent | ThinkEvent | ToolCallEvent | ToolResultEvent | GateEvent | AssistantMsgEvent | ArtifactEvent`, each `{ts, type, ...payload}`. `onEvent` fires per event as it streams. The resolved `CopilotTurn { events, answer, citations, citeMap, gate? }` folds the trace: `answer` is the last `assistant_msg`; `citations: TurnCitation[]` (`{id, source, offset}`) are `[source:offset]` refs pulled from the answer, deduped in first-appearance order; `citeMap` maps each citation id to the `tool_result` event that produced it; `gate` is the last gate outcome (undefined on a clarifying ask-back).
Transport helpers live in `data/copilotChat.ts`, pure and unit-tested: `parseSseFrames(buffer)` (incremental `\n\n`-delimited SSE splitter, carries partial frames forward) and `mapEventsToTurn(events)` (the folding logic above).

## Fault injection (`FaultInjectionPage.tsx` only — not part of `DataClient`)

`HttpDataClient` exposes 4 extra methods, used only by the Fault Injection page:

- `getScenarios(): Promise<FaultScenario[]>` → `GET /faults/scenarios` — the 21 scenario types, each with `valid_roles` (which device roles the scenario can target) and `default_duration`.
- `injectFault(req: InjectFaultRequest): Promise<{ scenario_id: string }>` → `POST /faults/inject { scenario, target, severity?, duration? }` — spawns the scenario against the sim in a background thread; 404 if the scenario is unknown, 422 if `target`'s role isn't valid for it, 409 if `target` is already being injected. `duration` defaults to 90s; the fault auto-reverts when it elapses.
- `getActiveFaults(): Promise<unknown>` → `GET /faults/active` — currently-running injections.
- `revertFault(scenarioId): Promise<unknown>` → `POST /faults/revert/{scenario_id}` — cancels a running injection early.

`HttpDataClient` exposes all 4; `FaultInjectionPage` feature-detects and falls back to visual-only escalation only if they're absent.

## Supporting types

- `Evidence { label, detail, source: DataSourceKind }`
- `RecommendedAction { title, detail }`
- `ApiError { status, code, message, retryable, requestId? }` — declared in `types.ts`; `HttpDataClient` throws these via `errors.ts` `normalizeError` on any fetch failure (`{ detail }` FastAPI body → `message`).

## `dataapi/app.py` reference (endpoints wired to the plugin)

`GET /metrics`, `GET /events`, `GET /flows`, `GET /labels`, `GET /topology`, `GET /datasets` (unused by the plugin), plus `GET /faults/scenarios`, `POST /faults/inject`, `GET /faults/active`, `POST /faults/revert/{id}`. FastAPI default error body on failure: `{ "detail": "<message>" }` (raised via `HTTPException`). Bound to `127.0.0.1` only; CORS allows `http://localhost:3000` + `http://127.0.0.1:3000` (the plugin's origin) — `/faults/*` routes run `docker exec` into the lab, so this allow-list is the auth boundary. Must run single-worker (`./start.sh`) — the `/faults/*` registry is in-process memory. See `INTEGRATION_GUIDE.md` for how `HttpDataClient` maps onto these.
