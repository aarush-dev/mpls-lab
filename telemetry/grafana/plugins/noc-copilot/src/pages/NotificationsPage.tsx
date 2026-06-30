import React from 'react';
import { AlertBar } from '../components/AlertBar';
import { MOCK_ALERTS, ALERT_STATS } from '../mock/alerts';


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


export function NotificationsPage() {
  const sorted=[...MOCK_ALERTS].sort((a,b)=>a.tti-b.tti);
  const statCards=[
    { label:'CRITICAL', value:ALERT_STATS.critical, color:DS.critical, bg:DS.criticalBg, border:'rgba(239,68,68,0.25)' },
    { label:'WARNING',  value:ALERT_STATS.warning,  color:DS.warning,  bg:DS.warningBg,  border:'rgba(245,158,11,0.25)' },
    { label:'TOTAL',    value:ALERT_STATS.total,    color:DS.info,     bg:DS.infoBg,     border:'rgba(59,130,246,0.25)' },
  ];
  return (
    <div style={{ display:'flex', flexDirection:'column', height:'100vh', background:DS.bg }}>
      <AlertBar/>
      <div style={{ flex:1, overflowY:'auto', padding:'14px 18px' }}>
        {/* Summary row */}
        <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr 1fr 2fr', gap:10, marginBottom:14 }}>
          {statCards.map(s=>(
            <div key={s.label} style={{ background:DS.card, border:'1px solid '+s.border,
              borderTop:'2px solid '+s.color, borderRadius:8, padding:'12px 16px' }}>
              <div style={{ fontSize:9, fontWeight:700, color:DS.muted, letterSpacing:'0.1em', marginBottom:6 }}>{s.label}</div>
              <div style={{ fontSize:32, fontWeight:800, color:s.color, fontFamily:'ui-monospace,monospace', lineHeight:1 }}>{s.value}</div>
            </div>
          ))}
          <div style={{ background:DS.card, border:'1px solid '+DS.border, borderRadius:8, padding:'12px 16px' }}>
            <div style={{ fontSize:9, fontWeight:700, color:DS.muted, letterSpacing:'0.1em', marginBottom:6 }}>MODEL ACCURACY (24h)</div>
            <div style={{ display:'flex', alignItems:'baseline', gap:10 }}>
              <div style={{ fontSize:32, fontWeight:800, color:DS.ok, fontFamily:'ui-monospace,monospace', lineHeight:1 }}>{ALERT_STATS.modelAccuracy}%</div>
              <div style={{ fontSize:12, color:DS.muted }}>avg lead {ALERT_STATS.avgTti.toFixed(1)} min</div>
            </div>
          </div>
        </div>
        {/* Alert feed */}
        <div style={{ background:DS.card, border:'1px solid '+DS.border, borderRadius:8, overflow:'hidden' }}>
          <div style={{ padding:'9px 16px', borderBottom:'1px solid '+DS.border,
            fontSize:10, fontWeight:700, color:DS.muted, letterSpacing:'0.1em',
            display:'flex', alignItems:'center', gap:8 }}>
            ACTIVE ALERTS — SORTED BY TIME-TO-IMPACT
            <span style={{ background:DS.criticalBg, border:'1px solid rgba(239,68,68,0.25)',
              color:DS.critical, fontSize:9, fontWeight:700, borderRadius:3, padding:'1px 6px' }}>
              {ALERT_STATS.critical} CRITICAL
            </span>
          </div>
          {sorted.map((a,idx)=>{
            const col=a.severity==='CRITICAL'?DS.critical:DS.warning;
            const urgent=a.severity==='CRITICAL';
            return (
              <div key={a.id} style={{
                display:'flex', alignItems:'center', gap:12, padding:'12px 16px',
                borderBottom:'1px solid '+DS.borderSubtle,
                borderLeft:'3px solid '+col,
                background:idx%2===0?'rgba(248,250,252,0.01)':'transparent',
              }}>
                {/* Severity badge */}
                <div style={{ flexShrink:0, display:'flex', alignItems:'center', gap:5,
                  background:col+'12', border:'1px solid '+col+'30', borderRadius:5,
                  padding:'3px 8px', minWidth:76 }}>
                  {urgent&&<span className="noc-pulse" style={{ width:5,height:5,borderRadius:'50%',background:col,display:'inline-block'}}/>}
                  <span style={{ fontSize:10, fontWeight:700, color:col, letterSpacing:'0.06em' }}>{a.severity}</span>
                </div>
                {/* Device */}
                <span style={{ background:'rgba(147,197,253,0.08)', border:'1px solid rgba(147,197,253,0.2)',
                  borderRadius:4, padding:'2px 8px', fontSize:11, color:'#93C5FD', flexShrink:0,
                  fontFamily:'ui-monospace,monospace' }}>{a.device}</span>
                {/* Fault type */}
                <span style={{ flex:1, fontSize:13, color:DS.text }}>{a.faultType}</span>
                {/* TTI */}
                <div style={{ flexShrink:0, textAlign:'right', minWidth:60 }}>
                  <div style={{ fontSize:20, fontWeight:800, color:col, fontFamily:'ui-monospace,monospace', lineHeight:1 }}>{a.tti}<span style={{ fontSize:11, fontWeight:400 }}> min</span></div>
                  <div style={{ fontSize:9, color:DS.dim, marginTop:1 }}>TIME TO IMPACT</div>
                </div>
                {/* Confidence */}
                <div style={{ flexShrink:0, textAlign:'right', minWidth:52 }}>
                  <div style={{ fontSize:12, color:DS.ok, fontWeight:700, fontFamily:'ui-monospace,monospace' }}>{a.confidence}%</div>
                  <div style={{ fontSize:9, color:DS.dim }}>confidence</div>
                </div>
                {/* Timestamp */}
                <div style={{ flexShrink:0, color:DS.dim, fontSize:10, fontFamily:'ui-monospace,monospace', minWidth:60 }}>{a.timestamp.slice(11,19)} UTC</div>
                {/* Diagnose */}
                <a href={"/a/noc-copilot/diagnostic?device="+encodeURIComponent(a.device)} style={{
                  flexShrink:0, background:'rgba(129,140,248,0.08)', border:'1px solid rgba(129,140,248,0.25)',
                  borderRadius:6, padding:'6px 12px', color:DS.ai, fontSize:11, fontWeight:700,
                  textDecoration:'none', letterSpacing:'0.04em' }}>→ Diagnose</a>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
