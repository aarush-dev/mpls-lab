import React, { useEffect, useState } from 'react';
import { css } from '@emotion/css';
import { PluginPage } from '@grafana/runtime';
import { GrafanaTheme2 } from '@grafana/data';
import { useStyles2 } from '@grafana/ui';

import { MetricCard } from '../components/MetricCard';
import { useAppState } from '../state/AppContext';
import { useDataClient } from '../data/DataClientContext';
import { Incident, Overview } from '../data/types';
import { count, secondsToEta } from '../utils/format';

export function OverviewPage() {
  const styles = useStyles2(getStyles);
  const { refreshTick, filters, injectedFaults } = useAppState();
  const dataClient = useDataClient();

  const [overview, setOverview] = useState<Overview | null>(null);
  const [incidents, setIncidents] = useState<Incident[]>([]);

  // Refetch every time the shared live/history clock advances (5s live tick or a history range
  // pick), so the whole page is a pure function of `refreshTick` — no local polling here.
  useEffect(() => {
    let cancelled = false;

    dataClient.getOverview(filters).then((result) => {
      if (!cancelled) {
        setOverview(result);
      }
    });
    dataClient.getIncidents(filters).then((result) => {
      if (!cancelled) {
        setIncidents(result.filter((i) => i.status === 'open' || i.status === 'active'));
      }
    });

    return () => {
      cancelled = true;
    };
  }, [dataClient, refreshTick, filters, injectedFaults]);

  return (
    <PluginPage>
      <h1>Overview</h1>
      <div className={styles.cardRow}>
        <MetricCard
          label="Reporting devices"
          value={overview ? `${count(overview.reportingDevices)} / ${count(overview.expectedDevices)}` : '—'}
        />
        <MetricCard
          label="Degraded devices"
          value={overview ? count(overview.degradedDevices) : '—'}
          tone={overview && overview.degradedDevices > 0 ? 'warning' : 'default'}
        />
        <MetricCard
          label="Tunnels"
          value={overview ? `${count(overview.totalTunnels - overview.degradedTunnels)} / ${count(overview.totalTunnels)}` : '—'}
          sub="healthy / total"
          tone={overview && overview.degradedTunnels > 0 ? 'warning' : 'default'}
        />
        <MetricCard
          label="Active incidents"
          value={overview ? count(overview.activeIncidents) : '—'}
          tone={overview && overview.activeIncidents > 0 ? 'error' : 'default'}
        />
        <MetricCard
          label="Highest risk"
          value={overview?.highestRisk ? overview.highestRisk.deviceId : 'None'}
          sub={overview?.highestRisk ? `confidence ${(overview.highestRisk.score * 100).toFixed(0)}%` : undefined}
          tone={overview?.highestRisk ? 'warning' : 'default'}
        />
        <MetricCard
          label="Nearest time to impact"
          value={
            overview && overview.nearestTimeToImpactSeconds != null
              ? secondsToEta(overview.nearestTimeToImpactSeconds)
              : 'None'
          }
          tone={overview && overview.nearestTimeToImpactSeconds != null ? 'error' : 'default'}
        />
      </div>

      <h3 className={styles.sectionTitle}>Active incidents</h3>
      {incidents.length === 0 ? (
        <p className={styles.empty}>No active incidents right now.</p>
      ) : (
        <ul className={styles.incidentList}>
          {incidents.map((incident) => (
            <li key={incident.id} className={styles.incidentItem}>
              <span className={styles.incidentStatus}>{incident.status}</span>
              <span className={styles.incidentDevices}>{incident.deviceIds.join(', ')}</span>
              <span>{incident.summary}</span>
            </li>
          ))}
        </ul>
      )}
    </PluginPage>
  );
}

const getStyles = (theme: GrafanaTheme2) => ({
  cardRow: css`
    display: flex;
    flex-wrap: wrap;
    gap: ${theme.spacing(2)};
    margin: ${theme.spacing(2)} 0;
  `,
  sectionTitle: css`
    margin-top: ${theme.spacing(3)};
  `,
  empty: css`
    color: ${theme.colors.text.secondary};
  `,
  incidentList: css`
    list-style: none;
    padding: 0;
    margin: 0;
    display: flex;
    flex-direction: column;
    gap: ${theme.spacing(1)};
  `,
  incidentItem: css`
    display: flex;
    gap: ${theme.spacing(1.5)};
    padding: ${theme.spacing(1)};
    background: ${theme.colors.background.secondary};
    border: 1px solid ${theme.colors.border.weak};
    border-radius: ${theme.shape.radius.default};
  `,
  incidentStatus: css`
    text-transform: uppercase;
    font-size: ${theme.typography.bodySmall.fontSize};
    font-weight: ${theme.typography.fontWeightMedium};
    color: ${theme.colors.warning.text};
    min-width: 60px;
  `,
  incidentDevices: css`
    font-family: ${theme.typography.fontFamilyMonospace};
    color: ${theme.colors.text.secondary};
    min-width: 120px;
  `,
});
