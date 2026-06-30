import React from 'react';
import { CopilotPanel } from '../components/CopilotPanel';
import { ASSISTANT_CONVO } from '../mock/conversations';

const C = { critical:'#f2495c', warning:'#ff9830', ok:'#73bf69', info:'#5794f2',
  bg:'#111217', cardBg:'#141618', border:'#2d3035', text:'#d9d9d9', muted:'#8e8e8e' };

const VRF = [
  { label:'BRANCH-VPN', value:41, color:'#5794f2' },
  { label:'VOIP',        value:22, color:'#73bf69' },
  { label:'MGMT',        value:18, color:'#ff9830' },
  { label:'SCADA',       value:13, color:'#e040fb' },
  { label:'OTHER',       value:6,  color:'#8e8e8e' },
];

const RISK = [
  { site:'CE-Branch-7',  v:94, c:'#f2495c' },
  { site:'CE-Branch-12', v:71, c:'#ff9830' },
  { site:'CE-Branch-3',  v:44, c:'#fade2a' },
  { site:'CE-Branch-8',  v:38, c:'#fade2a' },
  { site:'CE-Hub-2',     v:31, c:'#73bf69' },
  { site:'CE-Branch-15', v:18, c:'#73bf69' },
  { site:'CE-DC-1',      v:12, c:'#73bf69' },
  { site:'PE-7',         v:8,  c:'#73bf69' },
];

function Donut() {
  const total = VRF.reduce((s,d)=>s+d.value,0);
  let cum = 0;
  const r=65, ri=r-24, cx=80, cy=80;
  const slices = VRF.map(d => {
    const start = (cum/total)*360, angle = (d.value/total)*360;
    cum += d.value;
    const toRad = (deg:number) => (deg-90)*Math.PI/180;
    const x1=cx+r*Math.cos(toRad(start)), y1=cy+r*Math.sin(toRad(start));
    const x2=cx+r*Math.cos(toRad(start+angle)), y2=cy+r*Math.sin(toRad(start+angle));
    const xi1=cx+ri*Math.cos(toRad(start)), yi1=cy+ri*Math.sin(toRad(start));
    const xi2=cx+ri*Math.cos(toRad(start+angle)), yi2=cy+ri*Math.sin(toRad(start+angle));
    const lg = angle>180?1:0;
    return { ...d, path:'M'+x1+','+y1+' A'+r+','+r+' 0 '+lg+' 1 '+x2+','+y2+' L'+xi2+','+yi2+' A'+ri+','+ri+' 0 '+lg+' 0 '+xi1+','+yi1+' Z' };
  });
  return (
    <div style={{ background:C.cardBg, border:'1px solid '+C.border, borderRadius:8, padding:'12px 14px' }}>
      <div style={{ fontSize:11, color:C.muted, marginBottom:8, fontWeight:600, letterSpacing:'0.08em' }}>VRF TRAFFIC DISTRIBUTION</div>
      <div style={{ display:'flex', gap:14, alignItems:'center' }}>
        <svg width="160" height="160" viewBox="0 0 160 160">
          {slices.map(s=><path key={s.label} d={s.path} fill={s.color} opacity="0.85"/>)}
          <text x="80" y="77" textAnchor="middle" fill={C.text} fontSize="13" fontWeight="700">Traffic</text>
          <text x="80" y="92" textAnchor="middle" fill={C.muted} fontSize="10">VRF split</text>
        </svg>
        <div style={{ flex:1 }}>
          {VRF.map(d=>(
            <div key={d.label} style={{ display:'flex', alignItems:'center', gap:7, marginBottom:5 }}>
              <div style={{ width:9, height:9, borderRadius:2, background:d.color, flexShrink:0 }}/>
              <span style={{ fontSize:11, color:C.text, flex:1 }}>{d.label}</span>
              <span style={{ fontSize:11, color:d.color, fontWeight:700 }}>{d.value}%</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function RiskBar() {
  return (
    <div style={{ background:C.cardBg, border:'1px solid '+C.border, borderRadius:8, padding:'12px 14px' }}>
      <div style={{ fontSize:11, color:C.muted, marginBottom:10, fontWeight:600, letterSpacing:'0.08em' }}>30-MIN RISK FORECAST</div>
      {RISK.map((d,i)=>(
        <div key={d.site} style={{ marginBottom:7 }}>
          <div style={{ display:'flex', justifyContent:'space-between', marginBottom:2 }}>
            <span style={{ fontSize:11, color:C.text }}>#{i+1} {d.site}</span>
            <span style={{ fontSize:11, color:d.c, fontWeight:700 }}>{d.v}%</span>
          </div>
          <div style={{ height:5, background:'#1d1f26', borderRadius:3, overflow:'hidden' }}>
            <div style={{ width:d.v+'%', height:'100%', background:d.c, borderRadius:3 }}/>
          </div>
        </div>
      ))}
    </div>
  );
}

export function AssistantPage() {
  return (
    <div style={{ display:'flex', flexDirection:'column', height:'100vh', background:C.bg }}>
      <div style={{ display:'flex', alignItems:'center', gap:12, padding:'12px 18px', borderBottom:'1px solid '+C.border, background:'#0d0e12' }}>
        <span style={{ fontSize:20 }}>🤖</span>
        <div>
          <div style={{ fontSize:14, fontWeight:700, color:C.text }}>NOC Copilot — Network Assistant</div>
          <div style={{ fontSize:11, color:C.ok }}>● Mistral-7B-Q4_K_M · ChromaDB RAG · Zero egress · Air-gapped</div>
        </div>
        <div style={{ flex:1 }}/>
        {[['Predictive','predictive'],['Network Map','network-map'],['Alerts','notifications']].map(([l,p])=>(
          <a key={p} href={"/a/noc-copilot/"+p} style={{ color:C.muted, fontSize:12, textDecoration:'none', padding:'4px 10px', borderRadius:4, border:'1px solid '+C.border }}>{l}</a>
        ))}
      </div>
      <div style={{ display:'flex', flex:1, overflow:'hidden' }}>
        <div style={{ flex:6, display:'flex', flexDirection:'column', overflow:'hidden' }}>
          <div style={{ flex:1, overflowY:'auto', padding:'18px 22px' }}>
            <CopilotPanel messages={ASSISTANT_CONVO}/>
          </div>
          <div style={{ padding:'10px 18px', borderTop:'1px solid '+C.border, background:'#0d0e12' }}>
            <div style={{ display:'flex', gap:10, alignItems:'center', background:C.cardBg, border:'1px solid '+C.border, borderRadius:10, padding:'9px 14px' }}>
              <span style={{ fontSize:15 }}>🤖</span>
              <input readOnly placeholder="Ask about your network… (e.g. 'Why is Hub-2 latency elevated?')"
                style={{ flex:1, background:'transparent', border:'none', color:C.muted, fontSize:13, outline:'none' }}/>
              <button style={{ background:C.info+'20', border:'1px solid '+C.info+'40', borderRadius:6, padding:'6px 14px', color:C.info, fontSize:12, fontWeight:600, cursor:'pointer' }}>Send</button>
            </div>
            <div style={{ display:'flex', gap:8, marginTop:8, flexWrap:'wrap' }}>
              {['Network health summary','Highest risk sites','VRF traffic breakdown','Active fault list'].map(q=>(
                <span key={q} style={{ fontSize:11, color:C.muted, background:'#1a1c21', border:'1px solid '+C.border, borderRadius:12, padding:'3px 10px' }}>{q}</span>
              ))}
            </div>
          </div>
        </div>
        <div style={{ width:310, flexShrink:0, borderLeft:'1px solid '+C.border, overflowY:'auto', padding:14, display:'flex', flexDirection:'column', gap:12 }}>
          <Donut/>
          <RiskBar/>
        </div>
      </div>
    </div>
  );
}
