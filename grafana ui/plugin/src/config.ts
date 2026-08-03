export interface AppConfig {
  mode: 'mock' | 'api';
  apiBaseUrl: string;
  requestTimeoutMs: number;
  showDemoBadge: boolean;
  // UI-2 (#51): the copilot chat backend, a separate service from dataapi.
  copilotBaseUrl: string;
  copilotTimeoutMs: number; // idle timeout per stream read; a multi-tool chat outlives the JSON one
}

export const appConfig: AppConfig = {
  mode: 'api',
  apiBaseUrl: 'http://127.0.0.1:8000',
  requestTimeoutMs: 8000,
  showDemoBadge: false,
  copilotBaseUrl: 'http://127.0.0.1:8100',
  copilotTimeoutMs: 120000,
};
