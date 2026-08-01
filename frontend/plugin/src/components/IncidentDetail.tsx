import React from 'react';
import { css } from '@emotion/css';
import { GrafanaTheme2 } from '@grafana/data';
import { useStyles2, Drawer, Link } from '@grafana/ui';

import { nodeDetailPath } from '../constants';
import { Incident } from '../data/types';

interface Props {
  incident: Incident;
  onClose: () => void;
}

export function IncidentDetail({ incident, onClose }: Props) {
  const styles = useStyles2(getStyles);

  return (
    <Drawer title={incident.faultType} subtitle={`${incident.status} · ${incident.severity} severity`} onClose={onClose}>
      <p>{incident.summary}</p>

      <h4 className={styles.sectionTitle}>Affected scope</h4>
      {incident.affectedScope.length === 0 ? (
        <span className={styles.empty}>None recorded.</span>
      ) : (
        <div className={styles.chips}>
          {incident.affectedScope.map((id) => (
            <Link key={id} href={nodeDetailPath(id)} className={styles.deviceLink}>
              {id}
            </Link>
          ))}
        </div>
      )}

      <h4 className={styles.sectionTitle}>Evidence</h4>
      {incident.evidence.length === 0 ? (
        <span className={styles.empty}>No evidence recorded.</span>
      ) : (
        <ul className={styles.list}>
          {incident.evidence.map((e, i) => (
            <li key={i}>
              <strong>{e.label}:</strong> {e.detail}
            </li>
          ))}
        </ul>
      )}

      <h4 className={styles.sectionTitle}>Root-cause hypotheses</h4>
      {incident.rootCauseHypotheses.length === 0 ? (
        <span className={styles.empty}>None recorded.</span>
      ) : (
        <ul className={styles.list}>
          {incident.rootCauseHypotheses.map((h, i) => (
            <li key={i}>{h}</li>
          ))}
        </ul>
      )}

      <h4 className={styles.sectionTitle}>Recommended actions</h4>
      {incident.recommendedActions.length === 0 ? (
        <span className={styles.empty}>None recorded.</span>
      ) : (
        <ul className={styles.list}>
          {incident.recommendedActions.map((a, i) => (
            <li key={i}>
              <strong>{a.title}:</strong> {a.detail}
            </li>
          ))}
        </ul>
      )}
    </Drawer>
  );
}

const getStyles = (theme: GrafanaTheme2) => ({
  sectionTitle: css`
    margin-top: ${theme.spacing(2)};
  `,
  empty: css`
    color: ${theme.colors.text.secondary};
  `,
  list: css`
    margin: 0;
    padding-left: ${theme.spacing(2.5)};
  `,
  chips: css`
    display: flex;
    flex-wrap: wrap;
    gap: ${theme.spacing(1)};
  `,
  deviceLink: css`
    font-family: ${theme.typography.fontFamilyMonospace};
  `,
});
