import React from 'react';
import { ALERT_STATS } from '../mock/alerts';


const DS = {
  bg: '#020617',
  card: '#0E1223',
  elevated: '#1E293B',
  border: '#334155',
  borderSubtle: '#1A253B',
  text: '#F8FAFC',
  muted: '#94A3B8',
  dim: '#475569',
  critical: '#EF4444',
  criticalBg: 'rgba(239,68,68,0.08)',
  criticalGlow: 'rgba(239,68,68,0.25)',
  warning: '#F59E0B',
  warningBg: 'rgba(245,158,11,0.08)',
  ok: '#22C55E',
  okBg: 'rgba(34,197,94,0.08)',
  info: '#3B82F6',
  infoBg: 'rgba(59,130,246,0.08)',
  ai: '#818CF8',
  aiBg: 'rgba(129,140,248,0.08)',
  aiBorder: 'rgba(129,140,248,0.25)',
};


export function AlertBar() {
  const hasCritical = ALERT_STATS.critical > 0;
  const stats = [
    { label:'ACTIVE', value:String(ALERT_STATS.total), color:DS.critical, bg:DS.criticalBg, border:'rgba(239,68,68,0.3)' },
    { label:'CRITICAL', value:String(ALERT_STATS.critical), color:DS.critical, bg:DS.criticalBg, border:'rgba(239,68,68,0.3)' },
    { label:'WARNING', value:String(ALERT_STATS.warning), color:DS.warning, bg:DS.warningBg, border:'rgba(245,158,11,0.25)' },
    { label:'AVG TTI', value:ALERT_STATS.avgTti.toFixed(1)+'m', color:'#FDE68A', bg:'rgba(253,230,138,0.06)', border:'rgba(253,230,138,0.2)' },
    { label:'MODEL ACC', value:ALERT_STATS.modelAccuracy+'%', color:DS.ok, bg:DS.okBg, border:'rgba(34,197,94,0.25)' },
  ];
  const nav = [
    ['Predictive','predictive'],['Network Map','network-map'],
    ['Fault Injector','fault-injector'],['Alerts','notifications'],['Copilot','assistant'],
  ];
  return (
    <div style={{ display:'flex', alignItems:'center', gap:10, padding:'0 18px', height:46,
      background:DS.bg, borderBottom:'1px solid '+DS.border, flexShrink:0, overflow:'hidden' }}>
      {/* Brand */}
      <div style={{ display:'flex', alignItems:'center', gap:8, marginRight:6, flexShrink:0 }}>
        <span className={hasCritical?'noc-pulse':''} style={{
          width:8, height:8, borderRadius:'50%', background:hasCritical?DS.critical:DS.ok,
          display:'inline-block', flexShrink:0,
          boxShadow:hasCritical?'0 0 6px '+DS.critical:'0 0 6px '+DS.ok,
        }}/>
        <span style={{ fontSize:11, fontWeight:700, letterSpacing:'0.12em', color:DS.muted }}>NOC COPILOT</span>
      </div>
      <div style={{ width:1, height:18, background:DS.border, marginRight:4, flexShrink:0 }}/>
      {/* Stats */}
      <div style={{ display:'flex', gap:6, flexShrink:0 }}>
        {stats.map(s=>(
          <a key={s.label} href="/a/noc-copilot/notifications" style={{ display:'flex', alignItems:'center', gap:5,
            background:s.bg, border:'1px solid '+s.border, borderRadius:5,
            padding:'3px 9px', textDecoration:'none', cursor:'pointer' }}>
            <span style={{ fontSize:9, fontWeight:700, color:DS.dim, letterSpacing:'0.08em' }}>{s.label}</span>
            <span style={{ fontSize:12, fontWeight:700, color:s.color, fontVariantNumeric:'tabular-nums' }}>{s.value}</span>
          </a>
        ))}
      </div>
      <div style={{ flex:1 }}/>
      {/* Nav */}
      <nav style={{ display:'flex', gap:2 }}>
        {nav.map(([lbl,p])=>{
          const isActive = typeof window!=='undefined' && window.location.pathname.includes(p as string);
          return (
            <a key={p} href={'/a/noc-copilot/'+p} style={{
              color: isActive ? DS.text : DS.muted,
              fontSize:12, textDecoration:'none',
              padding:'4px 12px', borderRadius:5,
              background: isActive ? DS.elevated : 'transparent',
              border:'1px solid '+(isActive ? DS.border : 'transparent'),
              fontWeight: isActive ? 600 : 400,
              transition:'all 0.15s',
            }}>{lbl}</a>
          );
        })}
      </nav>
    </div>
  );
}
