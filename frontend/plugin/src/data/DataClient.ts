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
  Conversation,
  CreateConversationRequest,
  SendMessageRequest,
  SendMessageResponse,
  CopilotFeedbackRequest,
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
  getConversation(id: string): Promise<Conversation>;
  createConversation(request: CreateConversationRequest): Promise<Conversation>;
  sendMessage(request: SendMessageRequest): Promise<SendMessageResponse>;
  submitFeedback(request: CopilotFeedbackRequest): Promise<void>;
}
