import React from 'react';
import { AlertBar } from '../components/AlertBar';
import { MOCK_ALERTS, ALERT_STATS } from '../mock/alerts';


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

export function NotificationsPage() {
  const sorted=[...MOCK_ALERTS].sort((a,b)=>a.tti-b.tti);
  return (
    <div style={{ display:'flex',flexDirection:'column',height:'100vh',background:G.bg }}>
      <AlertBar/>
      <div style={{ flex:1,overflowY:'auto',padding:'14px 16px' }}>
        <div style={{ display:'grid',gridTemplateColumns:'1fr 1fr 1fr 2fr',gap:10,marginBottom:14 }}>
          {[
            {label:'CRITICAL', value:ALERT_STATS.critical, color:G.crit},
            {label:'WARNING',  value:ALERT_STATS.warning,  color:G.warn},
            {label:'TOTAL',    value:ALERT_STATS.total,    color:G.info},
          ].map(s=>(
            <div key={s.label} style={{ background:G.card,border:'1px solid '+G.bord,
              borderTop:'2px solid '+s.color,borderRadius:4,padding:'10px 14px' }}>
              <div style={{ fontSize:9,fontWeight:700,color:G.muted,letterSpacing:'0.1em',marginBottom:4 }}>{s.label}</div>
              <div style={{ fontSize:30,fontWeight:700,color:s.color,fontFamily:'ui-monospace,monospace',lineHeight:1 }}>{s.value}</div>
            </div>
          ))}
          <div style={{ background:G.card,border:'1px solid '+G.bord,borderRadius:4,padding:'10px 14px' }}>
            <div style={{ fontSize:9,fontWeight:700,color:G.muted,letterSpacing:'0.1em',marginBottom:4 }}>MODEL ACCURACY (24h)</div>
            <div style={{ display:'flex',alignItems:'baseline',gap:8 }}>
              <span style={{ fontSize:30,fontWeight:700,color:G.ok,fontFamily:'ui-monospace,monospace',lineHeight:1 }}>{ALERT_STATS.modelAccuracy}%</span>
              <span style={{ fontSize:11,color:G.muted }}>avg lead {ALERT_STATS.avgTti.toFixed(1)} min</span>
            </div>
          </div>
        </div>
        <div style={{ background:G.card,border:'1px solid '+G.bord,borderRadius:4,overflow:'hidden' }}>
          <div style={{ padding:'8px 14px',borderBottom:'1px solid '+G.bord,
            fontSize:10,fontWeight:700,color:G.muted,letterSpacing:'0.08em' }}>
            ACTIVE ALERTS — SORTED BY TIME-TO-IMPACT
          </div>
          {sorted.map(a=>{
            const col=a.severity==='CRITICAL'?G.crit:G.warn;
            return (
              <div key={a.id} style={{ display:'flex',alignItems:'center',gap:10,padding:'10px 14px',
                borderBottom:'1px solid '+G.bord+'30',borderLeft:'3px solid '+col }}>
                <span style={{ background:col+'15',border:'1px solid '+col+'30',borderRadius:3,
                  padding:'2px 7px',fontSize:10,fontWeight:700,color:col,flexShrink:0,
                  letterSpacing:'0.06em',fontFamily:'ui-monospace,monospace' }}>{a.severity}</span>
                <span style={{ background:G.elev,border:'1px solid '+G.bord,borderRadius:3,
                  padding:'2px 7px',fontSize:11,color:G.info,flexShrink:0,
                  fontFamily:'ui-monospace,monospace' }}>{a.device}</span>
                <span style={{ flex:1,fontSize:13,color:G.text }}>{a.faultType}</span>
                <div style={{ flexShrink:0,textAlign:'right',minWidth:55 }}>
                  <div style={{ fontSize:19,fontWeight:700,color:col,fontFamily:'ui-monospace,monospace',lineHeight:1 }}>
                    {a.tti}<span style={{ fontSize:10,fontWeight:400 }}>m</span>
                  </div>
                  <div style={{ fontSize:9,color:G.dim,marginTop:1 }}>TTI</div>
                </div>
                <div style={{ flexShrink:0,textAlign:'right',minWidth:50 }}>
                  <div style={{ fontSize:12,color:G.ok,fontWeight:700,fontFamily:'ui-monospace,monospace' }}>{a.confidence}%</div>
                  <div style={{ fontSize:9,color:G.dim }}>conf.</div>
                </div>
                <div style={{ flexShrink:0,color:G.dim,fontSize:10,fontFamily:'ui-monospace,monospace',minWidth:58 }}>{a.timestamp.slice(11,19)}</div>
                <a href={'/a/noc-copilot/diagnostic?device='+encodeURIComponent(a.device)} style={{
                  flexShrink:0,background:G.elev,border:'1px solid '+G.bord,
                  borderRadius:3,padding:'5px 10px',color:G.info,fontSize:11,
                  fontWeight:600,textDecoration:'none' }}>Diagnose</a>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
