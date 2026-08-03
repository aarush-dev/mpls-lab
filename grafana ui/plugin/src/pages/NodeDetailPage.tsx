import React, { useEffect, useMemo, useState } from 'react';
import { useParams, useHistory } from 'react-router-dom';
import { css } from '@emotion/css';
import { PluginPage } from '@grafana/runtime';
import { GrafanaTheme2, SelectableValue } from '@grafana/data';
import { useStyles2, Link, Select } from '@grafana/ui';

import { TimeSeriesPanel } from '../components/TimeSeriesPanel';
import { EmptyState } from '../components/EmptyState';
import { ErrorState } from '../components/ErrorState';
import { nodeDetailPath } from '../constants';
import { useAppState } from '../state/AppContext';
import { useDataClient } from '../data/DataClientContext';
import { Incident, MetricSeries, Prediction, TopologyGraph } from '../data/types';
import { groupSeries } from '../utils/metricGroups';

export function NodeDetailPage() {
  const { id } = useParams<{ id: string }>();
  const history = useHistory();
  const styles = useStyles2(getStyles);
  const { cursor, injectedFaults } = useAppState();
  const dataClient = useDataClient();

  const [telemetry, setTelemetry] = useState<MetricSeries[] | null>(null);
  const [topology, setTopology] = useState<TopologyGraph | null>(null);
  const [incidents, setIncidents] = useState<Incident[]>([]);
  const [predictions, setPredictions] = useState<Prediction[]>([]);
  const [error, setError] = useState(false);
  const [attempt, setAttempt] = useState(0);

  // Clear telemetry only when the selected device changes (not on clock ticks) so switching nodes
  // shows a clean "Loading…" instead of the previous node's charts, without per-tick blinking.
  useEffect(() => {
    setTelemetry(null);
  }, [id]);

  useEffect(() => {
    let cancelled = false;
    setError(false);

    Promise.all([
      dataClient.getTelemetry({ deviceId: id }),
      dataClient.getTopology({}),
      dataClient.getIncidents({ device: id }),
      dataClient.getPredictions({ device: id }),
    ])
      .then(([telemetryResult, topologyResult, incidentsResult, predictionsResult]) => {
        if (cancelled) {
          return;
        }
        setTelemetry(telemetryResult);
        setTopology(topologyResult);
        setIncidents(incidentsResult);
        setPredictions(predictionsResult);
      })
      .catch(() => {
        if (!cancelled) {
          setError(true);
        }
      });

    return () => {
      cancelled = true;
    };
    // injectedFaults: re-fetch when a fault escalates (pending->predicted->down) so the charts +
    // status update live between demo ticks.
  }, [dataClient, cursor, id, attempt, injectedFaults]);

  const node = topology?.nodes.find((n) => n.id === id);
  const neighborIds = useMemo(() => {
    if (!topology) {
      return [];
    }
    const ids = new Set<string>();
    for (const link of topology.links) {
      if (link.source === id) {
        ids.add(link.target);
      } else if (link.target === id) {
        ids.add(link.source);
      }
    }
    return Array.from(ids);
  }, [topology, id]);

  const activeIncident = incidents.find((i) => i.status === 'open' || i.status === 'active');
  const activePrediction = predictions[0];
  const injected = injectedFaults.find((f) => f.node === id);
  const panels = telemetry ? groupSeries(telemetry) : [];

  const loading = telemetry === null && !error;

  const deviceOptions = useMemo<Array<SelectableValue<string>>>(
    () =>
      (topology?.nodes ?? [])
        .map((n) => n.id)
        .sort()
        .map((d) => ({ label: d, value: d })),
    [topology]
  );

  return (
    <PluginPage>
      <div className={styles.titleRow}>
        <h1 className={styles.title}>{id}</h1>
        <Select
          width={30}
          placeholder="Select device…"
          options={deviceOptions}
          value={id}
          onChange={(v: SelectableValue<string>) => v?.value && history.push(nodeDetailPath(v.value))}
        />
      </div>

      {error ? (
        <ErrorState onRetry={() => setAttempt((a) => a + 1)} />
      ) : loading ? (
        <EmptyState message="Loading…" />
      ) : (
        <>
          <div className={styles.header}>
            <span>{node ? `${node.role}${node.pop ? ` · ${node.pop}` : ''}${node.siteType ? ` · ${node.siteType}` : ''}` : 'Unknown device'}</span>
            <span
              className={
                injected?.phase === 'down'
                  ? styles.down
                  : injected || activeIncident || activePrediction
                  ? styles.unhealthy
                  : styles.healthy
              }
            >
              {injected?.phase === 'down'
                ? `Down — ${injected.faultType} (injected)`
                : injected?.phase === 'predicted'
                ? `Predicted ${injected.faultType} in ${injected.leadSec}s`
                : injected?.phase === 'pending'
                ? `Fault arming — ${injected.faultType}`
                : activeIncident
                ? activeIncident.summary
                : activePrediction
                ? `Predicted ${activePrediction.faultType} (${(activePrediction.confidence * 100).toFixed(0)}%)`
                : 'Healthy'}
            </span>
          </div>

          <h3 className={styles.sectionTitle}>Telemetry</h3>
          {panels.length === 0 ? (
            <EmptyState message="No telemetry feed for this device (unmonitored host/core). Pick a monitored device above." />
          ) : (
            <div className={styles.panels}>
              {panels.map((group) => (
                <TimeSeriesPanel key={group.title} title={group.title} series={group.series} />
              ))}
            </div>
          )}

          <h3 className={styles.sectionTitle}>Neighbors</h3>
          {neighborIds.length === 0 ? (
            <span className={styles.empty}>No known neighbors.</span>
          ) : (
            <div className={styles.neighbors}>
              {neighborIds.map((neighborId) => (
                <Link key={neighborId} href={nodeDetailPath(neighborId)} className={styles.neighborLink}>
                  {neighborId}
                </Link>
              ))}
            </div>
          )}
        </>
      )}
    </PluginPage>
  );
}

const getStyles = (theme: GrafanaTheme2) => ({
  titleRow: css`
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: ${theme.spacing(2)};
    flex-wrap: wrap;
  `,
  title: css`
    margin: 0;
  `,
  header: css`
    display: flex;
    flex-direction: column;
    gap: ${theme.spacing(0.5)};
    margin-bottom: ${theme.spacing(2)};
  `,
  healthy: css`
    color: ${theme.colors.success.text};
  `,
  unhealthy: css`
    color: ${theme.colors.warning.text};
  `,
  down: css`
    color: ${theme.colors.error.text};
  `,
  sectionTitle: css`
    margin-top: ${theme.spacing(3)};
  `,
  panels: css`
    display: flex;
    flex-direction: column;
    gap: ${theme.spacing(2)};
  `,
  neighbors: css`
    display: flex;
    flex-wrap: wrap;
    gap: ${theme.spacing(1.5)};
  `,
  neighborLink: css`
    font-family: ${theme.typography.fontFamilyMonospace};
  `,
  empty: css`
    color: ${theme.colors.text.secondary};
  `,
});
