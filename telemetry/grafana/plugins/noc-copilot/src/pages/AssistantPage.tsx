import React from 'react';
import { CopilotPanel } from '../components/CopilotPanel';
import { ASSISTANT_CONVO } from '../mock/conversations';


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

const VRF=[
  { label:'CORP',   value:41, color:G.info },
  { label:'VOICE',  value:22, color:G.ok   },
  { label:'MGMT',   value:18, color:G.warn  },
  { label:'SCADA',  value:13, color:'#b877d9'},
  { label:'GUEST',  value:6,  color:G.muted  },
];

const RISK=[
  { site:'CE-Branch-7',  v:94, c:G.crit },
  { site:'CE-Branch-12', v:71, c:G.warn  },
  { site:'CE-Branch-3',  v:44, c:'#fade2a'},
  { site:'CE-Branch-8',  v:38, c:'#fade2a'},
  { site:'CE-Hub-2',     v:31, c:G.ok    },
  { site:'CE-Branch-15', v:18, c:G.ok    },
  { site:'CE-DC-1',      v:12, c:G.ok    },
  { site:'PE-7',         v:8,  c:G.ok    },
];

const CHIPS=['Network health summary','Highest risk sites','VRF traffic breakdown','Active fault list'];

function Donut() {
  const total=VRF.reduce((s,d)=>s+d.value,0);
  let cum=0;
  const r=52,ri=r-18,cx=65,cy=65;
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
    <div style={{ background:G.card,border:'1px solid '+G.bord,borderRadius:4,padding:'10px 12px' }}>
      <div style={{ fontSize:10,color:G.muted,fontWeight:700,letterSpacing:'0.07em',marginBottom:8 }}>VRF TRAFFIC DISTRIBUTION</div>
      <div style={{ display:'flex',gap:12,alignItems:'center' }}>
        <svg width="130" height="130" viewBox="0 0 130 130">
          {slices.map(s=><path key={s.label} d={s.path} fill={s.color} opacity="0.85"/>)}
          <text x="65" y="62" textAnchor="middle" fill={G.text} fontSize="10" fontWeight="600">Traffic</text>
          <text x="65" y="74" textAnchor="middle" fill={G.muted} fontSize="9">VRF split</text>
        </svg>
        <div style={{ flex:1 }}>
          {VRF.map(d=>(
            <div key={d.label} style={{ display:'flex',alignItems:'center',gap:7,marginBottom:5 }}>
              <div style={{ width:8,height:8,borderRadius:2,background:d.color,flexShrink:0 }}/>
              <span style={{ fontSize:11,color:G.text,flex:1,fontFamily:'ui-monospace,monospace' }}>{d.label}</span>
              <span style={{ fontSize:11,color:d.color,fontWeight:700,fontFamily:'ui-monospace,monospace' }}>{d.value}%</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function RiskBars() {
  return (
    <div style={{ background:G.card,border:'1px solid '+G.bord,borderRadius:4,padding:'10px 12px' }}>
      <div style={{ fontSize:10,color:G.muted,fontWeight:700,letterSpacing:'0.07em',marginBottom:8 }}>30-MIN RISK FORECAST</div>
      {RISK.map((d,i)=>(
        <div key={d.site} style={{ marginBottom:7 }}>
          <div style={{ display:'flex',justifyContent:'space-between',marginBottom:2 }}>
            <span style={{ fontSize:11,color:G.text,fontFamily:'ui-monospace,monospace' }}>
              <span style={{ color:G.dim }}>{i+1}. </span>{d.site}
            </span>
            <span style={{ fontSize:11,color:d.c,fontWeight:700,fontFamily:'ui-monospace,monospace' }}>{d.v}%</span>
          </div>
          <div style={{ height:4,background:G.elev,borderRadius:2,overflow:'hidden' }}>
            <div style={{ width:d.v+'%',height:'100%',background:d.c,borderRadius:2 }}/>
          </div>
        </div>
      ))}
    </div>
  );
}

export function AssistantPage() {
  return (
    <div style={{ display:'flex',flexDirection:'column',height:'100vh',background:G.bg }}>
      <div style={{ display:'flex',alignItems:'center',gap:10,padding:'8px 16px',
        borderBottom:'1px solid '+G.bord,background:G.card,flexShrink:0 }}>
        <div style={{ width:6,height:6,borderRadius:'50%',background:G.ok }}/>
        <div style={{ fontSize:13,fontWeight:600,color:G.text }}>NOC Copilot — Network Assistant</div>
        <div style={{ fontSize:10,color:G.muted,marginLeft:6 }}>Mistral-7B-Q4_K_M · ChromaDB RAG · Air-gapped</div>
        <div style={{ flex:1 }}/>
        {([['Predictive','predictive'],['Network Map','network-map'],['Alerts','notifications']] as [string,string][]).map(([l,p])=>(
          <a key={p} href={'/a/noc-copilot/'+p} style={{ color:G.muted,fontSize:12,
            textDecoration:'none',padding:'3px 9px',borderRadius:3,border:'1px solid '+G.bord }}>{l}</a>
        ))}
      </div>
      <div style={{ display:'flex',flex:1,overflow:'hidden' }}>
        <div style={{ flex:6,display:'flex',flexDirection:'column',overflow:'hidden' }}>
          <div style={{ flex:1,overflowY:'auto',padding:'16px 20px' }}>
            <CopilotPanel messages={ASSISTANT_CONVO}/>
          </div>
          <div style={{ padding:'8px 16px 12px',borderTop:'1px solid '+G.bord,background:G.card }}>
            <div style={{ display:'flex',gap:8,background:G.elev,border:'1px solid '+G.bord,
              borderRadius:4,padding:'8px 12px',alignItems:'center' }}>
              <input readOnly placeholder="Ask about your network..."
                style={{ flex:1,background:'transparent',border:'none',color:G.muted,fontSize:13,outline:'none' }}/>
              <button style={{ background:G.elev,border:'1px solid '+G.bord,borderRadius:3,
                padding:'5px 12px',color:G.info,fontSize:12,fontWeight:600,cursor:'pointer' }}>Send</button>
            </div>
            <div style={{ display:'flex',gap:6,marginTop:7,flexWrap:'wrap' }}>
              {CHIPS.map(q=>(
                <span key={q} style={{ fontSize:11,color:G.muted,background:G.elev,
                  border:'1px solid '+G.bord,borderRadius:20,padding:'2px 10px',cursor:'default' }}>{q}</span>
              ))}
            </div>
          </div>
        </div>
        <div style={{ width:290,flexShrink:0,borderLeft:'1px solid '+G.bord,overflowY:'auto',
          padding:'10px 10px',display:'flex',flexDirection:'column',gap:10 }}>
          <Donut/>
          <RiskBars/>
        </div>
      </div>
    </div>
  );
}
