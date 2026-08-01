import React, { useEffect, useMemo, useState } from 'react';
import { css } from '@emotion/css';
import { PluginPage } from '@grafana/runtime';
import { GrafanaTheme2, SelectableValue } from '@grafana/data';
import { useStyles2, Select, MultiSelect } from '@grafana/ui';

import { TimeSeriesPanel, FaultOverlay } from '../components/TimeSeriesPanel';
import { EmptyState } from '../components/EmptyState';
import { ErrorState } from '../components/ErrorState';
import { useAppState } from '../state/AppContext';
import { useDataClient } from '../data/DataClientContext';
import { MetricSeries, TopologyNode } from '../data/types';
import { brand } from '../brand';

const ERROR_COUNTER_KEYS = ['interface_if_in_errors', 'if_in_discards', 'if_out_errors'];

// Group series into a few charts by metric family so related lines share an axis.
function groupSeries(series: MetricSeries[]): Array<{ title: string; unit?: string; series: MetricSeries[] }> {
  const groups: Array<{ title: string; unit?: string; series: MetricSeries[] }> = [];
  const bucket = (title: string, test: (s: MetricSeries) => boolean) => {
    const matched = series.filter(test);
    if (matched.length) {
      groups.push({ title, unit: matched[0].unit, series: matched });
    }
  };
  bucket('CPU / memory', (s) => /cpu|mem/i.test(s.key));
  bucket('Interface throughput', (s) => /if_(in|out)_(octets|bps|bytes)/i.test(s.key));
  bucket('Interface errors', (s) => ERROR_COUNTER_KEYS.includes(s.key));
  bucket('Tunnel latency / jitter / loss', (s) => /tunnel|latency|jitter|loss/i.test(s.key));
  const known = new Set(groups.flatMap((g) => g.series));
  const rest = series.filter((s) => !known.has(s));
  if (rest.length) {
    groups.push({ title: 'Other metrics', unit: rest[0].unit, series: rest });
  }
  return groups;
}

export function TelemetryPage() {
  const styles = useStyles2(getStyles);
  const { cursor, filters } = useAppState();
  const dataClient = useDataClient();

  const [nodes, setNodes] = useState<TopologyNode[]>([]);
  const [selectedDevice, setSelectedDevice] = useState<string | undefined>(undefined);
  const [selectedKeys, setSelectedKeys] = useState<string[]>([]);
  const [series, setSeries] = useState<MetricSeries[] | null>(null);
  const [error, setError] = useState(false);
  const [retryTick, setRetryTick] = useState(0);
  const [overlays, setOverlays] = useState<FaultOverlay[]>([]);

  useEffect(() => {
    let cancelled = false;
    dataClient.getTopology({}).then((graph) => {
      if (!cancelled) {
        setNodes(graph.nodes);
      }
    });
    return () => {
      cancelled = true;
    };
  }, [dataClient]);

  useEffect(() => {
    if (!selectedDevice) {
      setSeries(null);
      setOverlays([]);
      return undefined;
    }
    let cancelled = false;
    setError(false);

    dataClient
      .getTelemetry({ deviceId: selectedDevice, keys: selectedKeys.length ? selectedKeys : undefined })
      .then((result) => {
        if (!cancelled) {
          setSeries(result);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setError(true);
        }
      });

    dataClient.getIncidents({ device: selectedDevice }).then((incidents) => {
      if (cancelled) {
        return;
      }
      const bands: FaultOverlay[] = incidents
        .filter((i) => i.deviceIds.includes(selectedDevice))
        .map((i) => {
          const fromMs = Date.parse(i.startedAt);
          const toMs = Date.parse(i.endedAt ?? i.impactAt ?? i.startedAt);
          return { fromMs, toMs: Number.isNaN(toMs) ? fromMs : toMs, label: i.faultType };
        })
        .filter((b) => !Number.isNaN(b.fromMs));
      setOverlays(bands);
    });

    return () => {
      cancelled = true;
    };
  }, [dataClient, cursor, filters, selectedDevice, selectedKeys, retryTick]);

  const deviceOptions: Array<SelectableValue<string>> = useMemo(
    () => nodes.map((n) => ({ label: n.id, value: n.id })),
    [nodes]
  );

  const keyOptions: Array<SelectableValue<string>> = useMemo(
    () => (series ?? []).map((s) => ({ label: s.label, value: s.key })),
    [series]
  );

  const groups = useMemo(() => groupSeries(series ?? []), [series]);
  const hasErrorCounter = (series ?? []).some((s) => ERROR_COUNTER_KEYS.includes(s.key));

  return (
    <PluginPage>
      <h1>Telemetry</h1>
      <div className={styles.controls}>
        <Select
          placeholder="Select device"
          options={deviceOptions}
          value={selectedDevice ? { label: selectedDevice, value: selectedDevice } : null}
          onChange={(v) => {
            setSelectedDevice(v?.value);
            setSelectedKeys([]);
          }}
          width={32}
        />
        <MultiSelect
          placeholder="All metrics"
          options={keyOptions}
          value={selectedKeys}
          onChange={(vals) => setSelectedKeys(vals.map((v) => v.value as string).filter((v): v is string => !!v))}
          width={40}
          disabled={!selectedDevice}
        />
      </div>

      {!selectedDevice && <EmptyState message="Select a device to view telemetry." />}
      {selectedDevice && error && (
        <ErrorState message="Failed to load telemetry." onRetry={() => setRetryTick((t) => t + 1)} />
      )}
      {selectedDevice && !error && series !== null && series.length === 0 && (
        <EmptyState message={`No telemetry available for ${selectedDevice}.`} />
      )}
      {selectedDevice && !error && groups.length > 0 && (
        <div className={styles.chartCol}>
          {hasErrorCounter && <p className={styles.caption}>Interface error counters are 0 (virtual links).</p>}
          {groups.map((g) => (
            <TimeSeriesPanel key={g.title} title={g.title} series={g.series} unit={g.unit} overlays={overlays} />
          ))}
        </div>
      )}
    </PluginPage>
  );
}

const getStyles = (theme: GrafanaTheme2) => ({
  controls: css`
    display: flex;
    gap: ${theme.spacing(2)};
    margin: ${theme.spacing(2)} 0;
  `,
  chartCol: css`
    display: flex;
    flex-direction: column;
    gap: ${theme.spacing(2)};
  `,
  caption: css`
    color: ${brand.textDim};
    font-size: ${theme.typography.bodySmall.fontSize};
    margin: 0;
  `,
});
