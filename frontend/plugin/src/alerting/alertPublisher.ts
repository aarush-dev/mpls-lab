// Pushes synthetic NOC alerts into the local Alertmanager so they surface in the
// native Grafana Alerting UI. Write goes browser -> Alertmanager directly:
// Alertmanager v0.27 sends permissive CORS headers, and Grafana's AM-datasource
// proxy only supports READING alerts, not posting them. Grafana reads these back
// through the provisioned "noc-alertmanager" datasource for its Alerting tab.
//
// Alertmanager runs on the same host as Grafana, port 9093 (see docker-compose).
// We omit startsAt so Alertmanager stamps receive-time itself — keeps this layer
// free of Date.now()/new Date() and lets alerts auto-resolve via resolve_timeout
// once we stop re-posting them.
import { nodeDetailPath } from '../constants';

export interface AlertDescriptor {
  alertname: string;
  node: string;
  severity: 'critical' | 'warning';
  pop?: string;
  summary: string;
  description?: string;
}

export interface AmAlert {
  labels: Record<string, string>;
  annotations: Record<string, string>;
  generatorURL: string;
}

/** Pure: descriptor list -> Alertmanager v2 postable-alert payload. */
export function buildAmAlerts(descriptors: AlertDescriptor[], appOrigin: string): AmAlert[] {
  return descriptors.map((d) => ({
    labels: {
      alertname: d.alertname,
      node: d.node,
      severity: d.severity,
      source: 'noc-copilot',
      ...(d.pop ? { pop: d.pop } : {}),
    },
    annotations: {
      summary: d.summary,
      ...(d.description ? { description: d.description } : {}),
    },
    generatorURL: `${appOrigin}${nodeDetailPath(d.node)}`,
  }));
}

/** Alertmanager base URL: same host as Grafana, port 9093. */
function alertmanagerBase(): string {
  const { protocol, hostname } = window.location;
  return `${protocol}//${hostname}:9093`;
}

/**
 * Fire-and-forget POST of the current firing set. Empty set is a no-op (cleared
 * alerts auto-resolve in Alertmanager). Failures are swallowed — a demo running
 * without the Alertmanager container must not break the UI.
 */
export async function publishAlerts(descriptors: AlertDescriptor[]): Promise<void> {
  if (descriptors.length === 0) {
    return;
  }
  const body = buildAmAlerts(descriptors, window.location.origin);
  try {
    await fetch(`${alertmanagerBase()}/api/v2/alerts`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
  } catch {
    // ponytail: swallow — Alertmanager optional; alerting is a bonus surface, not core UI.
  }
}
