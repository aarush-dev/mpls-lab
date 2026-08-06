import React, { useState } from 'react';
// Grafana 11.1 ships react-router-dom v5 (Switch/Route/useRouteMatch), NOT v6.
// Use the v5 API — Routes/element do not exist on the host-provided module.
import { Route, Switch, useRouteMatch } from 'react-router-dom';
import { AppRootProps } from '@grafana/data';

import { OverviewPage } from './pages/OverviewPage';
import { TopologyPage } from './pages/TopologyPage';
import { NodeDetailPage } from './pages/NodeDetailPage';
import { IncidentsPage } from './pages/IncidentsPage';
import { CopilotPage } from './pages/CopilotPage';
import { StatusPage } from './pages/StatusPage';

import { FaultInjectionPage } from './pages/FaultInjectionPage';
import { AppShell } from './components/AppShell';
import { PaAlertsBanner } from './components/PaAlertsBanner';
import { CopilotPanel } from './components/CopilotPanel';
import { CopilotChatProvider } from './hooks/CopilotChatContext';
import { AlertToasterProvider } from './components/AlertToaster';
import { AppProvider } from './state/AppContext';
import { DataClientProvider } from './data/DataClientContext';

export function App(props: AppRootProps) {
  return (
    <AppProvider>
      <DataClientProvider>
        {/* ponytail: no consumers since fake-alert publishing was removed (#87); kept for #84 to rewire to real alerts. */}
        <AlertToasterProvider>
          <CopilotChatProvider>
            <AppInner {...props} />
          </CopilotChatProvider>
        </AlertToasterProvider>
      </DataClientProvider>
    </AppProvider>
  );
}

function AppInner(_props: AppRootProps) {
  // Base path/url where Grafana mounted this app (/a/mplslab-noccopilot-app).
  const { path } = useRouteMatch();
  // T5/#72: global copilot side panel — open state lives here so the top-bar toggle (in AppShell)
  // and the panel itself share it. Panel mounts once above every route.
  const [copilotOpen, setCopilotOpen] = useState(false);

  return (
    <AppShell onToggleCopilot={() => setCopilotOpen((v) => !v)}>
      <CopilotPanel open={copilotOpen} onClose={() => setCopilotOpen(false)} />
      <PaAlertsBanner />
      <Switch>
        <Route exact path={path} component={OverviewPage} />
        <Route path={`${path}/topology`} component={TopologyPage} />
        <Route path={`${path}/node/:id`} component={NodeDetailPage} />
        <Route path={`${path}/incidents`} component={IncidentsPage} />
        <Route path={`${path}/copilot`} component={CopilotPage} />
        <Route path={`${path}/inject`} component={FaultInjectionPage} />
        <Route path={`${path}/status`} component={StatusPage} />
      </Switch>
    </AppShell>
  );
}
