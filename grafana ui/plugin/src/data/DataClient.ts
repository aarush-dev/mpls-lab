import type {
  Capabilities,
  Overview,
  TopologyGraph,
  TelemetryRequest,
  MetricSeries,
  Filters,
  NetworkEvent,
  FlowRecord,
  Incident,
  Prediction,
  ChatRequest,
  ChatEvent,
  CopilotTurn,
} from './types';

export interface DataClient {
  getCapabilities(): Promise<Capabilities>;
  getOverview(filters: Filters): Promise<Overview>;
  getTopology(filters: Filters): Promise<TopologyGraph>;
  getTelemetry(request: TelemetryRequest): Promise<MetricSeries[]>;
  getEvents(filters: Filters): Promise<NetworkEvent[]>;
  getFlows(filters: Filters): Promise<FlowRecord[]>;
  getIncidents(filters: Filters): Promise<Incident[]>;
  getPredictions(filters: Filters): Promise<Prediction[]>;
  /** Stream the real copilot `/chat` SSE trace, calling `onEvent` per event, resolving the folded
   * turn. Rejects when the copilot service is unreachable (never a fake reply). An optional `signal`
   * lets the caller abort the in-flight turn (Stop button, #70). */
  chat(request: ChatRequest, onEvent: (event: ChatEvent) => void, signal?: AbortSignal): Promise<CopilotTurn>;
}
