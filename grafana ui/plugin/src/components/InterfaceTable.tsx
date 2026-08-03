import React, { useMemo } from 'react';
import { css } from '@emotion/css';
import { GrafanaTheme2 } from '@grafana/data';
import { useStyles2 } from '@grafana/ui';

import { MetricSeries } from '../data/types';

interface Props {
  series: MetricSeries[];
}

export function lastValue(series?: MetricSeries): number | null {
  if (!series) {
    return null;
  }
  for (let i = series.points.length - 1; i >= 0; i--) {
    const v = series.points[i].value;
    if (v != null) {
      return v;
    }
  }
  return null;
}

export function humanRate(bps: number): string {
  const units = ['bps', 'Kbps', 'Mbps', 'Gbps'];
  let v = Math.abs(bps);
  let i = 0;
  while (v >= 1000 && i < units.length - 1) {
    v /= 1000;
    i++;
  }
  return `${i === 0 ? v.toFixed(0) : v.toFixed(2)} ${units[i]}`;
}

// Series keys are `<device>:<interface>:<metric>` (interface-scoped) or `<device>:<metric>`
// (device-level, skipped). Metric names of interest below.
const METRICS = [
  'interface_ifOperStatus',
  'interface_ifInErrors',
  'interface_ifOutErrors',
  'interface_ifInDiscards',
  'interface_ifOutDiscards',
  'interface_ifInOctets',
  'interface_ifOutOctets',
] as const;

interface Row {
  iface: string;
  status: number | null;
  inErrors: number | null;
  outErrors: number | null;
  inDiscards: number | null;
  outDiscards: number | null;
  rxBps: number | null;
  txBps: number | null;
}

function buildRows(series: MetricSeries[]): Row[] {
  // key -> metric name -> series, grouped by interface.
  const byIface = new Map<string, Map<string, MetricSeries>>();
  for (const s of series) {
    const parts = s.key.split(':');
    if (parts.length < 3) {
      continue; // device-level series, not interface-scoped
    }
    const iface = parts[1];
    const metric = parts[parts.length - 1];
    if (!METRICS.includes(metric as (typeof METRICS)[number])) {
      continue;
    }
    if (!byIface.has(iface)) {
      byIface.set(iface, new Map());
    }
    byIface.get(iface)!.set(metric, s);
  }

  const rows: Row[] = [];
  for (const [iface, metrics] of byIface) {
    const inOctets = lastValue(metrics.get('interface_ifInOctets'));
    const outOctets = lastValue(metrics.get('interface_ifOutOctets'));
    rows.push({
      iface,
      status: lastValue(metrics.get('interface_ifOperStatus')),
      inErrors: lastValue(metrics.get('interface_ifInErrors')),
      outErrors: lastValue(metrics.get('interface_ifOutErrors')),
      inDiscards: lastValue(metrics.get('interface_ifInDiscards')),
      outDiscards: lastValue(metrics.get('interface_ifOutDiscards')),
      rxBps: inOctets != null ? inOctets * 8 : null,
      txBps: outOctets != null ? outOctets * 8 : null,
    });
  }
  return rows.sort((a, b) => a.iface.localeCompare(b.iface));
}

export function InterfaceTable({ series }: Props) {
  const styles = useStyles2(getStyles);
  const rows = useMemo(() => buildRows(series), [series]);

  if (rows.length === 0) {
    return null;
  }

  return (
    <table className={styles.table}>
      <thead>
        <tr>
          <th>Interface</th>
          <th>Oper status</th>
          <th className={styles.num}>In errors/s</th>
          <th className={styles.num}>Out errors/s</th>
          <th className={styles.num}>In discards/s</th>
          <th className={styles.num}>Out discards/s</th>
          <th className={styles.num}>RX</th>
          <th className={styles.num}>TX</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.iface}>
            <td className={styles.mono}>{r.iface}</td>
            <td className={r.status === 1 ? styles.up : styles.down}>{r.status === 1 ? 'up' : r.status != null ? 'down' : '—'}</td>
            <td className={styles.num}>{r.inErrors ?? '—'}</td>
            <td className={styles.num}>{r.outErrors ?? '—'}</td>
            <td className={styles.num}>{r.inDiscards ?? '—'}</td>
            <td className={styles.num}>{r.outDiscards ?? '—'}</td>
            <td className={styles.num}>{r.rxBps != null ? humanRate(r.rxBps) : '—'}</td>
            <td className={styles.num}>{r.txBps != null ? humanRate(r.txBps) : '—'}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

const getStyles = (theme: GrafanaTheme2) => ({
  table: css`
    width: 100%;
    border-collapse: collapse;

    th {
      text-align: left;
      padding: ${theme.spacing(1)};
      color: ${theme.colors.text.secondary};
      border-bottom: 1px solid ${theme.colors.border.weak};
      font-weight: ${theme.typography.fontWeightMedium};
    }
    td {
      padding: ${theme.spacing(1)};
      border-bottom: 1px solid ${theme.colors.border.weak};
    }
  `,
  num: css`
    text-align: right;
  `,
  mono: css`
    font-family: ${theme.typography.fontFamilyMonospace};
  `,
  up: css`
    color: ${theme.colors.success.text};
  `,
  down: css`
    color: ${theme.colors.error.text};
  `,
});
