import React from 'react';
import { css } from '@emotion/css';
import { GrafanaTheme2 } from '@grafana/data';
import { useStyles2, useTheme2 } from '@grafana/ui';

import { colorForState, State } from './severity';

// Aggregate count bar: one proportional segment per state, worst-first, with a count legend.
type Counts = Partial<Record<State, number>>;

interface Props {
  counts: Counts;
  height?: number;
}

const ORDER: State[] = ['red', 'amber', 'green', 'unknown'];

export function SeverityStrip({ counts, height = 8 }: Props) {
  const styles = useStyles2(getStyles);
  const theme = useTheme2();
  const total = ORDER.reduce((sum, s) => sum + (counts[s] ?? 0), 0);

  return (
    <div>
      <div className={styles.bar} style={{ height }}>
        {total === 0
          ? null
          : ORDER.filter((s) => (counts[s] ?? 0) > 0).map((s) => (
              <span key={s} style={{ flex: counts[s], background: colorForState(theme, s).main }} title={`${s}: ${counts[s]}`} />
            ))}
      </div>
      <div className={styles.legend}>
        {ORDER.filter((s) => (counts[s] ?? 0) > 0).map((s) => (
          <span key={s} className={styles.item}>
            <span className={styles.dot} style={{ background: colorForState(theme, s).main }} />
            {counts[s]} {s}
          </span>
        ))}
      </div>
    </div>
  );
}

const getStyles = (theme: GrafanaTheme2) => ({
  bar: css`
    display: flex;
    width: 100%;
    border-radius: ${theme.shape.radius.pill};
    overflow: hidden;
    background: ${theme.colors.background.primary};
  `,
  legend: css`
    display: flex;
    flex-wrap: wrap;
    gap: ${theme.spacing(1.5)};
    margin-top: ${theme.spacing(0.5)};
    font-size: ${theme.typography.bodySmall.fontSize};
    color: ${theme.colors.text.secondary};
  `,
  item: css`
    display: inline-flex;
    align-items: center;
    gap: ${theme.spacing(0.5)};
  `,
  dot: css`
    width: 8px;
    height: 8px;
    border-radius: 50%;
    display: inline-block;
  `,
});
