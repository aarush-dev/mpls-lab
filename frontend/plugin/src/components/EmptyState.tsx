import React from 'react';
import { css } from '@emotion/css';
import { GrafanaTheme2 } from '@grafana/data';
import { useStyles2, Icon } from '@grafana/ui';

// Neutral "nothing to show" state. Not an error — used when a filter/cursor yields no rows.
export function EmptyState({ message = 'No data for the current view.' }: { message?: string }) {
  const styles = useStyles2(getStyles);
  return (
    <div className={styles.wrap}>
      <Icon name="search" size="xl" />
      <span>{message}</span>
    </div>
  );
}

const getStyles = (theme: GrafanaTheme2) => ({
  wrap: css`
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: ${theme.spacing(1)};
    padding: ${theme.spacing(4)};
    color: ${theme.colors.text.secondary};
  `,
});
