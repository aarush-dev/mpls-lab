import React from 'react';
import { CopilotPanel } from '../components/CopilotPanel';
import { DIAGNOSTIC_CONVO } from '../mock/conversations';
import { MOCK_ALERTS } from '../mock/alerts';

const C = { critical:'#f2495c', warning:'#ff9830', ok:'#73bf69', info:'#5794f2',
  bg:'#111217', cardBg:'#141618', border:'#2d3035', text:'#d9d9d9', muted:'#8e8e8e' };

const HOLD = [90,90,89,88,87,85,82,79,75,70,64,57,49,40,33,27,23];
const PFXS = [48,48,48,47,48,48,47,46,48,47,46,45,44,43,41,39,38];

function Sparkline({ data, color, label, critical }: { data:number[]; color:string; label:string; critical?:number }) {
  const W=280, H=90;
  const max=Math.max(...data)+2, min=Math.max(0,Math.min(...data)-2);
  const tx=(i:number)=>(i/(data.length-1))*W;
  const ty=(v:number)=>H-((v-min)/(max-min+1))*(H-18)-8;
  const pts=data.map((v,i)=>tx(i)+','+ty(v)).join(' ');
  return (
    <div style={{ background:'#0d0e12', borderRadius:8, padding:'10px 12px', border:'1px solid '+C.border }}>
      <div style={{ fontSize:11, color:C.muted, marginBottom:5, fontWeight:600 }}>{label}</div>
      <svg viewBox={"0 0 "+W+" "+H} style={{ width:'100%', height:H }}>
        {critical && <line x1="0" y1={ty(critical)} x2={W} y2={ty(critical)} stroke={C.warning} strokeWidth="1" strokeDasharray="4,3" opacity="0.6"/>}
        <polyline points={pts} fill="none" stroke={color} strokeWidth="2" strokeLinecap="round"/>
        <circle cx={tx(data.length-1)} cy={ty(data[data.length-1])} r="3" fill={color}/>
        <text x={tx(data.length-1)+5} y={ty(data[data.length-1])+4} fill={color} fontSize="11" fontWeight="700">{data[data.length-1]}</text>
      </svg>
    </div>
  );
}

export function DiagnosticPage({ device='PE-3' }: { device?: string }) {
  const alert = MOCK_ALERTS.find(a=>a.device===device) || MOCK_ALERTS[0];
  const col = alert.severity==='CRITICAL'?C.critical:C.warning;
  return (
    <div style={{ display:'flex', flexDirection:'column', height:'100vh', background:C.bg }}>
      <div style={{ padding:'8px 18px', borderBottom:'1px solid '+C.border, fontSize:12, color:C.muted, background:'#0d0e12' }}>
        <a href="/a/noc-copilot/notifications" style={{ color:C.info, textDecoration:'none' }}>← Alerts</a>
        <span style={{ margin:'0 8px' }}>›</span>Fault Diagnosis — {alert.device}
      </div>
      <div style={{ display:'flex', gap:10, padding:'10px 18px', background:'#0d0e12', borderBottom:'1px solid '+C.border, flexWrap:'wrap' }}>
        {[
          ['DEVICE',     alert.device,              '#8ab4f8'],
          ['FAULT TYPE', alert.faultType,            C.text  ],
          ['CONFIDENCE', alert.confidence+'%',       C.ok    ],
          ['TTI',        alert.tti+' min',           col     ],
          ['SEVERITY',   alert.severity,             col     ],
        ].map(([lbl,val,vc])=>(
          <div key={lbl as string} style={{ background:C.cardBg, border:'1px solid '+C.border, borderRadius:6, padding:'8px 14px', minWidth:110 }}>
            <div style={{ fontSize:9, fontWeight:700, color:C.muted, letterSpacing:'0.1em', marginBottom:3 }}>{lbl}</div>
            <div style={{ fontSize:14, fontWeight:700, color:vc as string }}>{val}</div>
          </div>
        ))}
        <div style={{ flex:1 }}/>
        <a href="/a/noc-copilot/network-map" style={{ alignSelf:'center', color:C.muted, fontSize:12, textDecoration:'none' }}>View on Map →</a>
      </div>
      <div style={{ display:'flex', flex:1, overflow:'hidden' }}>
        <div style={{ flex:6, overflowY:'auto', padding:18, borderRight:'1px solid '+C.border }}>
          <div style={{ display:'flex', alignItems:'center', gap:10, marginBottom:14 }}>
            <span style={{ fontSize:18 }}>🤖</span>
            <div>
              <div style={{ fontSize:14, fontWeight:700, color:C.text }}>Copilot Diagnosis</div>
              <div style={{ fontSize:11, color:C.ok }}>● Mistral-7B-Q4 · RAG: topology-meta, runbooks, incidents</div>
            </div>
          </div>
          <CopilotPanel messages={DIAGNOSTIC_CONVO}/>
        </div>
        <div style={{ width:310, flexShrink:0, overflowY:'auto', padding:14, display:'flex', flexDirection:'column', gap:12 }}>
          <div style={{ fontSize:11, fontWeight:700, color:C.muted, letterSpacing:'0.08em' }}>DIAGNOSTIC SIGNALS</div>
          <Sparkline data={HOLD} color={C.critical} label="BGP Hold Timer (s)" critical={30}/>
          <Sparkline data={PFXS} color={C.warning}  label="VPN Prefix Count — PE-3"/>
          <div style={{ background:C.cardBg, border:'1px solid '+C.border, borderRadius:8, padding:12 }}>
            <div style={{ fontWeight:700, color:C.muted, marginBottom:8, fontSize:11, letterSpacing:'0.08em' }}>RAG CONTEXT</div>
            {['topology-meta.json','runbook-bgp-flap.md','incident-2024-03-15.md'].map(s=>(
              <div key={s} style={{ display:'flex', gap:8, alignItems:'center', padding:'4px 0', borderBottom:'1px solid '+C.border+'20' }}>
                <span style={{ color:C.ok }}>✓</span>
                <span style={{ color:'#8ab4f8', fontSize:11 }}>{s}</span>
              </div>
            ))}
          </div>
          <div style={{ background:C.cardBg, border:'1px solid '+C.border, borderRadius:8, padding:12 }}>
            <div style={{ fontWeight:700, color:C.muted, marginBottom:8, fontSize:11, letterSpacing:'0.08em' }}>MODEL STATUS</div>
            {[['Inference','Mistral-7B Q4_K_M',C.ok],['Vector DB','ChromaDB local',C.ok],['Egress','ZERO (air-gapped)',C.ok],['Latency','1.24 s / response',C.info]].map(([l,v,vc])=>(
              <div key={l as string} style={{ display:'flex', justifyContent:'space-between', padding:'3px 0', fontSize:11 }}>
                <span style={{ color:C.muted }}>{l}</span><span style={{ color:vc as string }}>{v}</span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
