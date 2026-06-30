import React from 'react';
import { AlertBar } from '../components/AlertBar';
import { CopilotPanel } from '../components/CopilotPanel';
import { PREDICTIVE_CONVO } from '../mock/conversations';

const C = { critical:'#f2495c', warning:'#ff9830', ok:'#73bf69', info:'#5794f2',
  bg:'#111217', cardBg:'#141618', border:'#2d3035', text:'#d9d9d9', muted:'#8e8e8e' };

const UTIL  = [45,47,48,51,53,52,55,58,60,59,63,65,67,66,70,72,74,73,76,78,80,79,82,85,87,86,89,91,90,94];
const LAT   = [12,13,12,14,15,14,16,18,17,19,21,20,23,24,26,25,28,30,29,32,35,34,37,40,39,42,44,43,46,48];
const BGP   = [88,87,88,87,86,85,86,84,83,84,82,81,80,81,79,78,77,76,75,74,73,72,71,70,69,68,67,66,65,64];
const RISK  = [{d:'PE-3',v:91},{d:'CE-Branch-7',v:87},{d:'P-6',v:74},{d:'PE-8',v:69},
               {d:'CE-Branch-12',v:64},{d:'CE-Hub-2',v:58},{d:'PE-5',v:55},{d:'PE-4',v:22}];
const TTIS  = [{device:'PE-3',tti:'8 min',sev:'CRITICAL'},{device:'CE-Branch-7',tti:'11 min',sev:'CRITICAL'},
               {device:'P-6',tti:'18 min',sev:'WARNING'},{device:'PE-8',tti:'23 min',sev:'WARNING'}];

function Chart({ data, color, label, unit='', thr }: { data:number[]; color:string; label:string; unit?:string; thr?:number }) {
  const W=380, H=130;
  const max=Math.max(...data)+5, min=Math.max(0,Math.min(...data)-5);
  const tx=(i:number)=>(i/(data.length-1))*W;
  const ty=(v:number)=>H-((v-min)/(max-min+1))*(H-22)-10;
  const pts=data.map((v,i)=>tx(i)+','+ty(v)).join(' ');
  const gid='g'+label.replace(/W/g,'');
  return (
    <div style={{ background:C.cardBg, borderRadius:8, padding:'12px 14px', border:'1px solid '+C.border }}>
      <div style={{ fontSize:11, color:C.muted, marginBottom:6, fontWeight:600, letterSpacing:'0.04em' }}>{label}</div>
      <svg viewBox={"0 0 "+W+" "+H} style={{ width:'100%', height:H }}>
        <defs><linearGradient id={gid} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity="0.22"/><stop offset="100%" stopColor={color} stopOpacity="0"/>
        </linearGradient></defs>
        {thr && <><line x1="0" y1={ty(thr)} x2={W} y2={ty(thr)} stroke={C.warning} strokeWidth="1" strokeDasharray="4,4" opacity="0.7"/>
          <text x="4" y={ty(thr)-3} fill={C.warning} fontSize="9">SLA {thr}{unit}</text></>}
        <polygon points={"0,"+H+" "+pts+" "+W+","+H} fill={"url(#"+gid+")"}/>
        <polyline points={pts} fill="none" stroke={color} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
        <text x={W-4} y={ty(data[data.length-1])} fill={color} fontSize="11" textAnchor="end" fontWeight="700">{data[data.length-1]}{unit}</text>
      </svg>
    </div>
  );
}

function BarChart() {
  return (
    <div style={{ background:C.cardBg, borderRadius:8, padding:'12px 14px', border:'1px solid '+C.border }}>
      <div style={{ fontSize:11, color:C.muted, marginBottom:10, fontWeight:600, letterSpacing:'0.04em' }}>FAULT PROBABILITY BY DEVICE</div>
      {RISK.map(d => {
        const col = d.v>=80?C.critical:d.v>=60?C.warning:C.ok;
        return <div key={d.d} style={{ marginBottom:7 }}>
          <div style={{ display:'flex', justifyContent:'space-between', marginBottom:2 }}>
            <span style={{ fontSize:11, color:C.text }}>{d.d}</span>
            <span style={{ fontSize:11, color:col, fontWeight:700 }}>{d.v}%</span>
          </div>
          <div style={{ height:5, background:'#1d1f26', borderRadius:3, overflow:'hidden' }}>
            <div style={{ width:d.v+'%', height:'100%', background:col, borderRadius:3 }}/>
          </div>
        </div>;
      })}
    </div>
  );
}

export function PredictivePage() {
  return (
    <div style={{ display:'flex', flexDirection:'column', height:'100vh', overflow:'hidden', background:C.bg }}>
      <AlertBar/>
      <div style={{ display:'flex', flex:1, overflow:'hidden' }}>
        <div style={{ flex:3, overflowY:'auto', padding:'14px 14px 14px 18px', display:'flex', flexDirection:'column', gap:12 }}>
          <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:12 }}>
            <Chart data={UTIL} color={C.warning} label="INTERFACE UTILIZATION FORECAST (%)" unit="%" thr={80}/>
            <Chart data={LAT}  color={C.info}    label="TUNNEL LATENCY TREND (ms)"          unit="ms" thr={35}/>
          </div>
          <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:12 }}>
            <Chart data={BGP}  color={C.ok}      label="BGP ROUTE STABILITY SCORE (%)"      unit="%" thr={65}/>
            <BarChart/>
          </div>
          <div style={{ display:'flex', gap:10 }}>
            {TTIS.map(s => {
              const col = s.sev==='CRITICAL'?C.critical:C.warning;
              return <div key={s.device} style={{ flex:1, background:C.cardBg, border:'1px solid '+col+'40', borderRadius:8, padding:'12px 14px' }}>
                <div style={{ fontSize:10, color:C.muted, marginBottom:3, fontWeight:600, letterSpacing:'0.08em' }}>PREDICTED TTI</div>
                <div style={{ fontSize:24, fontWeight:700, color:col }}>{s.tti}</div>
                <div style={{ fontSize:12, color:C.text, marginTop:2 }}>{s.device}</div>
                <span style={{ fontSize:9, fontWeight:700, color:col, background:col+'18', padding:'1px 6px', borderRadius:3, display:'inline-block', marginTop:4 }}>{s.sev}</span>
              </div>;
            })}
          </div>
        </div>
        <div style={{ width:330, flexShrink:0, borderLeft:'1px solid '+C.border, background:'#0d0f14', display:'flex', flexDirection:'column' }}>
          <div style={{ padding:'12px 14px', borderBottom:'1px solid '+C.border, display:'flex', alignItems:'center', gap:8 }}>
            <span style={{ fontSize:16 }}>🤖</span>
            <div>
              <div style={{ fontSize:13, fontWeight:700, color:C.text }}>AI Copilot</div>
              <div style={{ fontSize:10, color:C.ok }}>● Mistral-7B-Q4 · RAG active</div>
            </div>
          </div>
          <div style={{ flex:1, overflowY:'auto', padding:12 }}><CopilotPanel messages={PREDICTIVE_CONVO}/></div>
          <div style={{ padding:'10px 12px', borderTop:'1px solid '+C.border }}>
            <div style={{ display:'flex', gap:8, background:'#141618', border:'1px solid '+C.border, borderRadius:8, padding:'8px 12px' }}>
              <input readOnly placeholder="Ask about the network…" style={{ flex:1, background:'transparent', border:'none', color:C.muted, fontSize:12, outline:'none' }}/>
              <a href="/a/noc-copilot/assistant" style={{ color:C.info, fontSize:12, textDecoration:'none', alignSelf:'center' }}>Open →</a>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
