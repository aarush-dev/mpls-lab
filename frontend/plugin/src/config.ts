export interface AppConfig {
  mode: 'mock' | 'api';
  apiBaseUrl: string;
  requestTimeoutMs: number;
  showDemoBadge: boolean;
}

export const appConfig: AppConfig = {
  mode: 'mock',
  apiBaseUrl: 'http://127.0.0.1:8000',
  requestTimeoutMs: 8000,
  showDemoBadge: false,
};
