import React from 'react';
import { GrafanaTheme2 } from '@grafana/data';
import { useStyles2, useTheme2 } from '@grafana/ui';
import { css } from '@emotion/css';

import { colorForSeverity, Severity } from './severity';

// Horizontal percent meter. Value clamped to [0, max]; color from severity thresholds.
interface Props {
  value: number;
  max?: number;
  width?: number;
  height?: number;
  label?: string;
  /** value fraction (0..1) at/above which the fill turns warning / error */
  thresholds?: { warn: number; crit: number };
}

// Exported pure geometry so value->fill is unit-testable.
export function gaugeGeometry(value: number, max: number, width: number) {
  const pct = max <= 0 ? 0 : Math.max(0, Math.min(1, value / max));
  return { pct, fillWidth: pct * width };
}

function severityFor(pct: number, t: { warn: number; crit: number }): Severity {
  if (pct >= t.crit) {
    return 'high';
  }
  if (pct >= t.warn) {
    return 'med';
  }
  return 'low';
}

export function Gauge({ value, max = 100, width = 120, height = 10, label, thresholds = { warn: 0.7, crit: 0.9 } }: Props) {
  const theme = useTheme2();
  const styles = useStyles2(getStyles);
  const { pct, fillWidth } = gaugeGeometry(value, max, width);
  const color = colorForSeverity(theme, severityFor(pct, thresholds)).main;
  return (
    <div className={styles.wrap}>
      <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} role="img">
        <rect x={0} y={0} width={width} height={height} rx={height / 2} fill={theme.colors.background.primary} />
        <rect x={0} y={0} width={fillWidth} height={height} rx={height / 2} fill={color} />
      </svg>
      {label && <span className={styles.label}>{label}</span>}
    </div>
  );
}

export { Gauge as Meter };

const getStyles = (theme: GrafanaTheme2) => ({
  wrap: css`
    display: inline-flex;
    align-items: center;
    gap: ${theme.spacing(1)};
  `,
  label: css`
    font-size: ${theme.typography.bodySmall.fontSize};
    color: ${theme.colors.text.secondary};
  `,
});
