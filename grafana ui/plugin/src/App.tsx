import React, { useEffect, useRef } from 'react';
// Grafana 11.1 ships react-router-dom v5 (Switch/Route/useRouteMatch), NOT v6.
// Use the v5 API — Routes/element do not exist on the host-provided module.
import { Route, Switch, useRouteMatch } from 'react-router-dom';
import { AppRootProps } from '@grafana/data';

import { OverviewPage } from './pages/OverviewPage';
import { TopologyPage } from './pages/TopologyPage';
import { NodeDetailPage } from './pages/NodeDetailPage';
import { TelemetryPage } from './pages/TelemetryPage';
import { IncidentsPage } from './pages/IncidentsPage';
import { CopilotPage } from './pages/CopilotPage';
import { StatusPage } from './pages/StatusPage';

import { FaultInjectionPage } from './pages/FaultInjectionPage';
import { AppShell } from './components/AppShell';
import { AlertToasterProvider, useToaster } from './components/AlertToaster';
import { AppProvider, useAppDispatch, useAppState } from './state/AppContext';
import { DataClientProvider, useDataClient } from './data/DataClientContext';
import { publishAlerts } from './alerting/alertPublisher';
import { activeAlertsFromInjected } from './alerting/activeAlerts';

export function App(props: AppRootProps) {
  return (
    <AppProvider>
      <DataClientProvider>
        <AlertToasterProvider>
          <AppInner {...props} />
        </AlertToasterProvider>
      </DataClientProvider>
    </AppProvider>
  );
}

function AppInner(_props: AppRootProps) {
  // Base path/url where Grafana mounted this app (/a/mplslab-noccopilot-app).
  const { path } = useRouteMatch();
  const { injectedFaults } = useAppState();
  const dispatch = useAppDispatch();
  const dataClient = useDataClient();
  const { notify } = useToaster();
  void dataClient; // data reads happen per-page; kept here only for future app-level hooks.

  // Fault escalation timers: an injected fault stays healthy ~5s, then goes 'predicted' (amber +
  // T-minus alert), then 'down' (red) after its leadSec. Instant visual feedback layered on top of
  // the real backend state. Scheduled once per node; cleared when the fault is cleared.
  const faultTimers = useRef<Map<string, number[]>>(new Map());
  useEffect(() => {
    const timers = faultTimers.current;
    const live = new Set(injectedFaults.map((f) => f.node));

    // Clear timers for faults that were cleared.
    for (const [node, ids] of timers) {
      if (!live.has(node)) {
        ids.forEach((id) => window.clearTimeout(id));
        timers.delete(node);
      }
    }
    // Schedule escalation for freshly-injected (pending, not yet scheduled) faults.
    for (const f of injectedFaults) {
      if (f.phase === 'pending' && !timers.has(f.node)) {
        const node = f.node;
        const toPredicted = window.setTimeout(() => dispatch({ type: 'ADVANCE_FAULT', payload: { node, phase: 'predicted' } }), 5000);
        const toDown = window.setTimeout(
          () => dispatch({ type: 'ADVANCE_FAULT', payload: { node, phase: 'down' } }),
          5000 + f.leadSec * 1000
        );
        timers.set(node, [toPredicted, toDown]);
      }
    }
  }, [injectedFaults, dispatch]);

  // Clear every pending timer on unmount.
  useEffect(() => {
    const timers = faultTimers.current;
    return () => {
      for (const ids of timers.values()) {
        ids.forEach((id) => window.clearTimeout(id));
      }
      timers.clear();
    };
  }, []);

  // Push the current firing set (injected-fault predictions + downs) into Alertmanager so they show
  // in the native Grafana Alerting tab, AND toast any newly-appeared alert. Only POST / toast when the
  // set changes. ponytail: change-only push means a node red for >resolve_timeout auto-resolves in AM.
  const prevAlertKeys = useRef<Set<string> | null>(null);
  useEffect(() => {
    const alerts = activeAlertsFromInjected(injectedFaults);
    const keys = new Set(alerts.map((a) => `${a.alertname}:${a.node}`));
    const prev = prevAlertKeys.current;

    if (prev === null) {
      // First run: seed without toasting the pre-existing set (avoids a load-time burst).
      prevAlertKeys.current = keys;
      publishAlerts(alerts);
      return;
    }
    const sameSet = keys.size === prev.size && [...keys].every((k) => prev.has(k));
    if (sameSet) {
      return;
    }
    // Toast only alerts that are newly firing since last time.
    for (const a of alerts) {
      if (!prev.has(`${a.alertname}:${a.node}`)) {
        notify({ severity: a.severity, title: a.summary, text: a.description });
      }
    }
    prevAlertKeys.current = keys;
    publishAlerts(alerts);
  }, [injectedFaults, notify]);

  return (
    <AppShell>
      <Switch>
        <Route exact path={path} component={OverviewPage} />
        <Route path={`${path}/topology`} component={TopologyPage} />
        <Route path={`${path}/node/:id`} component={NodeDetailPage} />
        <Route path={`${path}/telemetry`} component={TelemetryPage} />
        <Route path={`${path}/incidents`} component={IncidentsPage} />
        <Route path={`${path}/copilot`} component={CopilotPage} />
        <Route path={`${path}/inject`} component={FaultInjectionPage} />
        <Route path={`${path}/status`} component={StatusPage} />
      </Switch>
    </AppShell>
  );
}
