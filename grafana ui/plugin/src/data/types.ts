// Frontend-owned domain types for the NOC Copilot app plugin.
// These are the public contract between UI, state, and data-client layers.

export type DataSourceKind = 'mock' | 'measured' | 'simulated' | 'modelled' | 'ground_truth' | 'prediction';

export interface TimeRange {
  fromMs: number;
  toMs: number;
}

export interface Filters {
  timeRange?: TimeRange;
  pop?: string;
  siteType?: string;
  device?: string;
  vrf?: string;
  hub?: string;
}

export interface Capabilities {
  sources: Record<string, boolean>;
  datasetWindow: TimeRange;
}

export interface MetricPoint {
  tMs: number;
  value: number | null;
}

export interface MetricSeries {
  key: string;
  label: string;
  unit?: string;
  source: DataSourceKind;
  points: MetricPoint[];
}

export interface Overview {
  reportingDevices: number;
  expectedDevices: number;
  degradedDevices: number;
  totalTunnels: number;
  degradedTunnels: number;
  activeIncidents: number;
  highestRisk?: { deviceId: string; score: number };
  nearestTimeToImpactSeconds?: number | null;
}

export interface TopologyNode {
  id: string;
  role: string;
  siteType?: string;
  pop?: string;
  parent?: string;
  vrfs?: string[];
}

/** TopologyNode + a live-at-cursor health state, without changing the shared base type. */
export interface TopologyNodeLive extends TopologyNode {
  state?: 'red' | 'amber' | 'yellow' | 'green';
}

export interface TopologyLink {
  source: string;
  target: string;
  sourceIf?: string;
  targetIf?: string;
  kind?: 'physical' | 'tunnel';
}

export interface TopologyGraph {
  nodes: TopologyNode[];
  links: TopologyLink[];
}

export interface NetworkEvent {
  tsMs: number;
  device?: string;
  app?: string;
  severity?: string;
  line: string;
}

export interface FlowRecord {
  tsMs: number;
  device?: string;
  ipSrc?: string;
  ipDst?: string;
  portSrc?: number;
  portDst?: number;
  proto?: string | number;
  bytes?: number;
  packets?: number;
}

export interface Evidence {
  label: string;
  detail: string;
  source: DataSourceKind;
}

export interface RecommendedAction {
  title: string;
  detail: string;
}

export interface Incident {
  id: string;
  status: 'open' | 'active' | 'resolved' | 'unknown';
  faultType: string;
  severity: 'low' | 'medium' | 'high' | 'unknown';
  source: 'ground_truth' | 'prediction' | 'mock';
  deviceIds: string[];
  startedAt: string;
  impactAt?: string | null;
  endedAt?: string | null;
  summary: string;
  confidence?: number;
  timeToImpactSeconds?: number;
  evidence: Evidence[];
  affectedScope: string[];
  rootCauseHypotheses: string[];
  recommendedActions: RecommendedAction[];
}

export interface Prediction {
  id: string;
  deviceId: string;
  faultType: string;
  confidence: number;
  timeToImpactSeconds: number;
  source: 'mock';
  issuedAtMs: number;
}

export interface ApiError {
  status: number;
  code: string;
  message: string;
  retryable: boolean;
  requestId?: string;
}

export interface TelemetryRequest {
  deviceId?: string;
  keys?: string[];
  timeRange?: TimeRange;
}

// --- HttpDataClient additions -----------------------------------------------------------------

/**
 * One metric the sim emits. `metricCatalog.ts` is the single source of truth for metric grouping;
 * `promql` carries a `$dev` placeholder HttpDataClient substitutes with the queried device id.
 * `entityLabel` (when set) names the Prometheus label that identifies the per-metric entity
 * (e.g. "interface" or "tunnel") — used to disambiguate one device's many series.
 */
export interface MetricDescriptor {
  name: string;
  promql: string;
  label: string;
  unit?: string;
  source: DataSourceKind;
  group: string;
  entityLabel?: string;
}

/** A fault scenario the backend can inject (GET /faults/scenarios). Real backend shape:
 * `dataapi/faults_api.py:scenarios`. */
export interface FaultScenario {
  name: string;
  description: string;
  valid_roles: string[];
  default_duration: number;
}

/** One live injection in flight (GET /faults/active, `dataapi/faults_api.py:active`). `phase`
 * seeds "buildup" at inject; `lead` (seconds) and `t_impact` (ISO) are null until the worker's
 * first status write. */
export interface ActiveFault {
  scenario_id: string;
  scenario: string;
  target: string;
  started_at: string;
  duration?: number;
  phase: 'buildup' | 'impact' | 'reverting' | null;
  lead?: number | null;
  t_impact?: string | null;
}

/** One open forensic case digest (copilot GET /cases, `copilot/forensic/case.py:case_summary`). */
export interface ForensicCase {
  id: string;
  ts: string | null;
  device: string | null;
  fault_type: string | null;
  severity: 'low' | 'medium' | 'high' | 'unknown';
}

/** Request body for POST /faults/inject. */
export interface InjectFaultRequest {
  scenario: string;
  target: string;
  severity?: 'low' | 'medium' | 'high';
  duration?: number;
  buildup?: number; // precursor lead (s) before impact; omitted => backend draws the prior
}

// --- Copilot chat trace model (mirrors backend `event_wire`, ADR-0009) ------------------------
// The real /chat streams a 7-type event trace, not the mock's structured response. Each event is
// one `event_wire` dict: {type, ts, ...payload}. `CopilotTurn` is the folded result of one turn.

/** Request body for the streaming `DataClient.chat`. `sessionId` is always sent (multi-turn memory,
 * #66); `workspace` gates the shell/artifact tools (default false = read-only). History mode sets
 * `start`/`end` (epoch seconds); Live mode omits both so the backend rolls its own window. */
export interface ChatRequest {
  question: string;
  start?: number;
  end?: number;
  skills?: string[]; // reserved for a later picker (#66 out of scope)
  sessionId: string;
  workspace: boolean;
}

interface ChatEventBase {
  ts: string;
}
export interface UserMsgEvent extends ChatEventBase {
  type: 'user_msg';
  content: string;
}
export interface ThinkEvent extends ChatEventBase {
  type: 'think';
  content: string;
}
export interface ToolCallEvent extends ChatEventBase {
  type: 'tool_call';
  name: string;
  arguments: Record<string, unknown>;
  id: string;
}
export interface ToolResultEvent extends ChatEventBase {
  type: 'tool_result';
  id: string;
  name: string;
  content: string;
  n: number;
}
export interface GateEvent extends ChatEventBase {
  type: 'gate';
  ok: boolean;
  missing: string[];
  retry: number;
}
export interface AssistantMsgEvent extends ChatEventBase {
  type: 'assistant_msg';
  content: string;
}
export interface ArtifactEvent extends ChatEventBase {
  type: 'artifact';
  name: string;
  path?: string;
  kind?: string;
  [k: string]: unknown;
}
export type ChatEvent =
  | UserMsgEvent
  | ThinkEvent
  | ToolCallEvent
  | ToolResultEvent
  | GateEvent
  | AssistantMsgEvent
  | ArtifactEvent;

/** One `[source:offset]` citation lifted from the answer prose. */
export interface TurnCitation {
  id: string; // "metrics:0"
  source: string; // "metrics"
  offset: number; // 0
}

/** One folded copilot turn: the event trace, the final answer, its deduped citations, a
 * citation-id → source tool_result map (so a chip can locate its evidence card), and the last
 * gate outcome (undefined on a clarifying ask-back). */
export interface CopilotTurn {
  events: ChatEvent[];
  answer: string;
  citations: TurnCitation[];
  citeMap: Record<string, ToolResultEvent>;
  gate?: GateEvent;
}
