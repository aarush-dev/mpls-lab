// Base path where Grafana mounts this app plugin. Matches plugin.json "id".
// Use for absolute in-app navigation (react-router-dom v5 history.push).
export const APP_BASE = '/a/mplslab-noccopilot-app';

export const nodeDetailPath = (id: string) => `${APP_BASE}/node/${encodeURIComponent(id)}`;

export const copilotPath = `${APP_BASE}/copilot`;
