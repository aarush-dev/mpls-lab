import React from 'react';
import { css } from '@emotion/css';
import { GrafanaTheme2 } from '@grafana/data';
import { useStyles2, useTheme2 } from '@grafana/ui';

import { colorForState, State } from './severity';

// NxN device-health matrix. Near-square: cols = ceil(sqrt(n)). Each cell colored by state.
export interface DeviceHealth {
  id: string;
  state?: State;
}

interface Props {
  devices: DeviceHealth[];
  onSelect?: (id: string) => void;
  cell?: number;
}

export function StatusGrid({ devices, onSelect, cell = 18 }: Props) {
  const styles = useStyles2(getStyles);
  const theme = useTheme2();
  const cols = Math.max(1, Math.ceil(Math.sqrt(devices.length)));
  return (
    <div className={styles.grid} style={{ gridTemplateColumns: `repeat(${cols}, ${cell}px)` }}>
      {devices.map((d) => (
        <span
          key={d.id}
          className={styles.cell}
          style={{ width: cell, height: cell, background: colorForState(theme, d.state).main, cursor: onSelect ? 'pointer' : 'default' }}
          title={`${d.id}: ${d.state ?? 'unknown'}`}
          onClick={onSelect ? () => onSelect(d.id) : undefined}
        />
      ))}
    </div>
  );
}

const getStyles = (theme: GrafanaTheme2) => ({
  grid: css`
    display: grid;
    gap: ${theme.spacing(0.5)};
  `,
  cell: css`
    border-radius: 2px;
    display: inline-block;
  `,
});
