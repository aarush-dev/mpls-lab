import React, { useMemo } from 'react';
import { css } from '@emotion/css';
import { GrafanaTheme2 } from '@grafana/data';
import { useStyles2, useTheme2 } from '@grafana/ui';

import { MetricSeries } from '../data/types';
import { formatUtc } from '../utils/time';

// ponytail: native SVG line chart, no charting dep. Multi-series, null-gap aware, with optional
// fault-overlay bands. Deterministic (pure function of props) so it plays cleanly under the clock.
export interface FaultOverlay {
  fromMs: number;
  toMs: number;
  label?: string;
}

interface Props {
  title?: string;
  series: MetricSeries[];
  overlays?: FaultOverlay[];
  height?: number;
  unit?: string;
}

const PALETTE = ['#73bf69', '#f2cc0c', '#ff780a', '#5794f2', '#b877d9', '#ff9830', '#8ab8ff'];
const PAD = { top: 12, right: 16, bottom: 22, left: 44 };

export function TimeSeriesPanel({ title, series, overlays = [], height = 200, unit }: Props) {
  const styles = useStyles2(getStyles);
  const theme = useTheme2();
  const width = 640;

  const model = useMemo(() => {
    const pts = series.flatMap((s) => s.points);
    const xs = pts.map((p) => p.tMs);
    const ys = pts.filter((p) => p.value != null).map((p) => p.value as number);
    if (xs.length === 0 || ys.length === 0) {
      return null;
    }
    const xMin = Math.min(...xs);
    const xMax = Math.max(...xs);
    let yMin = Math.min(...ys);
    let yMax = Math.max(...ys);
    if (yMin === yMax) {
      yMin -= 1;
      yMax += 1;
    }
    const xSpan = xMax - xMin || 1;
    const ySpan = yMax - yMin || 1;
    const px = (t: number) => PAD.left + ((t - xMin) / xSpan) * (width - PAD.left - PAD.right);
    const py = (v: number) => PAD.top + (1 - (v - yMin) / ySpan) * (height - PAD.top - PAD.bottom);
    return { xMin, xMax, yMin, yMax, px, py };
  }, [series, height]);

  if (!model) {
    return (
      <div className={styles.wrap}>
        {title && <div className={styles.title}>{title}</div>}
        <div className={styles.empty}>No samples in range.</div>
      </div>
    );
  }

  const { px, py, yMin, yMax } = model;

  // Split each series into contiguous (non-null) segments so gaps render as breaks, not straight lines.
  const paths = series.map((s, si) => {
    const segments: string[] = [];
    let cur: string[] = [];
    for (const p of s.points) {
      if (p.value == null) {
        if (cur.length) {
          segments.push(cur.join(' '));
          cur = [];
        }
        continue;
      }
      cur.push(`${cur.length ? 'L' : 'M'}${px(p.tMs).toFixed(1)},${py(p.value).toFixed(1)}`);
    }
    if (cur.length) {
      segments.push(cur.join(' '));
    }
    return { color: PALETTE[si % PALETTE.length], d: segments.join(' '), label: s.label };
  });

  return (
    <div className={styles.wrap}>
      {title && <div className={styles.title}>{title}</div>}
      <svg viewBox={`0 0 ${width} ${height}`} className={styles.svg} preserveAspectRatio="none" role="img">
        {/* fault overlay bands */}
        {overlays.map((o, i) => {
          const x0 = px(o.fromMs);
          const x1 = px(o.toMs);
          return (
            <rect
              key={i}
              x={Math.min(x0, x1)}
              y={PAD.top}
              width={Math.max(2, Math.abs(x1 - x0))}
              height={height - PAD.top - PAD.bottom}
              fill={theme.colors.error.main}
              opacity={0.12}
            />
          );
        })}
        {/* y axis min/max ticks */}
        <line x1={PAD.left} y1={PAD.top} x2={PAD.left} y2={height - PAD.bottom} stroke={theme.colors.border.weak} />
        <line x1={PAD.left} y1={height - PAD.bottom} x2={width - PAD.right} y2={height - PAD.bottom} stroke={theme.colors.border.weak} />
        <text x={4} y={PAD.top + 4} className={styles.axis} fill={theme.colors.text.secondary}>
          {yMax.toFixed(0)}{unit ?? ''}
        </text>
        <text x={4} y={height - PAD.bottom} className={styles.axis} fill={theme.colors.text.secondary}>
          {yMin.toFixed(0)}{unit ?? ''}
        </text>
        <text x={PAD.left} y={height - 6} className={styles.axis} fill={theme.colors.text.secondary}>
          {formatUtc(model.xMin)}
        </text>
        <text x={width - PAD.right} y={height - 6} textAnchor="end" className={styles.axis} fill={theme.colors.text.secondary}>
          {formatUtc(model.xMax)}
        </text>
        {/* series lines */}
        {paths.map((p, i) => (
          <path key={i} d={p.d} fill="none" stroke={p.color} strokeWidth={1.5} />
        ))}
      </svg>
      <div className={styles.legend}>
        {paths.map((p, i) => (
          <span key={i} className={styles.legendItem}>
            <span className={styles.swatch} style={{ background: p.color }} />
            {p.label}
          </span>
        ))}
      </div>
    </div>
  );
}

const getStyles = (theme: GrafanaTheme2) => ({
  wrap: css`
    background: ${theme.colors.background.secondary};
    border: 1px solid ${theme.colors.border.weak};
    border-radius: ${theme.shape.radius.default};
    padding: ${theme.spacing(1.5)};
  `,
  title: css`
    font-weight: ${theme.typography.fontWeightMedium};
    margin-bottom: ${theme.spacing(1)};
  `,
  svg: css`
    width: 100%;
    height: auto;
    display: block;
  `,
  empty: css`
    color: ${theme.colors.text.secondary};
    padding: ${theme.spacing(3)};
    text-align: center;
  `,
  axis: css`
    font-size: 10px;
  `,
  legend: css`
    display: flex;
    flex-wrap: wrap;
    gap: ${theme.spacing(1.5)};
    margin-top: ${theme.spacing(1)};
    font-size: ${theme.typography.bodySmall.fontSize};
    color: ${theme.colors.text.secondary};
  `,
  legendItem: css`
    display: inline-flex;
    align-items: center;
    gap: ${theme.spacing(0.5)};
  `,
  swatch: css`
    width: 10px;
    height: 10px;
    border-radius: 2px;
    display: inline-block;
  `,
});
