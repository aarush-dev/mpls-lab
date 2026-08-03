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

export interface Citation {
  title: string;
  href: string;
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

export interface CopilotMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  createdAt: string;
  state?: 'draft' | 'sending' | 'complete' | 'error';
}

export interface CopilotResponse {
  summary: string;
  predictedIssue?: string;
  confidence?: number;
  timeToImpactSeconds?: number;
  affectedScope: string[];
  evidence: Evidence[];
  rootCauseHypotheses: string[];
  recommendedActions: RecommendedAction[];
  citations: Citation[];
  disclaimer?: string;
  // UI-2 (#51): the real copilot's visible work. Optional so the mock + its fixtures stay valid.
  trace?: TraceStep[];
  gateVerdict?: GateVerdict;
}

// --- Copilot live chat (UI-2 #51) -------------------------------------------------------------

/** One step in the agent's visible trace, mapped from a canonical copilot event. */
export interface TraceStep {
  kind: 'think' | 'tool_call' | 'tool_result' | 'gate';
  ts?: string;
  content?: string; // think text / tool_result content
  name?: string; // tool name (tool_call, tool_result)
  arguments?: unknown; // tool_call args
  id?: string; // correlates tool_call <-> tool_result
  n?: number; // tool_result row count
  gate?: GateVerdict;
}

/** The quality-gate outcome, folded from the run's gate events. */
export interface GateVerdict {
  ok: boolean;
  missing: string[];
  retry: number;
}

/**
 * A raw SSE frame from POST /chat. Mirrors the copilot's ADR-0009 canonical enum
 * (`agent/loop.py` EVENT_TYPES); each frame is `{type, ts, ...payload}`.
 */
export type CopilotEvent =
  | { type: 'user_msg'; ts?: string; content: string }
  | { type: 'think'; ts?: string; content: string }
  | { type: 'tool_call'; ts?: string; name: string; arguments?: unknown; id?: string }
  | { type: 'tool_result'; ts?: string; id?: string; name?: string; content: string; n?: number }
  | { type: 'gate'; ts?: string; ok: boolean; missing?: string[]; retry?: number }
  | { type: 'assistant_msg'; ts?: string; content: string };

/** Request to the streaming copilot chat. Window is epoch seconds (the copilot's tool window). */
export interface ChatStreamRequest {
  question: string;
  start?: number;
  end?: number;
  skills?: string[];
  sessionId?: string;
  caseId?: string;
}

/** A forensic case summary (UI-4 #53; sample-backed until the copilot exposes /cases). */
export interface CaseSummary {
  id: string;
  device?: string;
  cause?: string;
  alert?: boolean;
  abstain?: boolean;
  ts?: string;
}

/** A forensic case detail: the report markdown + its prediction record. */
export interface CaseDetail {
  id: string;
  caseMd: string;
  prediction?: Record<string, unknown>;
  chats?: string[];
}

export interface Conversation {
  id: string;
  messages: CopilotMessage[];
  context?: {
    deviceIds?: string[];
    incidentId?: string;
    timeRange?: TimeRange;
  };
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

/** A fault scenario the backend can inject (GET /faults/scenarios). */
export interface FaultScenario {
  id: string;
  label?: string;
  description?: string;
  [k: string]: unknown;
}

/** Request body for POST /faults/inject. */
export interface InjectFaultRequest {
  scenario: string;
  target: string;
  severity?: 'low' | 'medium' | 'high';
  duration?: number;
}

export interface CreateConversationRequest {
  context?: Conversation['context'];
  firstMessage?: string;
}

export interface SendMessageRequest {
  conversationId: string;
  message: CopilotMessage;
  context?: Conversation['context'];
}

export interface SendMessageResponse {
  message: CopilotMessage;
  response?: CopilotResponse;
}

export interface CopilotFeedbackRequest {
  conversationId: string;
  messageId: string;
  rating: 'up' | 'down';
  note?: string;
}
