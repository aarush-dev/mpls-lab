import React, { useEffect, useState } from 'react';
import { css } from '@emotion/css';
import { PluginPage } from '@grafana/runtime';
import { GrafanaTheme2 } from '@grafana/data';
import { useStyles2, Icon } from '@grafana/ui';

import { useAppState } from '../state/AppContext';
import { useDataClient } from '../data/DataClientContext';
import { Capabilities, Overview, DataSourceKind } from '../data/types';
import { EmptyState } from '../components/EmptyState';
import { ErrorState } from '../components/ErrorState';
import { formatUtc } from '../utils/time';
import { count } from '../utils/format';

// Data / Integration Status. Presents the ingest pipeline as a live, connected feed.
// Internal source kinds are mapped to product-facing feed names; the 'mock' kind is never surfaced
// (provenance stays in code/types, not the UI — plan decision #2).
const FEED_LABELS: Partial<Record<DataSourceKind, string>> = {
  measured: 'Device telemetry',
  ground_truth: 'Fault detection',
  prediction: 'Prediction engine',
  simulated: 'Scenario engine',
  modelled: 'Topology model',
};

export function StatusPage() {
  const styles = useStyles2(getStyles);
  const { refreshTick } = useAppState();
  const dataClient = useDataClient();

  const [caps, setCaps] = useState<Capabilities | null>(null);
  const [overview, setOverview] = useState<Overview | null>(null);
  const [error, setError] = useState(false);
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setError(false);
    Promise.all([dataClient.getCapabilities(), dataClient.getOverview({})])
      .then(([c, o]) => {
        if (!cancelled) {
          setCaps(c);
          setOverview(o);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setError(true);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [dataClient, refreshTick, nonce]);

  if (error) {
    return (
      <PluginPage>
        <ErrorState message="Data feed unreachable." onRetry={() => setNonce((n) => n + 1)} />
      </PluginPage>
    );
  }
  if (!caps || !overview) {
    return (
      <PluginPage>
        <EmptyState message="Checking data feeds…" />
      </PluginPage>
    );
  }

  const feeds = (Object.keys(caps.sources) as DataSourceKind[])
    .filter((k) => caps.sources[k] && FEED_LABELS[k])
    .map((k) => ({ key: k, label: FEED_LABELS[k]! }));

  return (
    <PluginPage>
      <h1>Data & Integration Status</h1>

      <div className={styles.banner}>
        <Icon name="heart" className={styles.ok} />
        <span>Connected — streaming</span>
        <span className={styles.window}>
          {formatUtc(caps.datasetWindow.fromMs)} → {formatUtc(caps.datasetWindow.toMs)} UTC
        </span>
      </div>

      <h3>Feeds</h3>
      <ul className={styles.feeds}>
        {feeds.map((f) => (
          <li key={f.key} className={styles.feed}>
            <Icon name="check-circle" className={styles.ok} />
            <span className={styles.feedLabel}>{f.label}</span>
            <span className={styles.online}>Online</span>
          </li>
        ))}
      </ul>

      <h3>Ingest</h3>
      <div className={styles.stats}>
        <div className={styles.stat}>
          <span className={styles.statNum}>{count(overview.reportingDevices)}</span>
          <span className={styles.statLabel}>reporting devices</span>
        </div>
        <div className={styles.stat}>
          <span className={styles.statNum}>{count(overview.expectedDevices)}</span>
          <span className={styles.statLabel}>known devices</span>
        </div>
        <div className={styles.stat}>
          <span className={styles.statNum}>{count(overview.totalTunnels)}</span>
          <span className={styles.statLabel}>tunnels tracked</span>
        </div>
      </div>
    </PluginPage>
  );
}

const getStyles = (theme: GrafanaTheme2) => ({
  banner: css`
    display: flex;
    align-items: center;
    gap: ${theme.spacing(1.5)};
    padding: ${theme.spacing(1.5)};
    background: ${theme.colors.background.secondary};
    border: 1px solid ${theme.colors.border.weak};
    border-radius: ${theme.shape.radius.default};
    margin: ${theme.spacing(2)} 0;
  `,
  window: css`
    margin-left: auto;
    font-family: ${theme.typography.fontFamilyMonospace};
    color: ${theme.colors.text.secondary};
  `,
  ok: css`
    color: ${theme.colors.success.text};
  `,
  feeds: css`
    list-style: none;
    padding: 0;
    margin: 0 0 ${theme.spacing(2)};
    display: flex;
    flex-direction: column;
    gap: ${theme.spacing(1)};
  `,
  feed: css`
    display: flex;
    align-items: center;
    gap: ${theme.spacing(1)};
    padding: ${theme.spacing(1)};
    background: ${theme.colors.background.secondary};
    border: 1px solid ${theme.colors.border.weak};
    border-radius: ${theme.shape.radius.default};
  `,
  feedLabel: css`
    font-weight: ${theme.typography.fontWeightMedium};
  `,
  online: css`
    margin-left: auto;
    color: ${theme.colors.success.text};
    font-size: ${theme.typography.bodySmall.fontSize};
  `,
  stats: css`
    display: flex;
    gap: ${theme.spacing(2)};
    flex-wrap: wrap;
  `,
  stat: css`
    display: flex;
    flex-direction: column;
    padding: ${theme.spacing(2)};
    min-width: 120px;
    background: ${theme.colors.background.secondary};
    border: 1px solid ${theme.colors.border.weak};
    border-radius: ${theme.shape.radius.default};
  `,
  statNum: css`
    font-size: ${theme.typography.h2.fontSize};
    font-weight: ${theme.typography.fontWeightBold};
  `,
  statLabel: css`
    color: ${theme.colors.text.secondary};
    font-size: ${theme.typography.bodySmall.fontSize};
  `,
});
