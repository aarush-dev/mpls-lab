import React from 'react';
import { css } from '@emotion/css';
import { GrafanaTheme2 } from '@grafana/data';
import { useStyles2, Icon, Button } from '@grafana/ui';

// Explicit failure state. Never rendered green — a failed fetch must read as broken, not healthy
// (plan: stale-not-green). Optional retry re-runs the caller's fetch.
export function ErrorState({ message = 'Failed to load data.', onRetry }: { message?: string; onRetry?: () => void }) {
  const styles = useStyles2(getStyles);
  return (
    <div className={styles.wrap}>
      <Icon name="exclamation-triangle" size="xl" />
      <span>{message}</span>
      {onRetry && (
        <Button variant="secondary" size="sm" onClick={onRetry}>
          Retry
        </Button>
      )}
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
    color: ${theme.colors.error.text};
  `,
});
