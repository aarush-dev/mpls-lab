import React from 'react';
import { css } from '@emotion/css';
import { GrafanaTheme2 } from '@grafana/data';
import { useStyles2, Link, Badge, BadgeColor } from '@grafana/ui';

import { nodeDetailPath } from '../constants';
import { formatUtc } from '../utils/time';
import { secondsToEta } from '../utils/format';
import { Incident, Prediction } from '../data/types';
import { brand } from '../brand';

interface Props {
  incidents: Incident[];
  predictions: Prediction[];
  onSelect: (incident: Incident) => void;
  selectedId?: string;
}

const STATUS_RANK: Record<Incident['status'], number> = { active: 0, open: 1, resolved: 2, unknown: 3 };
const SEVERITY_RANK: Record<Incident['severity'], number> = { high: 0, medium: 1, low: 2, unknown: 3 };
const STATUS_COLOR: Record<Incident['status'], BadgeColor> = {
  active: 'red',
  open: 'orange',
  resolved: 'green',
  unknown: 'purple',
};

function sortIncidents(incidents: Incident[]): Incident[] {
  return [...incidents].sort((a, b) => {
    if (STATUS_RANK[a.status] !== STATUS_RANK[b.status]) {
      return STATUS_RANK[a.status] - STATUS_RANK[b.status];
    }
    if (SEVERITY_RANK[a.severity] !== SEVERITY_RANK[b.severity]) {
      return SEVERITY_RANK[a.severity] - SEVERITY_RANK[b.severity];
    }
    return (a.timeToImpactSeconds ?? Infinity) - (b.timeToImpactSeconds ?? Infinity);
  });
}

export function IncidentTable({ incidents, predictions, onSelect, selectedId }: Props) {
  const styles = useStyles2(getStyles);
  const rows = sortIncidents(incidents);

  return (
    <div>
      {predictions.length > 0 && (
        <div className={styles.predictions}>
          <h4 className={styles.sectionTitle}>Predictions</h4>
          <div className={styles.predictionStrip}>
            {predictions.map((p) => (
              <div key={p.id} className={styles.predictionCard}>
                <Link href={nodeDetailPath(p.deviceId)} className={styles.deviceLink}>
                  {p.deviceId}
                </Link>
                <span>{p.faultType}</span>
                <span>{(p.confidence * 100).toFixed(0)}%</span>
                <span>{secondsToEta(p.timeToImpactSeconds)}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      <table className={styles.table}>
        <thead>
          <tr>
            <th>Status</th>
            <th>Severity</th>
            <th>Fault type</th>
            <th>Devices</th>
            <th>Started</th>
            <th>TTI</th>
            <th>Confidence</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((incident) => (
            <tr
              key={incident.id}
              className={incident.id === selectedId ? styles.rowSelected : styles.row}
              onClick={() => onSelect(incident)}
            >
              <td>
                <Badge text={incident.status} color={STATUS_COLOR[incident.status]} />
              </td>
              <td>{incident.severity}</td>
              <td>{incident.faultType}</td>
              <td>
                {incident.deviceIds.length > 0 ? (
                  <Link href={nodeDetailPath(incident.deviceIds[0])} className={styles.deviceLink}>
                    {incident.deviceIds[0]}
                  </Link>
                ) : (
                  '—'
                )}
                {incident.deviceIds.length > 1 ? ` +${incident.deviceIds.length - 1}` : ''}
              </td>
              <td>{formatUtc(Date.parse(incident.startedAt))}</td>
              <td>{incident.timeToImpactSeconds != null ? secondsToEta(incident.timeToImpactSeconds) : '—'}</td>
              <td>{incident.confidence != null ? `${(incident.confidence * 100).toFixed(0)}%` : '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const getStyles = (theme: GrafanaTheme2) => ({
  sectionTitle: css`
    margin: ${theme.spacing(1)} 0;
  `,
  predictions: css`
    margin-bottom: ${theme.spacing(2)};
  `,
  predictionStrip: css`
    display: flex;
    flex-wrap: wrap;
    gap: ${theme.spacing(1)};
  `,
  predictionCard: css`
    display: flex;
    gap: ${theme.spacing(1.5)};
    align-items: center;
    padding: ${theme.spacing(1)};
    background: ${brand.bg2};
    border: 1px solid ${brand.border};
    border-radius: ${theme.shape.radius.default};
    font-size: ${theme.typography.bodySmall.fontSize};
  `,
  table: css`
    width: 100%;
    border-collapse: collapse;

    th {
      text-align: left;
      padding: ${theme.spacing(1)};
      color: ${brand.textDim};
      border-bottom: 1px solid ${brand.border};
      font-weight: ${theme.typography.fontWeightMedium};
    }
    td {
      padding: ${theme.spacing(1)};
      border-bottom: 1px solid ${brand.border};
    }
  `,
  row: css`
    cursor: pointer;
    &:hover {
      background: ${brand.bg3};
    }
  `,
  rowSelected: css`
    cursor: pointer;
    background: ${brand.bg3};
  `,
  deviceLink: css`
    font-family: ${theme.typography.fontFamilyMonospace};
  `,
});
