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
