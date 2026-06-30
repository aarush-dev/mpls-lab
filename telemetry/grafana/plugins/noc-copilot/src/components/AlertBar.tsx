import React from 'react';
import { ALERT_STATS } from '../mock/alerts';


const G = {
  bg:    '#111217',
  card:  '#181b1f',
  elev:  '#1e2128',
  bord:  '#2c2e33',
  text:  '#d9d9d9',
  muted: '#8e8e8e',
  dim:   '#5a5a6a',
  crit:  '#f2495c',
  warn:  '#ff9830',
  ok:    '#73bf69',
  info:  '#5794f2',
};

const PAGES = [
  ['Predictive', 'predictive'],
  ['Network Map', 'network-map'],
  ['Fault Injector', 'fault-injector'],
  ['Alerts', 'notifications'],
  ['Copilot', 'assistant'],
] as const;

export function AlertBar() {
  const stats = [
    { label:'FAULTS',   value:String(ALERT_STATS.total),              color:G.crit },
    { label:'CRITICAL', value:String(ALERT_STATS.critical),           color:G.crit },
    { label:'WARNING',  value:String(ALERT_STATS.warning),            color:G.warn },
    { label:'AVG TTI',  value:ALERT_STATS.avgTti.toFixed(1)+'m',     color:'#fade2a' },
    { label:'MODEL ACC',value:ALERT_STATS.modelAccuracy+'%',         color:G.ok },
  ];
  return (
    <div style={{ display:'flex', alignItems:'center', gap:8, padding:'0 16px', height:40,
      background:G.card, borderBottom:'1px solid '+G.bord, flexShrink:0 }}>
      {/* Brand */}
      <span style={{ fontSize:11, fontWeight:700, color:G.muted, letterSpacing:'0.12em',
        marginRight:4, flexShrink:0 }}>NOC COPILOT</span>
      <div style={{ width:1, height:16, background:G.bord, flexShrink:0 }}/>
      {/* Status dot */}
      <div style={{ width:7, height:7, borderRadius:'50%', background:G.ok, flexShrink:0,
        marginLeft:2, marginRight:6 }}/>
      {/* Stats */}
      {stats.map(s => (
        <a key={s.label} href="/a/noc-copilot/notifications" style={{
          display:'flex', alignItems:'center', gap:5, textDecoration:'none',
          background:s.color+'12', border:'1px solid '+s.color+'30', borderRadius:4,
          padding:'2px 9px', flexShrink:0 }}>
          <span style={{ fontSize:9, fontWeight:600, color:G.dim, letterSpacing:'0.07em' }}>{s.label}</span>
          <span style={{ fontSize:12, fontWeight:700, color:s.color, fontVariantNumeric:'tabular-nums' }}>{s.value}</span>
        </a>
      ))}
      <div style={{ flex:1 }}/>
      {/* Nav */}
      <nav style={{ display:'flex', gap:2 }}>
        {PAGES.map(([lbl, p]) => (
          <a key={p} href={'/a/noc-copilot/'+p} style={{
            color:G.muted, fontSize:12, textDecoration:'none',
            padding:'3px 10px', borderRadius:3,
            border:'1px solid transparent',
          }}>{lbl}</a>
        ))}
      </nav>
    </div>
  );
}
