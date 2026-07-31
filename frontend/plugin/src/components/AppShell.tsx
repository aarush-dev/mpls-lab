import React, { PropsWithChildren } from 'react';
// Grafana 11.1 ships react-router-dom v5 (Switch/Route/useRouteMatch), NOT v6.
import { NavLink, useRouteMatch } from 'react-router-dom';
import { css } from '@emotion/css';
import { GrafanaTheme2 } from '@grafana/data';
import { useStyles2 } from '@grafana/ui';

import { PlaybackControls } from './PlaybackControls';
import { BucketMeta } from '../utils/time';

const NAV_LINKS = [
  { to: '', label: 'Overview', exact: true },
  { to: 'topology', label: 'Topology' },
  { to: 'node/1', label: 'Node Detail' },
  { to: 'telemetry', label: 'Telemetry' },
  { to: 'incidents', label: 'Incidents' },
  { to: 'copilot', label: 'Copilot' },
  { to: 'status', label: 'Status' },
];

interface Props {
  /** Bucket metadata for the demo clock, passed through to PlaybackControls for the time label. */
  meta?: BucketMeta;
}

/** Top-level layout: title + playback controls, nav, and the routed page content. */
export function AppShell({ meta, children }: PropsWithChildren<Props>) {
  const styles = useStyles2(getStyles);
  const { url } = useRouteMatch();

  return (
    <div className={styles.root}>
      <div className={styles.topBar}>
        <h2 className={styles.title}>NOC Copilot</h2>
        <div className={styles.playback}>
          <PlaybackControls meta={meta} />
        </div>
      </div>
      <nav className={styles.nav}>
        {NAV_LINKS.map((link) => (
          <NavLink
            key={link.to}
            exact={link.exact}
            to={link.to ? `${url}/${link.to}` : url}
            className={styles.navLink}
            activeClassName={styles.navLinkActive}
          >
            {link.label}
          </NavLink>
        ))}
      </nav>
      <div className={styles.content}>{children}</div>
    </div>
  );
}

const getStyles = (theme: GrafanaTheme2) => ({
  root: css`
    display: flex;
    flex-direction: column;
  `,
  topBar: css`
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: ${theme.spacing(2)};
    flex-wrap: wrap;
    padding-bottom: ${theme.spacing(1)};
  `,
  title: css`
    margin: 0;
    white-space: nowrap;
  `,
  playback: css`
    flex: 1;
    min-width: 320px;
  `,
  nav: css`
    display: flex;
    gap: ${theme.spacing(2)};
    padding: ${theme.spacing(1)} 0;
    border-bottom: 1px solid ${theme.colors.border.weak};
    margin-bottom: ${theme.spacing(2)};
  `,
  navLink: css`
    color: ${theme.colors.text.secondary};
    text-decoration: none;
    padding: ${theme.spacing(0.5)} ${theme.spacing(1)};
  `,
  navLinkActive: css`
    color: ${theme.colors.text.primary};
    font-weight: ${theme.typography.fontWeightMedium};
  `,
  content: css`
    flex: 1;
  `,
});
