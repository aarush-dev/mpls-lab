import React, { PropsWithChildren } from 'react';
// Grafana 11.1 ships react-router-dom v5 (Switch/Route/useRouteMatch), NOT v6.
import { NavLink, useRouteMatch } from 'react-router-dom';
import { css } from '@emotion/css';
import { GrafanaTheme2 } from '@grafana/data';
import { useStyles2 } from '@grafana/ui';

import { PlaybackControls } from './PlaybackControls';
import { FilterBar } from './FilterBar';
import { BucketMeta } from '../utils/time';
import { brand } from '../brand';

const NAV_LINKS = [
  { to: '', label: 'Overview', exact: true },
  { to: 'topology', label: 'Topology' },
  { to: 'node/pe1', label: 'Node Detail' },
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
        <div className={styles.brand}>
          <span className={styles.brandMark} />
          <span className={styles.brandName}>
            MPLS<span className={styles.brandAccent}>Copilot</span>
          </span>
          <span className={styles.brandTag}>predictive NOC</span>
        </div>
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
        <div className={styles.filters}>
          <FilterBar />
        </div>
      </nav>
      <div className={styles.content}>{children}</div>
    </div>
  );
}

const getStyles = (theme: GrafanaTheme2) => ({
  root: css`
    display: flex;
    flex-direction: column;
    background: ${brand.bg1};
    color: ${brand.text};
    min-height: 100%;
  `,
  topBar: css`
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: ${theme.spacing(2)};
    flex-wrap: wrap;
    padding: ${theme.spacing(1.5)} ${theme.spacing(2)};
    background: ${brand.bg0};
    border-bottom: 2px solid ${brand.accent};
  `,
  brand: css`
    display: flex;
    align-items: center;
    gap: ${theme.spacing(1.25)};
    white-space: nowrap;
  `,
  brandMark: css`
    width: 14px;
    height: 14px;
    border-radius: 3px;
    background: ${brand.accent};
    box-shadow: 0 0 10px ${brand.accent};
  `,
  brandName: css`
    font-size: 20px;
    font-weight: 800;
    letter-spacing: 0.02em;
    color: ${brand.text};
  `,
  brandAccent: css`
    color: ${brand.accent};
    margin-left: 2px;
  `,
  brandTag: css`
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.18em;
    color: ${brand.textFaint};
    border-left: 1px solid ${brand.border};
    padding-left: ${theme.spacing(1.25)};
  `,
  playback: css`
    flex: 1;
    min-width: 320px;
  `,
  nav: css`
    display: flex;
    align-items: center;
    gap: ${theme.spacing(0.5)};
    padding: ${theme.spacing(1)} ${theme.spacing(2)};
    background: ${brand.bg0};
    border-bottom: 1px solid ${brand.border};
    margin-bottom: ${theme.spacing(2)};
  `,
  filters: css`
    margin-left: auto;
  `,
  navLink: css`
    color: ${brand.textDim};
    text-decoration: none;
    padding: ${theme.spacing(0.75)} ${theme.spacing(1.25)};
    border-radius: ${theme.shape.radius.default};
    border: 1px solid transparent;
    &:hover {
      color: ${brand.text};
      background: ${brand.bg2};
    }
  `,
  navLinkActive: css`
    color: ${brand.text};
    font-weight: ${theme.typography.fontWeightMedium};
    background: ${brand.accentDim};
    border-color: ${brand.accent};
  `,
  content: css`
    flex: 1;
    padding: 0 ${theme.spacing(2)} ${theme.spacing(2)};
  `,
});
