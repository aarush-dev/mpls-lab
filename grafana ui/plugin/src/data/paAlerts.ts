// Shared wire shape + fetch for the live PA (predictive-analytics) pipeline: dataapi GET /pa/alerts,
// which proxies the pa_alerts service scoring the live topology through the graph-v2 model.
// Single source of truth for the PA wire types — both PaAlertsBanner (the banner) and TopologyPage
// (the precursor blink) read it.

import { appConfig } from '../config';

/** Poll cadence for /pa/alerts, shared by the banner and the topology blink. */
export const PA_POLL_MS = 10000;

export interface PaAlert {
  entity_id: string;
  device: string;
  cause: string | null;
  p_any: number;
  calibrated_probability?: number | null;
  time_to_impact_s?: number | null;
  baseline?: number;
  rise?: number;
  threshold?: number;
}

export interface PaAlertsResponse {
  ts: string | null;
  mode: string | null;
  warm: boolean;
  alerts: PaAlert[];
  predictions: PaAlert[];
  n_scored: number;
  error?: string | null;
}

/** GET /pa/alerts. Throws on a non-2xx or network failure — callers keep their last-good state. */
export async function fetchPaAlerts(): Promise<PaAlertsResponse> {
  const res = await fetch(`${appConfig.apiBaseUrl}/pa/alerts`);
  if (!res.ok) {
    throw new Error(`pa/alerts ${res.status}`);
  }
  return res.json();
}

/** Devices with a PA precursor currently *detected* (risen above baseline) — the blink set. */
export function precursorDevices(resp: PaAlertsResponse): Set<string> {
  return new Set(resp.alerts.map((a) => a.device));
}
