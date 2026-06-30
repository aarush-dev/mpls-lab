import React from 'react';
import { ALERT_STATS } from '../mock/alerts';

const C = {
  critical: '#f2495c', warning: '#ff9830', ok: '#73bf69', info: '#5794f2',
  border: '#2d3035', text: '#d9d9d9', muted: '#8e8e8e',
};

export function AlertBar() {
  const stats = [
    { label: 'ACTIVE FAULTS', value: String(ALERT_STATS.total),             color: C.critical },
    { label: 'CRITICAL',      value: String(ALERT_STATS.critical),           color: C.critical },
    { label: 'WARNING',       value: String(ALERT_STATS.warning),            color: C.warning  },
    { label: 'AVG TTI',       value: ALERT_STATS.avgTti.toFixed(1) + ' min', color: '#fade2a'  },
    { label: 'MODEL ACC',     value: ALERT_STATS.modelAccuracy + '%',        color: C.ok       },
  ];
  return (
    <div style={{ display:'flex', gap:8, padding:'10px 20px', background:'#0d0e12', borderBottom:'1px solid #2d3035', alignItems:'center', flexWrap:'wrap' }}>
      <span style={{ color:C.muted, fontSize:11, fontWeight:700, letterSpacing:'0.1em', marginRight:8 }}>🔔 ALERTS</span>
      {stats.map(s => (
        <a key={s.label} href="/a/noc-copilot/notifications" style={{
          background:s.color+'18', border:'1px solid '+s.color+'40', borderRadius:6,
          padding:'4px 12px', cursor:'pointer', color:s.color, fontSize:12, fontWeight:600,
          display:'flex', gap:6, alignItems:'center', textDecoration:'none',
        }}>
          <span style={{ color:C.muted, fontSize:10 }}>{s.label}</span>
          <span>{s.value}</span>
        </a>
      ))}
      <div style={{ flex:1 }} />
      {[['Predictive','predictive'],['Network Map','network-map'],['Fault Injector','fault-injector'],['AI Copilot','assistant']].map(([lbl,path]) => (
        <a key={path} href={'/a/noc-copilot/'+path} style={{
          color:C.muted, fontSize:12, textDecoration:'none', padding:'4px 10px',
          borderRadius:4, border:'1px solid #2d3035',
        }}>{lbl}</a>
      ))}
    </div>
  );
}
