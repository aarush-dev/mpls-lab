import React from 'react';
import type { AppRootProps } from '@grafana/data';
import { PredictivePage } from './pages/PredictivePage';
import { NetworkMapPage } from './pages/NetworkMapPage';
import { FaultInjectorPage } from './pages/FaultInjectorPage';
import { NotificationsPage } from './pages/NotificationsPage';
import { DiagnosticPage } from './pages/DiagnosticPage';
import { AssistantPage } from './pages/AssistantPage';

const ANIM = `
@keyframes nocPulse{0%,100%{opacity:1}50%{opacity:0.25}}
@keyframes nocGlowRed{0%,100%{box-shadow:0 0 4px rgba(239,68,68,0.3),0 0 12px rgba(239,68,68,0.1)}50%{box-shadow:0 0 10px rgba(239,68,68,0.7),0 0 24px rgba(239,68,68,0.25)}}
@keyframes nocGlowAmber{0%,100%{box-shadow:0 0 4px rgba(245,158,11,0.3)}50%{box-shadow:0 0 10px rgba(245,158,11,0.7)}}
.noc-pulse{animation:nocPulse 2s ease-in-out infinite}
.noc-glow-red{animation:nocGlowRed 2.5s ease-in-out infinite}
.noc-glow-amber{animation:nocGlowAmber 2.5s ease-in-out infinite}
`;

export function App(props: AppRootProps) {
  const path = props.path || '';
  return (
    <div style={{ fontFamily:"'Inter','Segoe UI',system-ui,sans-serif", background:'#020617', minHeight:'100vh', color:'#F8FAFC' }}>
      <style>{ANIM}</style>
      {path.includes('network-map')    ? <NetworkMapPage /> :
       path.includes('fault-injector') ? <FaultInjectorPage /> :
       path.includes('notifications')  ? <NotificationsPage /> :
       path.includes('diagnostic')     ? <DiagnosticPage device={(props.query?.device as string)||'PE-3'} /> :
       path.includes('assistant')      ? <AssistantPage /> :
       <PredictivePage />}
    </div>
  );
}
