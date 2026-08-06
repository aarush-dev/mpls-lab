import React, { useEffect, useMemo, useState } from 'react';
import { useParams, useHistory } from 'react-router-dom';
import { css } from '@emotion/css';
import { PluginPage } from '@grafana/runtime';
import { GrafanaTheme2, SelectableValue } from '@grafana/data';
import { useStyles2, Link, Select, FilterInput } from '@grafana/ui';

import { TimeSeriesPanel } from '../components/TimeSeriesPanel';
import { EmptyState } from '../components/EmptyState';
import { ErrorState } from '../components/ErrorState';
import { InterfaceTable } from '../components/InterfaceTable';
import { FlowTable } from '../components/FlowTable';
import { LogTerminal } from '../components/LogTerminal';
import { nodeDetailPath } from '../constants';
import { useAppState } from '../state/AppContext';
import { useDataClient } from '../data/DataClientContext';
import { FlowRecord, Incident, MetricSeries, NetworkEvent, Prediction, TopologyGraph } from '../data/types';
import { groupSeries } from '../utils/metricGroups';

export function NodeDetailPage() {
  const { id } = useParams<{ id: string }>();
  const history = useHistory();
  const styles = useStyles2(getStyles);
  const { mode, refreshTick, range } = useAppState();
  const dataClient = useDataClient();

  const [telemetry, setTelemetry] = useState<MetricSeries[] | null>(null);
  const [topology, setTopology] = useState<TopologyGraph | null>(null);
  const [incidents, setIncidents] = useState<Incident[]>([]);
  const [predictions, setPredictions] = useState<Prediction[]>([]);
  const [events, setEvents] = useState<NetworkEvent[]>([]);
  const [flows, setFlows] = useState<FlowRecord[]>([]);
  const [error, setError] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [q, setQ] = useState('');

  // Clear telemetry only when the selected device changes (not on refresh) so switching nodes shows
  // a clean "Loading…" instead of the previous node's charts, without per-refresh blinking.
  useEffect(() => {
    setTelemetry(null);
  }, [id]);

  useEffect(() => {
    let cancelled = false;
    setError(false);
    const timeRange = { fromMs: range.fromMs, toMs: range.toMs };
    // Routers log a boot/convergence burst then go quiet; widen the live log window to 60m so
    // it doesn't empty out as the 900s metric window slides past the burst.
    const eventsTimeRange =
      mode === 'live' ? { fromMs: (range.toMs || Date.now()) - 3600_000, toMs: range.toMs || Date.now() } : timeRange;

    Promise.all([
      dataClient.getTelemetry({ deviceId: id, timeRange }),
      dataClient.getTopology({}),
      dataClient.getIncidents({ device: id }),
      dataClient.getPredictions({ device: id }),
      dataClient.getEvents({ device: id, timeRange: eventsTimeRange }),
      dataClient.getFlows({ device: id, timeRange }),
    ])
      .then(([telemetryResult, topologyResult, incidentsResult, predictionsResult, eventsResult, flowsResult]) => {
        if (cancelled) {
          return;
        }
        setTelemetry(telemetryResult);
        setTopology(topologyResult);
        setIncidents(incidentsResult);
        setPredictions(predictionsResult);
        setEvents(eventsResult);
        setFlows(flowsResult);
      })
      .catch(() => {
        if (!cancelled) {
          setError(true);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [dataClient, refreshTick, range.fromMs, range.toMs, mode, id, attempt]);

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
  const panels = telemetry ? groupSeries(telemetry) : [];

  // Live search: filter metric panels + fixed sections by case-insensitive title substring.
  const term = q.trim().toLowerCase();
  const matches = (label: string) => !term || label.toLowerCase().includes(term);
  const shownPanels = panels.filter((p) => matches(p.title));
  const telemetryVisible = panels.length === 0 ? matches('telemetry') : shownPanels.length > 0;
  const anyVisible = telemetryVisible || ['interfaces', 'neighbors', 'packets flows', 'logs'].some((s) => matches(s));

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
            <span className={activeIncident || activePrediction ? styles.unhealthy : styles.healthy}>
              {activeIncident
                ? activeIncident.summary
                : activePrediction
                ? `Predicted ${activePrediction.faultType} (${(activePrediction.confidence * 100).toFixed(0)}%)`
                : 'Healthy'}
            </span>
          </div>

          <FilterInput
            value={q}
            onChange={setQ}
            placeholder="Filter stats / sections…"
            className={styles.search}
          />

          {matches('interfaces') && (
            <>
              <h3 className={styles.sectionTitle}>Interfaces</h3>
              <InterfaceTable series={telemetry ?? []} />
            </>
          )}

          {telemetryVisible && (
            <>
              <h3 className={styles.sectionTitle}>Telemetry</h3>
              {panels.length === 0 ? (
                <EmptyState message="No telemetry feed for this device (unmonitored host/core). Pick a monitored device above." />
              ) : (
                <div className={styles.panels}>
                  {shownPanels.map((group) => (
                    <TimeSeriesPanel key={group.title} title={group.title} series={group.series} unit={group.unit} />
                  ))}
                </div>
              )}
            </>
          )}

          {matches('neighbors') && (
            <>
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

          {matches('packets flows') && (
            <>
              <h3 className={styles.sectionTitle}>Packets / Flows</h3>
              <FlowTable flows={flows} />
            </>
          )}

          {matches('logs') && (
            <>
              <h3 className={styles.sectionTitle}>Logs</h3>
              <LogTerminal events={events} isHost={id.startsWith('h_')} />
            </>
          )}

          {term && !anyVisible && <EmptyState message={`No stats or sections match "${q}".`} />}
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
  search: css`
    max-width: 420px;
    margin-bottom: ${theme.spacing(1)};
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
