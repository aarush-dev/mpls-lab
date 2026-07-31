import React from 'react';
// Grafana 11.1 ships react-router-dom v5 (Switch/Route/useRouteMatch), NOT v6.
// Use the v5 API — Routes/element do not exist on the host-provided module.
import { NavLink, Route, Switch, useRouteMatch } from 'react-router-dom';
import { css } from '@emotion/css';
import { AppRootProps, GrafanaTheme2 } from '@grafana/data';
import { useStyles2 } from '@grafana/ui';

import { OverviewPage } from './pages/OverviewPage';
import { TopologyPage } from './pages/TopologyPage';
import { NodeDetailPage } from './pages/NodeDetailPage';
import { TelemetryPage } from './pages/TelemetryPage';
import { IncidentsPage } from './pages/IncidentsPage';
import { CopilotPage } from './pages/CopilotPage';
import { StatusPage } from './pages/StatusPage';

const NAV_LINKS = [
  { to: '', label: 'Overview', exact: true },
  { to: 'topology', label: 'Topology' },
  { to: 'node/1', label: 'Node Detail' },
  { to: 'telemetry', label: 'Telemetry' },
  { to: 'incidents', label: 'Incidents' },
  { to: 'copilot', label: 'Copilot' },
  { to: 'status', label: 'Status' },
];

export function App(_props: AppRootProps) {
  const styles = useStyles2(getStyles);
  // Base path/url where Grafana mounted this app (/a/mplslab-noccopilot-app).
  const { path, url } = useRouteMatch();

  return (
    <div className={styles.root}>
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
      <div className={styles.content}>
        <Switch>
          <Route exact path={path} component={OverviewPage} />
          <Route path={`${path}/topology`} component={TopologyPage} />
          <Route path={`${path}/node/:id`} component={NodeDetailPage} />
          <Route path={`${path}/telemetry`} component={TelemetryPage} />
          <Route path={`${path}/incidents`} component={IncidentsPage} />
          <Route path={`${path}/copilot`} component={CopilotPage} />
          <Route path={`${path}/status`} component={StatusPage} />
        </Switch>
      </div>
    </div>
  );
}

const getStyles = (theme: GrafanaTheme2) => ({
  root: css`
    display: flex;
    flex-direction: column;
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
