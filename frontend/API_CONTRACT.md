# API_CONTRACT.md

The `DataClient` interface (`frontend/plugin/src/data/DataClient.ts`) is the contract between UI and data layer. Domain types live in `frontend/plugin/src/data/types.ts`.

Only implementation today: `MockDataClient` (`frontend/plugin/src/data/MockDataClient.ts`), reads bundled fixture JSON. No HTTP client exists.

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
  getConversation(id: string): Promise<Conversation>;
  createConversation(request: CreateConversationRequest): Promise<Conversation>;
  sendMessage(request: SendMessageRequest): Promise<SendMessageResponse>;
  submitFeedback(request: CopilotFeedbackRequest): Promise<void>;
}
```

`MockDataClient` also exposes `setCursor(n)`, called every demo-clock tick by `App.tsx` to move the mock "now". Not part of `DataClient` — a real `HttpDataClient` has its own server-side notion of "now" and doesn't need it. Callers that want it should feature-detect: `if ('setCursor' in client) ...`.

## Shared types (`types.ts`)

- `Filters { timeRange?: TimeRange; pop?: string; siteType?: string; device?: string; vrf?: string; hub?: string }`
- `TimeRange { fromMs: number; toMs: number }`
- `DataSourceKind = 'mock' | 'measured' | 'simulated' | 'modelled' | 'ground_truth' | 'prediction'`

## Methods

### `getCapabilities(): Promise<Capabilities>`
No request args.
Response: `Capabilities { sources: Record<string, boolean>; datasetWindow: TimeRange }`.
Future backend: no matching endpoint in `dataapi/app.py`. Would need to be synthesized frontend-side or added.

### `getOverview(filters: Filters): Promise<Overview>`
Response: `Overview { reportingDevices, expectedDevices, degradedDevices, totalTunnels, degradedTunnels, activeIncidents: number; highestRisk?: { deviceId: string; score: number }; nearestTimeToImpactSeconds?: number | null }`.
Future backend: no matching endpoint. Would be derived client-side from `/topology`, `/labels`, `/metrics`, not a direct passthrough.

### `getTopology(filters: Filters): Promise<TopologyGraph>`
Response: `TopologyGraph { nodes: TopologyNode[]; links: TopologyLink[] }` where `TopologyNode { id, role, siteType?, pop?, parent?, vrfs? }` and `TopologyLink { source, target, sourceIf?, targetIf?, kind?: 'physical' | 'tunnel' }`.
Future backend: `GET /topology` in `dataapi/app.py` returns `sources.topology_graph()` — direct match by name and shape.

### `getTelemetry(request: TelemetryRequest): Promise<MetricSeries[]>`
Request: `TelemetryRequest { deviceId?: string; keys?: string[]; timeRange?: TimeRange }`.
Response: array of `MetricSeries { key, label, unit?, source: DataSourceKind; points: MetricPoint[] }`, `MetricPoint { tMs: number; value: number | null }`.
Future backend: `GET /metrics` in `dataapi/app.py` — PromQL passthrough to VictoriaMetrics (`sources.vm_query` / `vm_query_range`). Response shape differs (`{ result: ... }` raw PromQL result); an `HttpDataClient` would need to translate.

### `getEvents(filters: Filters): Promise<NetworkEvent[]>`
Response: `NetworkEvent { tsMs, device?, app?, severity?, line: string }`.
Future backend: `GET /events` in `dataapi/app.py` — Loki log rows (`sources.events_rows`), returns `{ rows: [...] }`.

### `getFlows(filters: Filters): Promise<FlowRecord[]>`
Response: `FlowRecord { tsMs, device?, ipSrc?, ipDst?, portSrc?, portDst?, proto?, bytes?, packets? }`.
Future backend: `GET /flows` in `dataapi/app.py` — nfacctd flow records (`sources.flow_rows`), returns `{ rows: [...] }`.

### `getIncidents(filters: Filters): Promise<Incident[]>`
Response: `Incident { id, status: 'open'|'active'|'resolved'|'unknown', faultType, severity: 'low'|'medium'|'high'|'unknown', source: 'ground_truth'|'prediction'|'mock', deviceIds: string[], startedAt, impactAt?, endedAt?, summary, confidence?, timeToImpactSeconds?, evidence: Evidence[], affectedScope: string[], rootCauseHypotheses: string[], recommendedActions: RecommendedAction[] }`.
Future backend: closest is `GET /labels` in `dataapi/app.py` (ground-truth fault timeline, `sources.label_rows()`) — raw rows, not this shape. No `/incidents` endpoint exists.

### `getPredictions(filters: Filters): Promise<Prediction[]>`
Response: `Prediction { id, deviceId, faultType, confidence, timeToImpactSeconds, source: 'mock', issuedAtMs }`.
Future backend: no matching endpoint. `dataapi/app.py` has no ML/prediction output; predictions are entirely fixture-fabricated today.

### `getConversation(id: string): Promise<Conversation>`
### `createConversation(request: CreateConversationRequest): Promise<Conversation>`
### `sendMessage(request: SendMessageRequest): Promise<SendMessageResponse>`
### `submitFeedback(request: CopilotFeedbackRequest): Promise<void>`
`Conversation { id, messages: CopilotMessage[], context?: { deviceIds?, incidentId?, timeRange? } }`.
`CopilotMessage { id, role: 'user'|'assistant', content, createdAt, state?: 'draft'|'sending'|'complete'|'error' }`.
`CreateConversationRequest { context?; firstMessage? }`.
`SendMessageRequest { conversationId, message: CopilotMessage, context? }`.
`SendMessageResponse { message: CopilotMessage; response?: CopilotResponse }`.
`CopilotResponse { summary, predictedIssue?, confidence?, timeToImpactSeconds?, affectedScope: string[], evidence: Evidence[], rootCauseHypotheses: string[], recommendedActions: RecommendedAction[], citations: Citation[], disclaimer? }`.
`CopilotFeedbackRequest { conversationId, messageId, rating: 'up'|'down', note? }`.
Future backend: no matching endpoint in `dataapi/app.py` (that service is data-only, no LLM/copilot route). This whole group is unimplemented server-side; `MockDataClient` serves canned seed conversations from `fixtures/conversations.json`.

## Supporting types

- `Evidence { label, detail, source: DataSourceKind }`
- `RecommendedAction { title, detail }`
- `Citation { title, href }`
- `ApiError { status, code, message, retryable, requestId? }` — declared in `types.ts`, not yet thrown/used by `MockDataClient`.

## `dataapi/app.py` reference (6 GET endpoints, not wired to the plugin)

`GET /metrics`, `GET /events`, `GET /flows`, `GET /labels`, `GET /topology`, `GET /datasets`. FastAPI default error body on failure: `{ "detail": "<message>" }` (raised via `HTTPException`). Bound to `127.0.0.1` only, local-only offline tool. See `INTEGRATION_GUIDE.md` for how a real `HttpDataClient` would map onto these.
