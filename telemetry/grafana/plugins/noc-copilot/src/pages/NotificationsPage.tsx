import React from 'react';
import { AlertBar } from '../components/AlertBar';
import { MOCK_ALERTS, ALERT_STATS } from '../mock/alerts';

const C = { critical:'#f2495c', warning:'#ff9830', ok:'#73bf69', info:'#5794f2',
  bg:'#111217', cardBg:'#141618', border:'#2d3035', text:'#d9d9d9', muted:'#8e8e8e' };

export function NotificationsPage() {
  const sorted = [...MOCK_ALERTS].sort((a,b)=>a.tti-b.tti);
  return (
    <div style={{ display:'flex', flexDirection:'column', height:'100vh', background:C.bg }}>
      <AlertBar/>
      <div style={{ flex:1, overflowY:'auto', padding:18 }}>
        <div style={{ display:'flex', gap:12, marginBottom:18 }}>
          {[
            { label:'CRITICAL', value:ALERT_STATS.critical, color:C.critical },
            { label:'WARNING',  value:ALERT_STATS.warning,  color:C.warning  },
            { label:'TOTAL',    value:ALERT_STATS.total,    color:C.info     },
          ].map(s=>(
            <div key={s.label} style={{ flex:1, background:C.cardBg, border:'1px solid '+s.color+'30', borderRadius:8, padding:'14px 18px' }}>
              <div style={{ fontSize:10, fontWeight:700, color:C.muted, letterSpacing:'0.1em', marginBottom:4 }}>{s.label}</div>
              <div style={{ fontSize:30, fontWeight:700, color:s.color }}>{s.value}</div>
            </div>
          ))}
          <div style={{ flex:2, background:C.cardBg, border:'1px solid '+C.border, borderRadius:8, padding:'14px 18px' }}>
            <div style={{ fontSize:10, fontWeight:700, color:C.muted, letterSpacing:'0.1em', marginBottom:4 }}>MODEL ACCURACY (24h)</div>
            <div style={{ display:'flex', alignItems:'baseline', gap:8 }}>
              <div style={{ fontSize:30, fontWeight:700, color:C.ok }}>{ALERT_STATS.modelAccuracy}%</div>
              <div style={{ fontSize:12, color:C.muted }}>Avg lead time {ALERT_STATS.avgTti.toFixed(1)} min</div>
            </div>
          </div>
        </div>
        <div style={{ background:C.cardBg, border:'1px solid '+C.border, borderRadius:8, overflow:'hidden' }}>
          <div style={{ padding:'10px 16px', borderBottom:'1px solid '+C.border, fontSize:11, fontWeight:700, color:C.muted, letterSpacing:'0.08em' }}>
            ACTIVE ALERTS — SORTED BY TIME-TO-IMPACT
          </div>
          {sorted.map(a=>{
            const col = a.severity==='CRITICAL'?C.critical:C.warning;
            return <div key={a.id} style={{ display:'flex', alignItems:'center', gap:12, padding:'13px 16px',
              borderBottom:'1px solid '+C.border+'20', borderLeft:'4px solid '+col }}>
              <span style={{ background:col+'20', border:'1px solid '+col+'50', borderRadius:4, padding:'2px 7px',
                fontSize:10, fontWeight:700, color:col, flexShrink:0 }}>{a.severity}</span>
              <span style={{ background:'#1d2035', borderRadius:4, padding:'2px 7px', fontSize:11, color:'#8ab4f8', flexShrink:0 }}>{a.device}</span>
              <span style={{ flex:1, fontSize:13, color:C.text }}>{a.faultType}</span>
              <div style={{ flexShrink:0, textAlign:'right' }}>
                <div style={{ fontSize:18, fontWeight:700, color:col }}>{a.tti} min</div>
                <div style={{ fontSize:10, color:C.muted }}>TTI</div>
              </div>
              <div style={{ flexShrink:0, textAlign:'right', color:C.muted, fontSize:11 }}>
                <div>{a.confidence}%</div><div>confidence</div>
              </div>
              <div style={{ flexShrink:0, color:C.muted, fontSize:11 }}>{a.timestamp.slice(11,19)} UTC</div>
              <a href={"/a/noc-copilot/diagnostic?device="+encodeURIComponent(a.device)} style={{
                flexShrink:0, background:C.info+'15', border:'1px solid '+C.info+'40',
                borderRadius:6, padding:'6px 12px', color:C.info, fontSize:12, fontWeight:600, textDecoration:'none' }}>→ Diagnose</a>
            </div>;
          })}
        </div>
      </div>
    </div>
  );
}
