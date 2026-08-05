import React, { useLayoutEffect, useMemo, useRef, useState } from 'react';
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
  /** horizontal reference line in data units (e.g. an alert threshold) */
  threshold?: number;
}

const PALETTE = ['#73bf69', '#f2cc0c', '#ff780a', '#5794f2', '#b877d9', '#ff9830', '#8ab8ff'];
const PAD = { top: 12, right: 16, bottom: 22, left: 44 };
const GRID_ROWS = 4;

// ponytail: jsdom has no ResizeObserver and reports clientWidth 0; fall back to this fixed width so
// SSR/tests still render. Upgrade path: none needed — real browsers hit the observer path.
const FALLBACK_WIDTH = 640;

export function TimeSeriesPanel({ title, series, overlays = [], height = 200, unit, threshold }: Props) {
  const styles = useStyles2(getStyles);
  const theme = useTheme2();
  const wrapRef = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(FALLBACK_WIDTH);
  const [cursorX, setCursorX] = useState<number | null>(null);

  useLayoutEffect(() => {
    const el = wrapRef.current;
    if (!el || typeof ResizeObserver === 'undefined') {
      return;
    }
    const ro = new ResizeObserver((entries) => {
      const w = entries[0]?.contentRect.width;
      if (w && w > 0) {
        setWidth(w);
      }
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

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
    if (threshold != null) {
      yMin = Math.min(yMin, threshold);
      yMax = Math.max(yMax, threshold);
    }
    if (yMin === yMax) {
      yMin -= 1;
      yMax += 1;
    }
    const xSpan = xMax - xMin || 1;
    const ySpan = yMax - yMin || 1;
    const px = (t: number) => PAD.left + ((t - xMin) / xSpan) * (width - PAD.left - PAD.right);
    const py = (v: number) => PAD.top + (1 - (v - yMin) / ySpan) * (height - PAD.top - PAD.bottom);
    return { xMin, xMax, yMin, yMax, xSpan, px, py };
  }, [series, height, width, threshold]);

  if (!model) {
    return (
      <div className={styles.wrap} ref={wrapRef}>
        {title && <div className={styles.title}>{title}</div>}
        <div className={styles.empty}>No samples in range.</div>
      </div>
    );
  }

  const { px, py, yMin, yMax, xMin, xSpan } = model;

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

  // Nearest-sample readout under the cursor, per series.
  const tAtCursor = cursorX == null ? null : xMin + ((cursorX - PAD.left) / (width - PAD.left - PAD.right)) * xSpan;
  const readout =
    tAtCursor == null
      ? null
      : series.map((s, si) => {
          let best: { tMs: number; value: number } | null = null;
          for (const p of s.points) {
            if (p.value == null) {
              continue;
            }
            if (!best || Math.abs(p.tMs - tAtCursor) < Math.abs(best.tMs - tAtCursor)) {
              best = { tMs: p.tMs, value: p.value };
            }
          }
          return best ? { color: PALETTE[si % PALETTE.length], label: s.label, ...best } : null;
        });
  const snapTms = readout?.find((r) => r)?.tMs;

  return (
    <div className={styles.wrap} ref={wrapRef}>
      {title && <div className={styles.title}>{title}</div>}
      <svg
        viewBox={`0 0 ${width} ${height}`}
        className={styles.svg}
        preserveAspectRatio="none"
        role="img"
        onMouseMove={(e) => {
          const rect = e.currentTarget.getBoundingClientRect();
          setCursorX(((e.clientX - rect.left) / rect.width) * width);
        }}
        onMouseLeave={() => setCursorX(null)}
      >
        {/* horizontal gridlines */}
        {Array.from({ length: GRID_ROWS + 1 }, (_, i) => {
          const y = PAD.top + (i / GRID_ROWS) * (height - PAD.top - PAD.bottom);
          return <line key={i} x1={PAD.left} y1={y} x2={width - PAD.right} y2={y} stroke={theme.colors.border.weak} opacity={0.5} />;
        })}
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
        {/* optional threshold line */}
        {threshold != null && (
          <line x1={PAD.left} y1={py(threshold)} x2={width - PAD.right} y2={py(threshold)} stroke={theme.colors.warning.main} strokeDasharray="4 3" />
        )}
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
        {/* hover crosshair */}
        {snapTms != null && (
          <line x1={px(snapTms)} y1={PAD.top} x2={px(snapTms)} y2={height - PAD.bottom} stroke={theme.colors.text.secondary} strokeWidth={1} opacity={0.6} />
        )}
      </svg>
      {readout && snapTms != null && (
        <div className={styles.readout}>
          <span className={styles.readoutTime}>{formatUtc(snapTms)}</span>
          {readout.filter(Boolean).map((r, i) => (
            <span key={i} className={styles.legendItem}>
              <span className={styles.swatch} style={{ background: r!.color }} />
              {r!.label}: {r!.value.toFixed(2)}{unit ?? ''}
            </span>
          ))}
        </div>
      )}
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
  readout: css`
    display: flex;
    flex-wrap: wrap;
    gap: ${theme.spacing(1.5)};
    margin-top: ${theme.spacing(0.5)};
    font-size: ${theme.typography.bodySmall.fontSize};
    color: ${theme.colors.text.primary};
  `,
  readoutTime: css`
    color: ${theme.colors.text.secondary};
    font-family: ${theme.typography.fontFamilyMonospace};
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
