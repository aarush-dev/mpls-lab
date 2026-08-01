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
import { MOCK_BUCKET_META, MOCK_WINDOW_BUCKETS } from './data/MockDataClient';
import { AlertDescriptor, publishAlerts } from './alerting/alertPublisher';

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
  const { cursor, absTick, injectedFaults } = useAppState();
  const dispatch = useAppDispatch();
  const dataClient = useDataClient();
  const { notify } = useToaster();

  // Fixture bucket bounds are known statically in mock mode; wire them into the shared clock once
  // on mount so the PlaybackControls slider/loop have the right range from the first render.
  useEffect(() => {
    dispatch({
      type: 'SET_BOUNDS',
      payload: { bucketCount: MOCK_BUCKET_META.bucketCount, windowBuckets: MOCK_WINDOW_BUCKETS },
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Every clock tick, tell the (cursor-aware) data client where "now" is. HttpDataClient (M3+)
  // won't implement setCursor, so this is a soft feature-detect rather than a hard interface call.
  useEffect(() => {
    const cursorAware = dataClient as unknown as { setCursor?: (n: number) => void };
    if (typeof cursorAware.setCursor === 'function') {
      cursorAware.setCursor(cursor);
    }
  }, [dataClient, cursor]);

  // Same soft feature-detect for the monotonic display clock (absTick). getTelemetry uses it to
  // rewrite point timestamps so the chart time axis never rewinds on loop.
  useEffect(() => {
    const clockAware = dataClient as unknown as { setAbsTick?: (n: number) => void };
    if (typeof clockAware.setAbsTick === 'function') {
      clockAware.setAbsTick(absTick);
    }
  }, [dataClient, absTick]);

  // Push manually injected faults (Fault Injection page) into the data client so a node reads red
  // and fires an alert regardless of the cursor. Runs before the alert effect below.
  useEffect(() => {
    const injectAware = dataClient as unknown as {
      setInjectedFaults?: (f: Array<{ node: string; faultType: string }>) => void;
    };
    if (typeof injectAware.setInjectedFaults === 'function') {
      injectAware.setInjectedFaults(injectedFaults);
    }
  }, [dataClient, injectedFaults]);

  // Push the current firing set (node-down + T-5min predictions + injected faults) into Alertmanager
  // so they show in the native Grafana Alerting tab, AND toast any newly-appeared alert. Only POST /
  // toast when the set changes. Runs after the setCursor/injected effects so getActiveAlerts is fresh.
  // ponytail: change-only push means a node red for >resolve_timeout auto-resolves in AM; the demo
  // loops far faster than that, so no periodic re-post is needed.
  const prevAlertKeys = useRef<Set<string> | null>(null);
  useEffect(() => {
    const alertAware = dataClient as unknown as { getActiveAlerts?: () => AlertDescriptor[] };
    if (typeof alertAware.getActiveAlerts !== 'function') {
      return;
    }
    const alerts = alertAware.getActiveAlerts();
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
  }, [dataClient, cursor, injectedFaults, notify]);

  return (
    <AppShell meta={MOCK_BUCKET_META}>
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
