export interface AppConfig {
  apiBaseUrl: string;
  requestTimeoutMs: number;
  copilotBaseUrl: string;
  copilotTimeoutMs: number;
}

export const appConfig: AppConfig = {
  apiBaseUrl: 'http://127.0.0.1:8000',
  requestTimeoutMs: 8000,
  // Copilot /chat is a separate service with a much longer budget (an investigation can take ~3 min).
  copilotBaseUrl: 'http://127.0.0.1:8100',
  copilotTimeoutMs: 180000,
};
