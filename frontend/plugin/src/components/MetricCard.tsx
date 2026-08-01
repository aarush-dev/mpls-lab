import React from 'react';
import { css } from '@emotion/css';
import { GrafanaTheme2 } from '@grafana/data';
import { useStyles2 } from '@grafana/ui';
import { brand } from '../brand';

interface Props {
  label: string;
  value: string;
  sub?: string;
  /** Optional accent (e.g. 'red'/'amber') to draw attention to a degraded/at-risk stat. */
  tone?: 'default' | 'warning' | 'error';
}

const getStyles = (theme: GrafanaTheme2) => ({
  card: css({
    display: 'flex',
    flexDirection: 'column',
    gap: theme.spacing(0.5),
    padding: theme.spacing(2),
    background: brand.bg2,
    border: `1px solid ${brand.border}`,
    borderRadius: theme.shape.radius.default,
    minWidth: 160,
    flex: '1 1 160px',
  }),
  label: css({
    fontSize: theme.typography.bodySmall.fontSize,
    color: brand.textDim,
    textTransform: 'uppercase',
    letterSpacing: '0.02em',
  }),
  value: css({
    fontSize: theme.typography.h2.fontSize,
    fontWeight: theme.typography.fontWeightMedium,
    lineHeight: 1.2,
  }),
  valueWarning: css({
    color: theme.colors.warning.text,
  }),
  valueError: css({
    color: theme.colors.error.text,
  }),
  sub: css({
    fontSize: theme.typography.bodySmall.fontSize,
    color: brand.textDim,
  }),
});

export function MetricCard({ label, value, sub, tone = 'default' }: Props) {
  const styles = useStyles2(getStyles);
  const valueClass =
    tone === 'error' ? `${styles.value} ${styles.valueError}` : tone === 'warning' ? `${styles.value} ${styles.valueWarning}` : styles.value;

  return (
    <div className={styles.card}>
      <span className={styles.label}>{label}</span>
      <span className={valueClass}>{value}</span>
      {sub && <span className={styles.sub}>{sub}</span>}
    </div>
  );
}
