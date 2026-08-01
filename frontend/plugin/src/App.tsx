import React, { useEffect } from 'react';
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

import { AppShell } from './components/AppShell';
import { AppProvider, useAppDispatch, useAppState } from './state/AppContext';
import { DataClientProvider, useDataClient } from './data/DataClientContext';
import { MOCK_BUCKET_META, MOCK_WINDOW_BUCKETS } from './data/MockDataClient';

export function App(props: AppRootProps) {
  return (
    <AppProvider>
      <DataClientProvider>
        <AppInner {...props} />
      </DataClientProvider>
    </AppProvider>
  );
}

function AppInner(_props: AppRootProps) {
  // Base path/url where Grafana mounted this app (/a/mplslab-noccopilot-app).
  const { path } = useRouteMatch();
  const { cursor, absTick } = useAppState();
  const dispatch = useAppDispatch();
  const dataClient = useDataClient();

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

  return (
    <AppShell meta={MOCK_BUCKET_META}>
      <Switch>
        <Route exact path={path} component={OverviewPage} />
        <Route path={`${path}/topology`} component={TopologyPage} />
        <Route path={`${path}/node/:id`} component={NodeDetailPage} />
        <Route path={`${path}/telemetry`} component={TelemetryPage} />
        <Route path={`${path}/incidents`} component={IncidentsPage} />
        <Route path={`${path}/copilot`} component={CopilotPage} />
        <Route path={`${path}/status`} component={StatusPage} />
      </Switch>
    </AppShell>
  );
}
