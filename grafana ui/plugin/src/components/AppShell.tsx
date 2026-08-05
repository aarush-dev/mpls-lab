import React, { PropsWithChildren } from 'react';
// Grafana 11.1 ships react-router-dom v5 (Switch/Route/useRouteMatch), NOT v6.
import { NavLink, useRouteMatch } from 'react-router-dom';
import { css } from '@emotion/css';
import { GrafanaTheme2, IconName } from '@grafana/data';
import { useStyles2, Button, Icon } from '@grafana/ui';

import { TimeControl } from './TimeControl';
import { LabStatusBadge } from './LabStatusBadge';
import { FilterBar } from './FilterBar';
import { useAppState } from '../state/AppContext';

interface NavLinkDef {
  to: string;
  label: string;
  icon: IconName;
  exact?: boolean;
}

const NAV_LINKS: NavLinkDef[] = [
  { to: '', label: 'Overview', icon: 'apps', exact: true },
  { to: 'topology', label: 'Topology', icon: 'sitemap' },
  // Node Detail is injected dynamically from the last-viewed node (see below) — not a hardcoded device.
  { to: 'telemetry', label: 'Telemetry', icon: 'graph-bar' },
  { to: 'incidents', label: 'Incidents', icon: 'bell' },
  { to: 'copilot', label: 'Copilot', icon: 'comment-alt' },
  { to: 'inject', label: 'Fault Injection', icon: 'bolt' },
  { to: 'status', label: 'Status', icon: 'heart' },
];

/** Top-level layout: title + lab status + time control, nav, and the routed page content. */
export function AppShell({ children, onToggleCopilot }: PropsWithChildren<{ onToggleCopilot?: () => void }>) {
  const styles = useStyles2(getStyles);
  const { url } = useRouteMatch();
  const { lastNode } = useAppState();

  // "Node Detail" reflects the last-viewed node instead of a frozen device; hidden until one is opened
  // (Node Detail stays reachable by drill-through from Topology/Incidents in the meantime).
  const links: NavLinkDef[] = lastNode
    ? [...NAV_LINKS.slice(0, 2), { to: `node/${encodeURIComponent(lastNode)}`, label: 'Node Detail', icon: 'monitor' }, ...NAV_LINKS.slice(2)]
    : NAV_LINKS;

  return (
    <div className={styles.root}>
      <div className={styles.topBar}>
        <h2 className={styles.title}>NOC Copilot</h2>
        <div className={styles.controls}>
          <LabStatusBadge />
          <TimeControl />
          {onToggleCopilot && (
            <Button variant="secondary" size="sm" icon="comment-alt" onClick={onToggleCopilot}>
              Copilot
            </Button>
          )}
        </div>
      </div>
      <nav className={styles.nav}>
        {links.map((link) => (
          <NavLink
            key={link.to}
            exact={link.exact}
            to={link.to ? `${url}/${link.to}` : url}
            className={styles.navLink}
            activeClassName={styles.navLinkActive}
          >
            <Icon name={link.icon} />
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
  controls: css`
    display: flex;
    align-items: center;
    gap: ${theme.spacing(1.5)};
    flex-wrap: wrap;
  `,
  nav: css`
    display: flex;
    align-items: center;
    gap: ${theme.spacing(2)};
    padding: ${theme.spacing(1)} 0;
    border-bottom: 1px solid ${theme.colors.border.weak};
    margin-bottom: ${theme.spacing(2)};
  `,
  filters: css`
    margin-left: auto;
  `,
  navLink: css`
    display: inline-flex;
    align-items: center;
    gap: ${theme.spacing(0.5)};
    color: ${theme.colors.text.secondary};
    text-decoration: none;
    padding: ${theme.spacing(0.5)} ${theme.spacing(1)};
    border-bottom: 2px solid transparent;
  `,
  navLinkActive: css`
    color: ${theme.colors.text.primary};
    font-weight: ${theme.typography.fontWeightMedium};
    border-bottom-color: ${theme.colors.primary.border};
  `,
  content: css`
    flex: 1;
  `,
});
