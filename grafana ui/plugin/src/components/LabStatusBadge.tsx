import React, { useEffect, useState } from 'react';
import { css } from '@emotion/css';
import { GrafanaTheme2 } from '@grafana/data';
import { useStyles2 } from '@grafana/ui';

import { useDataClient } from '../data/DataClientContext';

type LabStatus = 'probing' | 'on' | 'off';

const POLL_MS = 10000;

export function LabStatusBadge() {
  const styles = useStyles2(getStyles);
  const dataClient = useDataClient();
  const [status, setStatus] = useState<LabStatus>('probing');

  useEffect(() => {
    let cancelled = false;
    const probe = () => {
      dataClient
        .getCapabilities()
        .then((caps) => {
          if (!cancelled) {
            setStatus(caps.sources.measured ? 'on' : 'off');
          }
        })
        .catch(() => {
          if (!cancelled) {
            setStatus('off');
          }
        });
    };
    probe();
    const id = setInterval(probe, POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [dataClient]);

  const label = status === 'probing' ? 'Lab …' : status === 'on' ? 'Lab ON' : 'Lab OFF';
  const dotClass = status === 'probing' ? styles.dotProbing : status === 'on' ? styles.dotOn : styles.dotOff;

  return (
    <span className={styles.pill}>
      <span className={dotClass} />
      {label}
    </span>
  );
}

const getStyles = (theme: GrafanaTheme2) => ({
  pill: css`
    display: inline-flex;
    align-items: center;
    gap: ${theme.spacing(0.5)};
    padding: ${theme.spacing(0.25)} ${theme.spacing(1)};
    border-radius: ${theme.shape.radius.pill};
    border: 1px solid ${theme.colors.border.weak};
    background: ${theme.colors.background.secondary};
    font-size: ${theme.typography.bodySmall.fontSize};
  `,
  dotProbing: css`
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: ${theme.colors.text.secondary};
  `,
  dotOn: css`
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: ${theme.colors.success.text};
  `,
  dotOff: css`
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: ${theme.colors.error.text};
  `,
});
