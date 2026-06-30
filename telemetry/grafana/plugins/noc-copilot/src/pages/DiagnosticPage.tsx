import React from 'react';
import { CopilotPanel } from '../components/CopilotPanel';
import { DIAGNOSTIC_CONVO } from '../mock/conversations';
import { MOCK_ALERTS } from '../mock/alerts';


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


const HOLD=[90,90,89,88,87,85,82,79,75,70,64,57,49,40,33,27,23];
const PFXS=[48,48,48,47,48,48,47,46,48,47,46,45,44,43,41,39,38];

function Sparkline({ data, color, label, threshold }: { data:number[]; color:string; label:string; threshold?:number }) {
  const W=280, H=80;
  const max=Math.max(...data)+3, min=Math.max(0,Math.min(...data)-3);
  const tx=(i:number)=>(i/(data.length-1))*W;
  const ty=(v:number)=>H-((v-min)/(max-min||1))*(H-16)-8;
  const pts=data.map((v,i)=>tx(i)+','+ty(v)).join(' ');
  const last=data[data.length-1];
  return (
    <div style={{ background:'rgba(0,0,0,0.3)', borderRadius:7, padding:'10px 12px', border:'1px solid '+DS.border }}>
      <div style={{ display:'flex', justifyContent:'space-between', marginBottom:5 }}>
        <div style={{ fontSize:10, color:DS.muted, fontWeight:600 }}>{label}</div>
        <div style={{ fontSize:12, fontWeight:700, color, fontFamily:'ui-monospace,monospace' }}>{last}</div>
      </div>
      <svg viewBox={"0 0 "+W+" "+H} style={{ width:'100%', height:H }}>
        {[0.3,0.6].map((p,i)=>{
          const gy=H-(p*(H-16))-8;
          return <line key={i} x1="0" y1={gy} x2={W} y2={gy} stroke={DS.border} strokeWidth="0.5" opacity="0.7"/>;
        })}
        {threshold&&<line x1="0" y1={ty(threshold)} x2={W} y2={ty(threshold)} stroke={DS.warning} strokeWidth="1" strokeDasharray="4,3" opacity="0.7"/>}
        <defs><linearGradient id={"sg"+label.replace(/[^a-z]/gi,'')} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity="0.2"/>
          <stop offset="100%" stopColor={color} stopOpacity="0"/>
        </linearGradient></defs>
        <polygon points={"0,"+H+" "+pts+" "+W+","+H} fill={"url(#sg"+label.replace(/[^a-z]/gi,'')+")"}/>
        <polyline points={pts} fill="none" stroke={color} strokeWidth="1.5" strokeLinecap="round"/>
        <circle cx={tx(data.length-1)} cy={ty(last)} r="3" fill={color}/>
      </svg>
    </div>
  );
}

export function DiagnosticPage({ device='PE-3' }: { device?: string }) {
  const alert=MOCK_ALERTS.find(a=>a.device===device)||MOCK_ALERTS[0];
  const col=alert.severity==='CRITICAL'?DS.critical:DS.warning;
  return (
    <div style={{ display:'flex', flexDirection:'column', height:'100vh', background:DS.bg }}>
      {/* Breadcrumb */}
      <div style={{ padding:'7px 18px', borderBottom:'1px solid '+DS.border,
        fontSize:11, color:DS.muted, background:DS.card, display:'flex', alignItems:'center', gap:6 }}>
        <a href="/a/noc-copilot/notifications" style={{ color:DS.ai, textDecoration:'none', fontWeight:600 }}>← Alerts</a>
        <span style={{ color:DS.dim }}>›</span>
        <span>Fault Diagnosis</span>
        <span style={{ color:DS.dim }}>›</span>
        <span style={{ color:DS.text, fontFamily:'ui-monospace,monospace', fontWeight:600 }}>{alert.device}</span>
      </div>
      {/* Stat bar */}
      <div style={{ display:'flex', gap:8, padding:'8px 18px', background:DS.card,
        borderBottom:'1px solid '+DS.border, flexWrap:'wrap' }}>
        {([
          ['DEVICE', alert.device, '#93C5FD'],
          ['FAULT TYPE', alert.faultType, DS.text],
          ['CONFIDENCE', alert.confidence+'%', DS.ok],
          ['TIME TO IMPACT', alert.tti+' min', col],
          ['SEVERITY', alert.severity, col],
        ] as [string,string,string][]).map(([lbl,val,vc])=>(
          <div key={lbl} style={{ background:DS.elevated, border:'1px solid '+DS.border,
            borderRadius:6, padding:'7px 12px', minWidth:110 }}>
            <div style={{ fontSize:9, fontWeight:700, color:DS.dim, letterSpacing:'0.1em', marginBottom:3 }}>{lbl}</div>
            <div style={{ fontSize:13, fontWeight:700, color:vc, fontFamily:'ui-monospace,monospace' }}>{val}</div>
          </div>
        ))}
        <div style={{ flex:1 }}/>
        <a href="/a/noc-copilot/network-map" style={{ alignSelf:'center', color:DS.muted, fontSize:11, textDecoration:'none' }}>View on Map →</a>
      </div>
      {/* Body */}
      <div style={{ display:'flex', flex:1, overflow:'hidden' }}>
        {/* Copilot chat */}
        <div style={{ flex:6, overflowY:'auto', padding:'16px 20px', borderRight:'1px solid '+DS.border }}>
          <div style={{ display:'flex', alignItems:'center', gap:10, marginBottom:14 }}>
            <div style={{ width:30, height:30, borderRadius:8, background:'rgba(129,140,248,0.12)',
              border:'1px solid rgba(129,140,248,0.3)', display:'flex', alignItems:'center',
              justifyContent:'center', fontSize:16 }}>✦</div>
            <div>
              <div style={{ fontSize:13, fontWeight:700, color:DS.text }}>Copilot Diagnosis</div>
              <div style={{ fontSize:10, color:DS.ok, display:'flex', alignItems:'center', gap:4 }}>
                <span className="noc-pulse" style={{ width:5,height:5,borderRadius:'50%',background:DS.ok,display:'inline-block'}}/>
                Mistral-7B-Q4 · RAG: topology-meta, runbooks, incidents
              </div>
            </div>
          </div>
          <CopilotPanel messages={DIAGNOSTIC_CONVO}/>
        </div>
        {/* Signals panel */}
        <div style={{ width:300, flexShrink:0, overflowY:'auto', padding:'14px 14px',
          display:'flex', flexDirection:'column', gap:12 }}>
          <div style={{ fontSize:10, fontWeight:700, color:DS.muted, letterSpacing:'0.1em' }}>DIAGNOSTIC SIGNALS</div>
          <Sparkline data={HOLD} color={DS.critical} label="BGP Hold Timer (s)" threshold={30}/>
          <Sparkline data={PFXS} color={DS.warning}  label="VPN Prefix Count — PE-3"/>
          {/* RAG context */}
          <div style={{ background:DS.card, border:'1px solid '+DS.border, borderRadius:8, padding:12 }}>
            <div style={{ fontSize:10, fontWeight:700, color:DS.muted, letterSpacing:'0.1em', marginBottom:9 }}>RAG CONTEXT</div>
            {['topology-meta.json','runbook-bgp-flap.md','incident-2024-03-15.md'].map(s=>(
              <div key={s} style={{ display:'flex', alignItems:'center', gap:8, padding:'5px 0',
                borderBottom:'1px solid '+DS.borderSubtle }}>
                <span style={{ color:DS.ok, fontSize:11 }}>✓</span>
                <span style={{ color:'#93C5FD', fontSize:11, fontFamily:'ui-monospace,monospace' }}>{s}</span>
              </div>
            ))}
          </div>
          {/* Model status */}
          <div style={{ background:DS.card, border:'1px solid '+DS.border, borderRadius:8, padding:12 }}>
            <div style={{ fontSize:10, fontWeight:700, color:DS.muted, letterSpacing:'0.1em', marginBottom:9 }}>MODEL STATUS</div>
            {([
              ['Inference','Mistral-7B Q4_K_M',DS.ok],
              ['Vector DB','ChromaDB local',DS.ok],
              ['Egress','ZERO (air-gapped)',DS.ok],
              ['Latency','1.24 s / response',DS.info],
            ] as [string,string,string][]).map(([l,v,vc])=>(
              <div key={l} style={{ display:'flex', justifyContent:'space-between', padding:'4px 0', fontSize:11 }}>
                <span style={{ color:DS.muted }}>{l}</span>
                <span style={{ color:vc, fontFamily:'ui-monospace,monospace', fontSize:10 }}>{v}</span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
