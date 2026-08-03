// Base path where Grafana mounts this app plugin. Matches plugin.json "id".
// Use for absolute in-app navigation (react-router-dom v5 history.push).
export const APP_BASE = '/a/mplslab-noccopilot-app';

export const nodeDetailPath = (id: string) => `${APP_BASE}/node/${encodeURIComponent(id)}`;

// UI-3 (#52): deep-link into the Copilot page pre-scoped to a device / incident / window, so it
// auto-asks the copilot to explain it. Window is epoch ms (the page converts to seconds).
export const copilotExplainPath = (p: {
  device?: string;
  incident?: string;
  faultType?: string;
  from?: number;
  to?: number;
}) => {
  const q = new URLSearchParams();
  if (p.device) {
    q.set('device', p.device);
  }
  if (p.incident) {
    q.set('incident', p.incident);
  }
  if (p.faultType) {
    q.set('faultType', p.faultType);
  }
  if (p.from && p.from > 0) {
    q.set('from', String(p.from));
  }
  if (p.to && p.to > 0) {
    q.set('to', String(p.to));
  }
  return `${APP_BASE}/copilot?${q.toString()}`;
};
