import React from 'react';
import { AlertBar } from '../components/AlertBar';
import { CopilotPanel } from '../components/CopilotPanel';
import { PREDICTIVE_CONVO } from '../mock/conversations';


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


const UTIL=[45,47,48,51,53,52,55,58,60,59,63,65,67,66,70,72,74,73,76,78,80,79,82,85,87,86,89,91,90,94];
const LAT=[12,13,12,14,15,14,16,18,17,19,21,20,23,24,26,25,28,30,29,32,35,34,37,40,39,42,44,43,46,48];
const BGP=[88,87,88,87,86,85,86,84,83,84,82,81,80,81,79,78,77,76,75,74,73,72,71,70,69,68,67,66,65,64];
const RISK=[{d:'PE-3',v:91},{d:'CE-Branch-7',v:87},{d:'P-6',v:74},{d:'PE-8',v:69},{d:'CE-Branch-12',v:64},{d:'CE-Hub-2',v:58},{d:'PE-5',v:55},{d:'PE-4',v:22}];
const TTIS=[{device:'PE-3',tti:'8 min',sev:'CRITICAL'},{device:'CE-Branch-7',tti:'11 min',sev:'CRITICAL'},{device:'P-6',tti:'18 min',sev:'WARNING'},{device:'PE-8',tti:'23 min',sev:'WARNING'}];

function Chart({ data, color, label, unit='', thr }: { data:number[]; color:string; label:string; unit?:string; thr?:number }) {
  const W=380, H=120;
  const max=Math.max(...data)+5, min=Math.max(0,Math.min(...data)-5);
  const tx=(i:number)=>(i/(data.length-1))*W;
  const ty=(v:number)=>H-((v-min)/(max-min||1))*(H-22)-10;
  const pts=data.map((v,i)=>tx(i)+','+ty(v)).join(' ');
  const gid='g'+label.replace(/[^a-zA-Z]/g,'');
  const gridVals=[0.25,0.5,0.75].map(p=>min+(max-min)*p);
  const last=data[data.length-1];
  const trend=last>data[data.length-4]?'▲':'▼';
  const isBad=thr?(color===DS.warning&&last>thr)||(color===DS.info&&last>thr)||(color===DS.ok&&last<thr):false;
  return (
    <div style={{ background:DS.card, borderRadius:8, padding:'12px 14px',
      border:'1px solid '+DS.border,
      boxShadow:'inset 0 1px 0 rgba(255,255,255,0.03)' }}>
      <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', marginBottom:8 }}>
        <div style={{ fontSize:10, color:DS.muted, fontWeight:700, letterSpacing:'0.08em' }}>{label}</div>
        <div style={{ fontFamily:'ui-monospace,monospace', fontSize:13, fontWeight:700,
          color: isBad ? DS.critical : color }}>
          {last}{unit} <span style={{ fontSize:10 }}>{trend}</span>
        </div>
      </div>
      <svg viewBox={"0 0 "+W+" "+H} style={{ width:'100%', height:H }}>
        <defs><linearGradient id={gid} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity="0.18"/>
          <stop offset="100%" stopColor={color} stopOpacity="0"/>
        </linearGradient></defs>
        {gridVals.map((v,i)=>{
          const gy=ty(v);
          return <g key={i}>
            <line x1="0" y1={gy} x2={W} y2={gy} stroke={DS.border} strokeWidth="0.5" opacity="0.7"/>
            <text x="2" y={gy-2} fill={DS.dim} fontSize="8" fontFamily="ui-monospace,monospace">{Math.round(v)}</text>
          </g>;
        })}
        {thr && <><line x1="0" y1={ty(thr)} x2={W} y2={ty(thr)} stroke={DS.warning} strokeWidth="1" strokeDasharray="5,4" opacity="0.7"/>
          <text x={W-2} y={ty(thr)-3} fill={DS.warning} fontSize="8" textAnchor="end">SLA {thr}{unit}</text></>}
        <polygon points={"0,"+H+" "+pts+" "+W+","+H} fill={"url(#"+gid+")"}/>
        <polyline points={pts} fill="none" stroke={color} strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
        <circle cx={tx(data.length-1)} cy={ty(last)} r="3" fill={color}/>
      </svg>
    </div>
  );
}

function BarChart() {
  return (
    <div style={{ background:DS.card, borderRadius:8, padding:'12px 14px', border:'1px solid '+DS.border, boxShadow:'inset 0 1px 0 rgba(255,255,255,0.03)' }}>
      <div style={{ fontSize:10, color:DS.muted, fontWeight:700, letterSpacing:'0.08em', marginBottom:10 }}>FAULT PROBABILITY BY DEVICE</div>
      {RISK.map(d=>{
        const col=d.v>=80?DS.critical:d.v>=60?DS.warning:DS.ok;
        return (
          <div key={d.d} style={{ marginBottom:8 }}>
            <div style={{ display:'flex', justifyContent:'space-between', marginBottom:3, alignItems:'center' }}>
              <span style={{ fontSize:11, color:DS.text, fontFamily:'ui-monospace,monospace' }}>{d.d}</span>
              <span style={{ fontSize:11, color:col, fontWeight:700, fontFamily:'ui-monospace,monospace' }}>{d.v}%</span>
            </div>
            <div style={{ height:4, background:DS.elevated, borderRadius:2, overflow:'hidden' }}>
              <div style={{ width:d.v+'%', height:'100%',
                background:'linear-gradient(90deg,'+col+'90,'+col+')',
                borderRadius:2, boxShadow:'0 0 6px '+col+'60' }}/>
            </div>
          </div>
        );
      })}
    </div>
  );
}

export function PredictivePage() {
  return (
    <div style={{ display:'flex', flexDirection:'column', height:'100vh', overflow:'hidden', background:DS.bg }}>
      <AlertBar/>
      <div style={{ display:'flex', flex:1, overflow:'hidden' }}>
        {/* Main charts area */}
        <div style={{ flex:3, overflowY:'auto', padding:'14px 14px 14px 16px', display:'flex', flexDirection:'column', gap:10 }}>
          <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:10 }}>
            <Chart data={UTIL} color={DS.warning} label="INTERFACE UTILIZATION FORECAST (%)" unit="%" thr={80}/>
            <Chart data={LAT}  color={DS.info}    label="TUNNEL LATENCY TREND (ms)" unit="ms" thr={35}/>
          </div>
          <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:10 }}>
            <Chart data={BGP}  color={DS.ok}      label="BGP ROUTE STABILITY SCORE (%)" unit="%"/>
            <BarChart/>
          </div>
          {/* TTI stat tiles */}
          <div style={{ display:'grid', gridTemplateColumns:'repeat(4,1fr)', gap:10 }}>
            {TTIS.map(s=>{
              const col=s.sev==='CRITICAL'?DS.critical:DS.warning;
              return (
                <div key={s.device} style={{ background:DS.card,
                  border:'1px solid '+col+'35',
                  borderTop:'2px solid '+col,
                  borderRadius:8, padding:'12px 14px' }}>
                  <div style={{ fontSize:9, color:DS.muted, fontWeight:700, letterSpacing:'0.1em', marginBottom:6 }}>PREDICTED TTI</div>
                  <div style={{ fontSize:26, fontWeight:800, color:col, fontFamily:'ui-monospace,monospace', lineHeight:1 }}>{s.tti}</div>
                  <div style={{ fontSize:11, color:DS.text, marginTop:6, fontFamily:'ui-monospace,monospace' }}>{s.device}</div>
                  <div style={{ display:'inline-block', fontSize:9, fontWeight:700, color:col,
                    background:col+'15', border:'1px solid '+col+'30',
                    borderRadius:3, padding:'1px 6px', marginTop:5, letterSpacing:'0.08em' }}>{s.sev}</div>
                </div>
              );
            })}
          </div>
        </div>
        {/* Copilot sidebar */}
        <div style={{ width:320, flexShrink:0, borderLeft:'1px solid '+DS.border,
          background:'linear-gradient(180deg,#040D1E 0%,'+DS.bg+' 100%)',
          display:'flex', flexDirection:'column' }}>
          <div style={{ padding:'12px 14px', borderBottom:'1px solid '+DS.borderSubtle,
            background:'rgba(129,140,248,0.05)' }}>
            <div style={{ display:'flex', alignItems:'center', gap:8 }}>
              <div style={{ width:28, height:28, borderRadius:7, background:'rgba(129,140,248,0.15)',
                border:'1px solid rgba(129,140,248,0.3)', display:'flex', alignItems:'center',
                justifyContent:'center', fontSize:14 }}>✦</div>
              <div>
                <div style={{ fontSize:12, fontWeight:700, color:DS.text, letterSpacing:'0.02em' }}>AI Copilot</div>
                <div style={{ fontSize:10, color:DS.ok, display:'flex', alignItems:'center', gap:4 }}>
                  <span style={{ width:5, height:5, borderRadius:'50%', background:DS.ok,
                    display:'inline-block', boxShadow:'0 0 4px '+DS.ok }} className="noc-pulse"/>
                  Mistral-7B-Q4 · RAG active
                </div>
              </div>
            </div>
          </div>
          <div style={{ flex:1, overflowY:'auto', padding:'12px 12px' }}>
            <CopilotPanel messages={PREDICTIVE_CONVO}/>
          </div>
          <div style={{ padding:'8px 10px', borderTop:'1px solid '+DS.borderSubtle }}>
            <div style={{ display:'flex', gap:6, background:DS.elevated, border:'1px solid '+DS.border,
              borderRadius:7, padding:'7px 10px', alignItems:'center' }}>
              <input readOnly placeholder="Ask about the network…"
                style={{ flex:1, background:'transparent', border:'none', color:DS.muted,
                  fontSize:12, outline:'none' }}/>
              <a href="/a/noc-copilot/assistant" style={{ color:DS.ai, fontSize:11,
                textDecoration:'none', fontWeight:600, flexShrink:0 }}>Full chat →</a>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
