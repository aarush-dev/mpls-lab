import React from 'react';
import { css, cx } from '@emotion/css';
import { GrafanaTheme2 } from '@grafana/data';
import { useStyles2 } from '@grafana/ui';

// Card chrome shared by every page. Optional header with a right-aligned action slot.
interface Props {
  title?: React.ReactNode;
  action?: React.ReactNode;
  className?: string;
  children: React.ReactNode;
}

export function Panel({ title, action, className, children }: Props) {
  const styles = useStyles2(getStyles);
  return (
    <div className={cx(styles.panel, className)}>
      {(title || action) && (
        <div className={styles.header}>
          {title && <div className={styles.title}>{title}</div>}
          {action && <div className={styles.action}>{action}</div>}
        </div>
      )}
      {children}
    </div>
  );
}

const getStyles = (theme: GrafanaTheme2) => ({
  panel: css`
    background: ${theme.colors.background.secondary};
    border: 1px solid ${theme.colors.border.weak};
    border-radius: ${theme.shape.radius.default};
    padding: ${theme.spacing(2)};
  `,
  header: css`
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: ${theme.spacing(1)};
    margin-bottom: ${theme.spacing(1.5)};
  `,
  title: css`
    font-weight: ${theme.typography.fontWeightMedium};
  `,
  action: css`
    display: flex;
    align-items: center;
    gap: ${theme.spacing(1)};
  `,
});
