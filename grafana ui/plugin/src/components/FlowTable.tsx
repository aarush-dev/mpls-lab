import React, { useMemo } from 'react';
import { css } from '@emotion/css';
import { GrafanaTheme2 } from '@grafana/data';
import { useStyles2 } from '@grafana/ui';

import { FlowRecord } from '../data/types';
import { MetricCard } from './MetricCard';

interface Props {
  flows: FlowRecord[];
}

const MAX_ROWS = 200;

export function humanBytes(n: number): string {
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let v = Math.abs(n);
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  const sign = n < 0 ? '-' : '';
  return `${sign}${i === 0 ? v.toFixed(0) : v.toFixed(2)} ${units[i]}`;
}

function hhmmss(tsMs: number): string {
  const d = new Date(tsMs);
  const pad = (x: number) => String(x).padStart(2, '0');
  return `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:${pad(d.getUTCSeconds())}`;
}

function addr(ip?: string, port?: number): string {
  if (!ip) {
    return '—';
  }
  return port != null ? `${ip}:${port}` : ip;
}

export function FlowTable({ flows }: Props) {
  const styles = useStyles2(getStyles);

  const { rows, totalBytes, totalPackets, topTalker } = useMemo(() => {
    const sorted = [...flows].sort((a, b) => b.tsMs - a.tsMs).slice(0, MAX_ROWS);
    const totalBytes = flows.reduce((sum, f) => sum + (f.bytes ?? 0), 0);
    const totalPackets = flows.reduce((sum, f) => sum + (f.packets ?? 0), 0);

    const bySrc = new Map<string, number>();
    for (const f of flows) {
      if (!f.ipSrc) {
        continue;
      }
      bySrc.set(f.ipSrc, (bySrc.get(f.ipSrc) ?? 0) + (f.bytes ?? 0));
    }
    let topTalker = '—';
    let topBytes = -1;
    for (const [ip, bytes] of bySrc) {
      if (bytes > topBytes) {
        topBytes = bytes;
        topTalker = ip;
      }
    }
    return { rows: sorted, totalBytes, totalPackets, topTalker };
  }, [flows]);

  return (
    <div>
      <div className={styles.stats}>
        <MetricCard label="Total flows" value={String(flows.length)} />
        <MetricCard label="Total bytes" value={humanBytes(totalBytes)} />
        <MetricCard label="Total packets" value={String(totalPackets)} />
        <MetricCard label="Top talker" value={topTalker} />
      </div>
      <table className={styles.table}>
        <thead>
          <tr>
            <th>Time</th>
            <th>Src</th>
            <th>Dst</th>
            <th>Proto</th>
            <th className={styles.num}>Packets</th>
            <th className={styles.num}>Bytes</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((f, i) => (
            <tr key={i}>
              <td>{hhmmss(f.tsMs)}</td>
              <td className={styles.mono}>{addr(f.ipSrc, f.portSrc)}</td>
              <td className={styles.mono}>{addr(f.ipDst, f.portDst)}</td>
              <td>{f.proto ?? '—'}</td>
              <td className={styles.num}>{f.packets ?? '—'}</td>
              <td className={styles.num}>{f.bytes != null ? humanBytes(f.bytes) : '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const getStyles = (theme: GrafanaTheme2) => ({
  stats: css`
    display: flex;
    flex-wrap: wrap;
    gap: ${theme.spacing(1)};
    margin-bottom: ${theme.spacing(2)};
  `,
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
});
