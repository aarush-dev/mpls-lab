import React from 'react';
import { CopilotPanel } from '../components/CopilotPanel';
import { DIAGNOSTIC_CONVO } from '../mock/conversations';
import { MOCK_ALERTS } from '../mock/alerts';


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

const HOLD=[90,90,89,88,87,85,82,79,75,70,64,57,49,40,33,27,23];
const PFXS=[48,48,48,47,48,48,47,46,48,47,46,45,44,43,41,39,38];

function Sparkline({ data,color,label,threshold }: { data:number[];color:string;label:string;threshold?:number }) {
  const W=270,H=72;
  const max=Math.max(...data)+3,min=Math.max(0,Math.min(...data)-3);
  const tx=(i:number)=>(i/(data.length-1))*W;
  const ty=(v:number)=>H-((v-min)/(max-min||1))*(H-14)-6;
  const pts=data.map((v,i)=>tx(i)+','+ty(v)).join(' ');
  const last=data[data.length-1];
  return (
    <div style={{ background:G.bg,borderRadius:4,padding:'9px 10px',border:'1px solid '+G.bord }}>
      <div style={{ display:'flex',justifyContent:'space-between',marginBottom:4 }}>
        <span style={{ fontSize:10,color:G.muted,fontWeight:600 }}>{label}</span>
        <span style={{ fontSize:12,fontWeight:700,color,fontFamily:'ui-monospace,monospace' }}>{last}</span>
      </div>
      <svg viewBox={'0 0 '+W+' '+H} style={{ width:'100%',height:H }}>
        {[0.35,0.65].map((p,i)=><line key={i} x1="0" y1={H-(p*(H-14))-6} x2={W} y2={H-(p*(H-14))-6} stroke={G.bord} strokeWidth="0.5"/>)}
        {threshold&&<line x1="0" y1={ty(threshold)} x2={W} y2={ty(threshold)} stroke={G.warn} strokeWidth="1" strokeDasharray="4,3" opacity="0.7"/>}
        <defs><linearGradient id={'sg'+label.replace(/[^a-z]/gi,'')} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity="0.15"/>
          <stop offset="100%" stopColor={color} stopOpacity="0"/>
        </linearGradient></defs>
        <polygon points={'0,'+H+' '+pts+' '+W+','+H} fill={'url(#sg'+label.replace(/[^a-z]/gi,'')+')'}/>
        <polyline points={pts} fill="none" stroke={color} strokeWidth="1.5" strokeLinecap="round"/>
        <circle cx={tx(data.length-1)} cy={ty(last)} r="2.5" fill={color}/>
      </svg>
    </div>
  );
}

export function DiagnosticPage({ device='PE-3' }: { device?: string }) {
  const alert=MOCK_ALERTS.find(a=>a.device===device)||MOCK_ALERTS[0];
  const col=alert.severity==='CRITICAL'?G.crit:G.warn;
  return (
    <div style={{ display:'flex',flexDirection:'column',height:'100vh',background:G.bg }}>
      {/* Breadcrumb */}
      <div style={{ padding:'6px 16px',borderBottom:'1px solid '+G.bord,fontSize:11,color:G.muted,background:G.card,display:'flex',alignItems:'center',gap:6 }}>
        <a href="/a/noc-copilot/notifications" style={{ color:G.info,textDecoration:'none' }}>Alerts</a>
        <span style={{ color:G.dim }}>&rsaquo;</span>
        <span>Fault Diagnosis</span>
        <span style={{ color:G.dim }}>&rsaquo;</span>
        <span style={{ color:G.text,fontFamily:'ui-monospace,monospace',fontWeight:600 }}>{alert.device}</span>
      </div>
      {/* Stat bar */}
      <div style={{ display:'flex',gap:7,padding:'7px 16px',background:G.card,borderBottom:'1px solid '+G.bord,flexWrap:'wrap' }}>
        {([
          ['DEVICE',      alert.device,          G.info],
          ['FAULT TYPE',  alert.faultType,        G.text],
          ['CONFIDENCE',  alert.confidence+'%',   G.ok  ],
          ['TTI',         alert.tti+' min',       col   ],
          ['SEVERITY',    alert.severity,         col   ],
        ] as [string,string,string][]).map(([lbl,val,vc])=>(
          <div key={lbl} style={{ background:G.elev,border:'1px solid '+G.bord,borderRadius:4,padding:'6px 10px',minWidth:100 }}>
            <div style={{ fontSize:9,fontWeight:700,color:G.dim,letterSpacing:'0.09em',marginBottom:2 }}>{lbl}</div>
            <div style={{ fontSize:12,fontWeight:700,color:vc,fontFamily:'ui-monospace,monospace' }}>{val}</div>
          </div>
        ))}
        <div style={{ flex:1 }}/>
        <a href="/a/noc-copilot/network-map" style={{ alignSelf:'center',color:G.muted,fontSize:11,textDecoration:'none' }}>View on Map &rarr;</a>
      </div>
      {/* Body */}
      <div style={{ display:'flex',flex:1,overflow:'hidden' }}>
        {/* Chat panel + input */}
        <div style={{ flex:6,display:'flex',flexDirection:'column',overflow:'hidden',borderRight:'1px solid '+G.bord }}>
          <div style={{ flex:1,overflowY:'auto',padding:'14px 16px' }}>
            <div style={{ display:'flex',alignItems:'center',gap:8,marginBottom:12 }}>
              <div style={{ width:6,height:6,borderRadius:'50%',background:G.ok,display:'inline-block' }}/>
              <span style={{ fontSize:12,fontWeight:600,color:G.text }}>Copilot Diagnosis</span>
              <span style={{ fontSize:10,color:G.muted }}>Mistral-7B-Q4 · RAG: topology-meta, runbooks, incidents</span>
            </div>
            <CopilotPanel messages={DIAGNOSTIC_CONVO}/>
          </div>
          {/* Input area */}
          <div style={{ padding:'8px 12px',borderTop:'1px solid '+G.bord,background:G.card,flexShrink:0 }}>
            <div style={{ display:'flex',gap:8,background:G.elev,border:'1px solid '+G.bord,borderRadius:4,padding:'7px 10px',alignItems:'center' }}>
              <input readOnly placeholder={'Ask about '+alert.device+'…'}
                style={{ flex:1,background:'transparent',border:'none',color:G.muted,fontSize:12,outline:'none' }}/>
              <button style={{ background:G.elev,border:'1px solid '+G.bord,borderRadius:3,
                padding:'4px 12px',color:G.info,fontSize:11,fontWeight:600,cursor:'pointer' }}>Send</button>
            </div>
          </div>
        </div>
        {/* Signals panel */}
        <div style={{ width:290,flexShrink:0,overflowY:'auto',padding:'12px 12px',display:'flex',flexDirection:'column',gap:10 }}>
          <div style={{ fontSize:10,fontWeight:700,color:G.muted,letterSpacing:'0.08em' }}>DIAGNOSTIC SIGNALS</div>
          <Sparkline data={HOLD} color={G.crit} label="BGP Hold Timer (s)" threshold={30}/>
          <Sparkline data={PFXS} color={G.warn} label="VPN Prefix Count — PE-3"/>
          <div style={{ background:G.card,border:'1px solid '+G.bord,borderRadius:4,padding:10 }}>
            <div style={{ fontSize:10,fontWeight:700,color:G.muted,letterSpacing:'0.08em',marginBottom:7 }}>RAG CONTEXT</div>
            {['topology-meta.json','runbook-bgp-flap.md','incident-2024-03-15.md'].map(s=>(
              <div key={s} style={{ display:'flex',gap:7,alignItems:'center',padding:'4px 0',borderBottom:'1px solid '+G.bord+'20' }}>
                <span style={{ color:G.ok,fontSize:11,fontWeight:700 }}>v</span>
                <span style={{ color:G.info,fontSize:11,fontFamily:'ui-monospace,monospace' }}>{s}</span>
              </div>
            ))}
          </div>
          <div style={{ background:G.card,border:'1px solid '+G.bord,borderRadius:4,padding:10 }}>
            <div style={{ fontSize:10,fontWeight:700,color:G.muted,letterSpacing:'0.08em',marginBottom:7 }}>MODEL STATUS</div>
            {([
              ['Inference','Mistral-7B Q4_K_M',G.ok],
              ['Vector DB','ChromaDB local',G.ok],
              ['Egress','ZERO (air-gapped)',G.ok],
              ['Latency','1.24 s / resp',G.info],
            ] as [string,string,string][]).map(([l,v,vc])=>(
              <div key={l} style={{ display:'flex',justifyContent:'space-between',padding:'3px 0',fontSize:11 }}>
                <span style={{ color:G.muted }}>{l}</span>
                <span style={{ color:vc,fontFamily:'ui-monospace,monospace',fontSize:10 }}>{v}</span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
