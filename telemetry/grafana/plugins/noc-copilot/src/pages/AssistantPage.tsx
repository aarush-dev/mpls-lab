import React from 'react';
import { CopilotPanel } from '../components/CopilotPanel';
import { ASSISTANT_CONVO } from '../mock/conversations';


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


const VRF=[
  { label:'BRANCH-VPN', value:41, color:'#60A5FA' },
  { label:'VOIP',        value:22, color:DS.ok },
  { label:'MGMT',        value:18, color:DS.warning },
  { label:'SCADA',       value:13, color:'#C084FC' },
  { label:'OTHER',       value:6,  color:DS.muted },
];

const RISK=[
  { site:'CE-Branch-7',  v:94, c:DS.critical },
  { site:'CE-Branch-12', v:71, c:DS.warning },
  { site:'CE-Branch-3',  v:44, c:'#FDE68A' },
  { site:'CE-Branch-8',  v:38, c:'#FDE68A' },
  { site:'CE-Hub-2',     v:31, c:DS.ok },
  { site:'CE-Branch-15', v:18, c:DS.ok },
  { site:'CE-DC-1',      v:12, c:DS.ok },
  { site:'PE-7',         v:8,  c:DS.ok },
];

const CHIPS=['Network health summary','Highest risk sites','VRF traffic breakdown','Active fault list'];

function Donut() {
  const total=VRF.reduce((s,d)=>s+d.value,0);
  let cum=0;
  const r=55,ri=r-20,cx=70,cy=70;
  const slices=VRF.map(d=>{
    const start=(cum/total)*360,angle=(d.value/total)*360;
    cum+=d.value;
    const toRad=(deg:number)=>(deg-90)*Math.PI/180;
    const x1=cx+r*Math.cos(toRad(start)),y1=cy+r*Math.sin(toRad(start));
    const x2=cx+r*Math.cos(toRad(start+angle)),y2=cy+r*Math.sin(toRad(start+angle));
    const xi1=cx+ri*Math.cos(toRad(start)),yi1=cy+ri*Math.sin(toRad(start));
    const xi2=cx+ri*Math.cos(toRad(start+angle)),yi2=cy+ri*Math.sin(toRad(start+angle));
    const lg=angle>180?1:0;
    return {...d,path:'M'+x1+','+y1+' A'+r+','+r+' 0 '+lg+' 1 '+x2+','+y2+' L'+xi2+','+yi2+' A'+ri+','+ri+' 0 '+lg+' 0 '+xi1+','+yi1+' Z'};
  });
  return (
    <div style={{ background:DS.card, border:'1px solid '+DS.border, borderRadius:8, padding:'12px 14px' }}>
      <div style={{ fontSize:10, color:DS.muted, fontWeight:700, letterSpacing:'0.08em', marginBottom:10 }}>VRF TRAFFIC DISTRIBUTION</div>
      <div style={{ display:'flex', gap:12, alignItems:'center' }}>
        <svg width="140" height="140" viewBox="0 0 140 140">
          {slices.map(s=><path key={s.label} d={s.path} fill={s.color} opacity="0.9"/>)}
          <text x="70" y="68" textAnchor="middle" fill={DS.text} fontSize="11" fontWeight="700">Traffic</text>
          <text x="70" y="82" textAnchor="middle" fill={DS.muted} fontSize="9">VRF split</text>
        </svg>
        <div style={{ flex:1 }}>
          {VRF.map(d=>(
            <div key={d.label} style={{ display:'flex', alignItems:'center', gap:7, marginBottom:6 }}>
              <div style={{ width:8, height:8, borderRadius:2, background:d.color, flexShrink:0 }}/>
              <span style={{ fontSize:11, color:DS.text, flex:1, fontFamily:'ui-monospace,monospace' }}>{d.label}</span>
              <span style={{ fontSize:11, color:d.color, fontWeight:700, fontFamily:'ui-monospace,monospace' }}>{d.value}%</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function RiskBar() {
  return (
    <div style={{ background:DS.card, border:'1px solid '+DS.border, borderRadius:8, padding:'12px 14px' }}>
      <div style={{ fontSize:10, color:DS.muted, fontWeight:700, letterSpacing:'0.08em', marginBottom:10 }}>30-MIN RISK FORECAST</div>
      {RISK.map((d,i)=>(
        <div key={d.site} style={{ marginBottom:8 }}>
          <div style={{ display:'flex', justifyContent:'space-between', marginBottom:3, alignItems:'center' }}>
            <span style={{ fontSize:11, color:DS.text, fontFamily:'ui-monospace,monospace' }}>
              <span style={{ color:DS.dim, marginRight:4 }}>#{i+1}</span>{d.site}
            </span>
            <span style={{ fontSize:11, color:d.c, fontWeight:700, fontFamily:'ui-monospace,monospace' }}>{d.v}%</span>
          </div>
          <div style={{ height:4, background:DS.elevated, borderRadius:2, overflow:'hidden' }}>
            <div style={{ width:d.v+'%', height:'100%',
              background:'linear-gradient(90deg,'+d.c+'80,'+d.c+')',
              borderRadius:2, boxShadow:d.v>70?'0 0 5px '+d.c+'60':undefined }}/>
          </div>
        </div>
      ))}
    </div>
  );
}

export function AssistantPage() {
  return (
    <div style={{ display:'flex', flexDirection:'column', height:'100vh', background:DS.bg }}>
      {/* Header */}
      <div style={{ display:'flex', alignItems:'center', gap:12, padding:'10px 18px',
        borderBottom:'1px solid '+DS.border, background:DS.card,
        flexShrink:0 }}>
        <div style={{ width:32, height:32, borderRadius:9, background:'rgba(129,140,248,0.15)',
          border:'1px solid rgba(129,140,248,0.35)', display:'flex', alignItems:'center',
          justifyContent:'center', fontSize:18 }}>✦</div>
        <div>
          <div style={{ fontSize:13, fontWeight:700, color:DS.text }}>NOC Copilot — Network Assistant</div>
          <div style={{ fontSize:10, color:DS.ok, display:'flex', alignItems:'center', gap:5 }}>
            <span className="noc-pulse" style={{ width:5,height:5,borderRadius:'50%',background:DS.ok,display:'inline-block',boxShadow:'0 0 4px '+DS.ok }}/>
            Mistral-7B-Q4_K_M · ChromaDB RAG · Zero egress · Air-gapped
          </div>
        </div>
        <div style={{ flex:1 }}/>
        {[['Predictive','predictive'],['Network Map','network-map'],['Alerts','notifications']].map(([l,p])=>(
          <a key={p} href={"/a/noc-copilot/"+p} style={{ color:DS.muted, fontSize:12,
            textDecoration:'none', padding:'4px 10px', borderRadius:5,
            border:'1px solid '+DS.border }}>
            {l}
          </a>
        ))}
      </div>
      {/* Body */}
      <div style={{ display:'flex', flex:1, overflow:'hidden' }}>
        {/* Chat */}
        <div style={{ flex:6, display:'flex', flexDirection:'column', overflow:'hidden' }}>
          <div style={{ flex:1, overflowY:'auto', padding:'18px 22px' }}>
            <CopilotPanel messages={ASSISTANT_CONVO}/>
          </div>
          {/* Input area */}
          <div style={{ padding:'10px 18px 14px', borderTop:'1px solid '+DS.border, background:DS.card }}>
            <div style={{ display:'flex', gap:10, alignItems:'center', background:DS.elevated,
              border:'1px solid '+DS.border, borderRadius:9, padding:'9px 14px',
              boxShadow:'0 0 0 0 transparent', transition:'box-shadow 0.2s' }}>
              <div style={{ width:20, height:20, borderRadius:5, background:'rgba(129,140,248,0.15)',
                display:'flex', alignItems:'center', justifyContent:'center', fontSize:12, flexShrink:0 }}>✦</div>
              <input readOnly placeholder="Ask about your network… (e.g. 'Why is Hub-2 latency elevated?')"
                style={{ flex:1, background:'transparent', border:'none', color:DS.muted,
                  fontSize:13, outline:'none' }}/>
              <button style={{ background:'rgba(129,140,248,0.12)', border:'1px solid rgba(129,140,248,0.3)',
                borderRadius:6, padding:'5px 14px', color:DS.ai, fontSize:12,
                fontWeight:700, cursor:'pointer', letterSpacing:'0.04em' }}>Send</button>
            </div>
            <div style={{ display:'flex', gap:7, marginTop:8, flexWrap:'wrap' }}>
              {CHIPS.map(q=>(
                <span key={q} style={{ fontSize:11, color:DS.muted, background:DS.elevated,
                  border:'1px solid '+DS.border, borderRadius:20, padding:'3px 11px', cursor:'default' }}>{q}</span>
              ))}
            </div>
          </div>
        </div>
        {/* Sidebar charts */}
        <div style={{ width:300, flexShrink:0, borderLeft:'1px solid '+DS.border,
          overflowY:'auto', padding:'12px 12px', display:'flex', flexDirection:'column', gap:10 }}>
          <Donut/>
          <RiskBar/>
        </div>
      </div>
    </div>
  );
}
