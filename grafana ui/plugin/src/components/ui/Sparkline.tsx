import React from 'react';
import { useTheme2 } from '@grafana/ui';

// Leaf SVG trend cell. Nulls break the line into segments (no straight-line lie across gaps).
interface Props {
  values: Array<number | null>;
  width?: number;
  height?: number;
  color?: string;
  strokeWidth?: number;
}

// Exported pure so the path is unit-testable without a DOM. Maps values to a "M..L.." path,
// splitting on nulls. Flat series render as a mid-height line.
export function sparkPath(values: Array<number | null>, width: number, height: number): string {
  const nums = values.filter((v): v is number => v != null);
  if (nums.length === 0) {
    return '';
  }
  const min = Math.min(...nums);
  const max = Math.max(...nums);
  const span = max - min || 1;
  const n = values.length;
  const dx = n > 1 ? width / (n - 1) : 0;
  const segments: string[] = [];
  let cur: string[] = [];
  values.forEach((v, i) => {
    if (v == null) {
      if (cur.length) {
        segments.push(cur.join(' '));
        cur = [];
      }
      return;
    }
    const x = dx * i;
    const y = height - ((v - min) / span) * height;
    cur.push(`${cur.length ? 'L' : 'M'}${x.toFixed(1)},${y.toFixed(1)}`);
  });
  if (cur.length) {
    segments.push(cur.join(' '));
  }
  return segments.join(' ');
}

export function Sparkline({ values, width = 80, height = 20, color, strokeWidth = 1.5 }: Props) {
  const theme = useTheme2();
  const d = sparkPath(values, width, height);
  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} role="img" style={{ display: 'block' }}>
      <path d={d} fill="none" stroke={color ?? theme.colors.text.link} strokeWidth={strokeWidth} />
    </svg>
  );
}
