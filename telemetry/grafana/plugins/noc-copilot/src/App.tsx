import React from 'react';
import type { AppRootProps } from '@grafana/data';
import { PredictivePage } from './pages/PredictivePage';
import { NetworkMapPage } from './pages/NetworkMapPage';
import { FaultInjectorPage } from './pages/FaultInjectorPage';
import { NotificationsPage } from './pages/NotificationsPage';
import { DiagnosticPage } from './pages/DiagnosticPage';
import { AssistantPage } from './pages/AssistantPage';

const PAGE_STYLES: React.CSSProperties = {
  fontFamily: "'Inter', 'Helvetica Neue', Arial, sans-serif",
  background: '#111217',
  minHeight: '100vh',
  color: '#d9d9d9',
  padding: '0',
};

export function App(props: AppRootProps) {
  const path = props.path || '';
  return (
    <div style={PAGE_STYLES}>
      {path.includes('network-map')   ? <NetworkMapPage /> :
       path.includes('fault-injector') ? <FaultInjectorPage /> :
       path.includes('notifications')  ? <NotificationsPage /> :
       path.includes('diagnostic')     ? <DiagnosticPage device={(props.query?.device as string) || 'PE-3'} /> :
       path.includes('assistant')      ? <AssistantPage /> :
       <PredictivePage />}
    </div>
  );
}
