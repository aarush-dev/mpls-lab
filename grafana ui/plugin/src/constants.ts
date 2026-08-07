// Base path where Grafana mounts this app plugin. Matches plugin.json "id".
// Use for absolute in-app navigation (react-router-dom v5 history.push).
export const APP_BASE = '/a/mplslab-noccopilot-app';

export const nodeDetailPath = (id: string) => `${APP_BASE}/node/${encodeURIComponent(id)}`;

export const copilotPath = `${APP_BASE}/copilot`;

// sessionStorage hand-off for the "Open copilot" deep-link. Survives the full-reload navigation the
// plain <a> triggers, where Grafana can strip the query string before CopilotPage's effect reads it.
export const COPILOT_CASE_KEY = 'noc.copilot.case';

// Deep-link to /copilot carrying a forensic case so the tab opens a fresh chat auto-asking about it.
// CopilotPage reads device/ts/fault/sev and composes the question + hour-before window.
export const copilotCasePath = (c: {
  device: string | null;
  ts: string | null;
  fault_type: string | null;
  severity: string;
}) => {
  const p = new URLSearchParams();
  if (c.device) p.set('device', c.device);
  if (c.ts) p.set('ts', c.ts);
  if (c.fault_type) p.set('fault', c.fault_type);
  if (c.severity) p.set('sev', c.severity);
  return `${copilotPath}?${p.toString()}`;
};
