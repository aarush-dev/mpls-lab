import React from 'react';
import { NavLink, Route, Routes } from 'react-router-dom';
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
  { to: '', label: 'Overview', end: true },
  { to: 'topology', label: 'Topology' },
  { to: 'node/1', label: 'Node Detail' },
  { to: 'telemetry', label: 'Telemetry' },
  { to: 'incidents', label: 'Incidents' },
  { to: 'copilot', label: 'Copilot' },
  { to: 'status', label: 'Status' },
];

export function App(props: AppRootProps) {
  const styles = useStyles2(getStyles);

  return (
    <div className={styles.root}>
      <nav className={styles.nav}>
        {NAV_LINKS.map((link) => (
          <NavLink
            key={link.to}
            to={link.to}
            end={link.end}
            className={({ isActive }) => (isActive ? `${styles.navLink} ${styles.navLinkActive}` : styles.navLink)}
          >
            {link.label}
          </NavLink>
        ))}
      </nav>
      <div className={styles.content}>
        <Routes>
          <Route path="/" element={<OverviewPage />} />
          <Route path="/topology" element={<TopologyPage />} />
          <Route path="/node/:id" element={<NodeDetailPage />} />
          <Route path="/telemetry" element={<TelemetryPage />} />
          <Route path="/incidents" element={<IncidentsPage />} />
          <Route path="/copilot" element={<CopilotPage />} />
          <Route path="/status" element={<StatusPage />} />
        </Routes>
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
