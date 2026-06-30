import React from 'react';
import { AlertBar } from '../components/AlertBar';
import { CopilotPanel } from '../components/CopilotPanel';
import { PREDICTIVE_CONVO } from '../mock/conversations';


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

const UTIL=[45,47,48,51,53,52,55,58,60,59,63,65,67,66,70,72,74,73,76,78,80,79,82,85,87,86,89,91,90,94];
const LAT=[12,13,12,14,15,14,16,18,17,19,21,20,23,24,26,25,28,30,29,32,35,34,37,40,39,42,44,43,46,48];
const BGP=[88,87,88,87,86,85,86,84,83,84,82,81,80,81,79,78,77,76,75,74,73,72,71,70,69,68,67,66,65,64];
const RISK=[{d:'PE-3',v:91},{d:'CE-Branch-7',v:87},{d:'P-6',v:74},{d:'PE-8',v:69},{d:'CE-Branch-12',v:64},{d:'CE-Hub-2',v:58},{d:'PE-5',v:55},{d:'PE-4',v:22}];
const TTIS=[{device:'PE-3',tti:'8 min',sev:'CRITICAL'},{device:'CE-Branch-7',tti:'11 min',sev:'CRITICAL'},{device:'P-6',tti:'18 min',sev:'WARNING'},{device:'PE-8',tti:'23 min',sev:'WARNING'}];

function Chart({ data, color, label, unit='', thr }: { data:number[];color:string;label:string;unit?:string;thr?:number }) {
  const W=380,H=110;
  const max=Math.max(...data)+5,min=Math.max(0,Math.min(...data)-5);
  const tx=(i:number)=>(i/(data.length-1))*W;
  const ty=(v:number)=>H-((v-min)/(max-min||1))*(H-20)-8;
  const pts=data.map((v,i)=>tx(i)+','+ty(v)).join(' ');
  const gid='g'+label.replace(/[^a-zA-Z]/g,'');
  const last=data[data.length-1];
  return (
    <div style={{ background:G.card,borderRadius:4,padding:'10px 12px',border:'1px solid '+G.bord }}>
      <div style={{ display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:6 }}>
        <span style={{ fontSize:10,color:G.muted,fontWeight:600,letterSpacing:'0.07em' }}>{label}</span>
        <span style={{ fontSize:13,fontWeight:700,color,fontFamily:'ui-monospace,monospace' }}>{last}{unit}</span>
      </div>
      <svg viewBox={'0 0 '+W+' '+H} style={{ width:'100%',height:H }}>
        <defs><linearGradient id={gid} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity="0.15"/>
          <stop offset="100%" stopColor={color} stopOpacity="0"/>
        </linearGradient></defs>
        {[0.25,0.5,0.75].map((p,i)=>{
          const gy=H-(p*(H-20))-8;
          return <line key={i} x1="0" y1={gy} x2={W} y2={gy} stroke={G.bord} strokeWidth="0.5"/>;
        })}
        {thr&&<><line x1="0" y1={ty(thr)} x2={W} y2={ty(thr)} stroke={G.warn} strokeWidth="1" strokeDasharray="4,3" opacity="0.6"/>
          <text x={W-2} y={ty(thr)-3} fill={G.warn} fontSize="8" textAnchor="end">SLA {thr}{unit}</text></>}
        <polygon points={'0,'+H+' '+pts+' '+W+','+H} fill={'url(#'+gid+')'}/>
        <polyline points={pts} fill="none" stroke={color} strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
        <circle cx={tx(data.length-1)} cy={ty(last)} r="2.5" fill={color}/>
      </svg>
    </div>
  );
}

function RiskBars() {
  return (
    <div style={{ background:G.card,borderRadius:4,padding:'10px 12px',border:'1px solid '+G.bord }}>
      <div style={{ fontSize:10,color:G.muted,fontWeight:600,letterSpacing:'0.07em',marginBottom:8 }}>FAULT PROBABILITY BY DEVICE</div>
      {RISK.map(d=>{
        const col=d.v>=80?G.crit:d.v>=60?G.warn:G.ok;
        return (
          <div key={d.d} style={{ marginBottom:7 }}>
            <div style={{ display:'flex',justifyContent:'space-between',marginBottom:2 }}>
              <span style={{ fontSize:11,color:G.text,fontFamily:'ui-monospace,monospace' }}>{d.d}</span>
              <span style={{ fontSize:11,color:col,fontWeight:700,fontFamily:'ui-monospace,monospace' }}>{d.v}%</span>
            </div>
            <div style={{ height:4,background:G.elev,borderRadius:2,overflow:'hidden' }}>
              <div style={{ width:d.v+'%',height:'100%',background:col,borderRadius:2 }}/>
            </div>
          </div>
        );
      })}
    </div>
  );
}

export function PredictivePage() {
  return (
    <div style={{ display:'flex',flexDirection:'column',height:'100vh',overflow:'hidden',background:G.bg }}>
      <AlertBar/>
      <div style={{ display:'flex',flex:1,overflow:'hidden' }}>
        <div style={{ flex:3,overflowY:'auto',padding:'12px 12px 12px 14px',display:'flex',flexDirection:'column',gap:10 }}>
          <div style={{ display:'grid',gridTemplateColumns:'1fr 1fr',gap:10 }}>
            <Chart data={UTIL} color={G.warn} label="INTERFACE UTILIZATION FORECAST (%)" unit="%" thr={80}/>
            <Chart data={LAT}  color={G.info} label="TUNNEL LATENCY TREND (ms)" unit="ms" thr={35}/>
          </div>
          <div style={{ display:'grid',gridTemplateColumns:'1fr 1fr',gap:10 }}>
            <Chart data={BGP}  color={G.ok}   label="BGP ROUTE STABILITY SCORE (%)"/>
            <RiskBars/>
          </div>
          <div style={{ display:'grid',gridTemplateColumns:'repeat(4,1fr)',gap:10 }}>
            {TTIS.map(s=>{
              const col=s.sev==='CRITICAL'?G.crit:G.warn;
              return (
                <div key={s.device} style={{ background:G.card,border:'1px solid '+col+'30',
                  borderTop:'2px solid '+col,borderRadius:4,padding:'10px 12px' }}>
                  <div style={{ fontSize:9,color:G.muted,fontWeight:700,letterSpacing:'0.1em',marginBottom:5 }}>PREDICTED TTI</div>
                  <div style={{ fontSize:24,fontWeight:700,color:col,fontFamily:'ui-monospace,monospace',lineHeight:1 }}>{s.tti}</div>
                  <div style={{ fontSize:11,color:G.text,marginTop:5,fontFamily:'ui-monospace,monospace' }}>{s.device}</div>
                  <div style={{ display:'inline-block',fontSize:9,fontWeight:700,color:col,
                    background:col+'12',border:'1px solid '+col+'25',borderRadius:3,
                    padding:'1px 5px',marginTop:4,letterSpacing:'0.07em' }}>{s.sev}</div>
                </div>
              );
            })}
          </div>
        </div>
        {/* Copilot sidebar */}
        <div style={{ width:310,flexShrink:0,borderLeft:'1px solid '+G.bord,background:G.card,display:'flex',flexDirection:'column' }}>
          <div style={{ padding:'9px 12px',borderBottom:'1px solid '+G.bord }}>
            <div style={{ fontSize:12,fontWeight:600,color:G.text }}>AI Copilot</div>
            <div style={{ fontSize:10,color:G.ok,marginTop:2,display:'flex',alignItems:'center',gap:5 }}>
              <span style={{ width:6,height:6,borderRadius:'50%',background:G.ok,display:'inline-block' }}/>
              Mistral-7B-Q4 · RAG active
            </div>
          </div>
          <div style={{ flex:1,overflowY:'auto',padding:'10px 10px' }}>
            <CopilotPanel messages={PREDICTIVE_CONVO}/>
          </div>
          <div style={{ padding:'8px 10px',borderTop:'1px solid '+G.bord }}>
            <div style={{ display:'flex',gap:6,background:G.elev,border:'1px solid '+G.bord,borderRadius:4,padding:'6px 10px' }}>
              <input readOnly placeholder="Ask about the network…"
                style={{ flex:1,background:'transparent',border:'none',color:G.muted,fontSize:12,outline:'none' }}/>
              <a href="/a/noc-copilot/assistant" style={{ color:G.info,fontSize:11,textDecoration:'none',fontWeight:600,flexShrink:0 }}>Open →</a>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
