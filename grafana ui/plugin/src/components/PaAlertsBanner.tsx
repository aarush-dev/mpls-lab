import React, { useEffect, useRef, useState } from 'react';
import { fetchPaAlerts, PaAlertsResponse, PA_POLL_MS } from '../data/paAlerts';
import { useToaster } from './AlertToaster';
import { copilotCasePath, COPILOT_CASE_KEY } from '../constants';

// PA live-prediction banner (demo path A). Polls dataapi /pa/alerts (which proxies
// the pa_alerts service scoring the live topology through the graph-v2 model) and
// shows the current PA predictions. When an entity's PA risk RISES above its
// baseline (a freshly injected fault), it appears here and fires a toast — this is
// the "our PA pipeline predicted the injected fault" signal on the dashboard.
// Copilot is intentionally NOT in this path.

function eta(s?: number | null): string {
  if (s == null) return '—';
  if (s < 90) return `${Math.round(s)}s`;
  return `${Math.round(s / 60)}m`;
}

export function PaAlertsBanner() {
  const [data, setData] = useState<PaAlertsResponse | null>(null);
  const seen = useRef<Set<string>>(new Set());
  const { notify } = useToaster();

  useEffect(() => {
    let alive = true;
    const tick = async () => {
      try {
        const json = await fetchPaAlerts();
        if (!alive) return;
        // toast on NEW alerting entities
        for (const a of json.alerts) {
          if (!seen.current.has(a.entity_id)) {
            seen.current.add(a.entity_id);
            notify({
              severity: 'critical',
              title: `PA prediction: ${a.device}`,
              text: `${a.cause ?? 'fault'} risk ${(a.p_any * 100).toFixed(0)}%${
                a.time_to_impact_s != null ? `, ETA ${eta(a.time_to_impact_s)}` : ''
              }`,
            });
          }
        }
        // drop cleared entities from the seen-set so a re-alert re-notifies
        const now = new Set(json.alerts.map((a) => a.entity_id));
        seen.current.forEach((e) => {
          if (!now.has(e)) {
            seen.current.delete(e);
          }
        });
        setData(json);
      } catch {
        /* service down — banner shows disconnected */
      }
    };
    tick();
    const id = setInterval(tick, PA_POLL_MS);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, [notify]);

  const alerts = data?.alerts ?? [];
  const active = alerts.length > 0;

  const bar: React.CSSProperties = {
    display: 'flex',
    alignItems: 'center',
    gap: 12,
    padding: '8px 14px',
    borderRadius: 6,
    marginBottom: 10,
    fontSize: 13,
    background: active ? 'rgba(224, 47, 68, 0.12)' : 'rgba(255,255,255,0.04)',
    border: `1px solid ${active ? 'rgba(224,47,68,0.5)' : 'rgba(255,255,255,0.08)'}`,
  };
  const dot: React.CSSProperties = {
    width: 9,
    height: 9,
    borderRadius: '50%',
    background: !data ? '#888' : active ? '#e02f44' : '#3bb45a',
    boxShadow: active ? '0 0 8px #e02f44' : 'none',
    flex: 'none',
  };
  const chip: React.CSSProperties = {
    display: 'inline-flex',
    gap: 6,
    alignItems: 'baseline',
    padding: '2px 8px',
    borderRadius: 4,
    background: 'rgba(224,47,68,0.22)',
    color: '#ffd7dc',
    fontWeight: 600,
  };

  let label: string;
  if (!data) {
    label = 'PA pipeline: connecting…';
  } else if (data.error) {
    label = `PA pipeline: ${data.error}`;
  } else if (!data.warm && data.mode === 'rank') {
    label = `PA pipeline: warming up baseline (${data.n_scored} entities)…`;
  } else if (active) {
    label = `PA PREDICTION — ${alerts.length} fault${alerts.length > 1 ? 's' : ''} predicted`;
  } else {
    label = `PA pipeline: monitoring ${data.n_scored} entities — no fault predicted`;
  }

  return (
    <div style={bar} data-testid="pa-alerts-banner">
      <span style={dot} />
      <strong style={{ letterSpacing: 0.3 }}>{label}</strong>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
        {alerts.map((a) => {
          // Deep-link to copilot like "Open copilot", but WITHOUT the fault type — just ask about
          // whatever happened on this device at the prediction time (data.ts), hour-before window.
          const c = { device: a.device, ts: data?.ts ?? null, fault_type: null, severity: '' };
          return (
            <a
              key={a.entity_id}
              href={copilotCasePath(c)}
              onClick={() => {
                try {
                  sessionStorage.setItem(COPILOT_CASE_KEY, JSON.stringify(c));
                } catch {
                  // storage unavailable — query-string fallback still carries device+ts
                }
              }}
              style={{ ...chip, textDecoration: 'none', cursor: 'pointer' }}
              title="Open copilot — investigate this prediction"
            >
              <span>{a.device}</span>
              <span style={{ opacity: 0.85 }}>{a.cause ?? 'fault'}</span>
              <span style={{ opacity: 0.7 }}>· {(a.p_any * 100).toFixed(0)}%</span>
              {a.time_to_impact_s != null && <span style={{ opacity: 0.7 }}>· ETA {eta(a.time_to_impact_s)}</span>}
            </a>
          );
        })}
      </div>
    </div>
  );
}
