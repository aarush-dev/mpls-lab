import React from 'react';
import type { ChatMessage } from '../types';

const C = { info:'#5794f2', warning:'#ff9830', text:'#d9d9d9', muted:'#8e8e8e', border:'#2d3035' };

function MiniChart() {
  const data = [12,14,13,16,18,17,21,24,23,27,31,29,33,38,42];
  const W=260, H=70, min=Math.min(...data), max=Math.max(...data);
  const pts = data.map((v,i) => ((i/(data.length-1))*W)+','+(H-((v-min)/(max-min+1))*(H-14)-7)).join(' ');
  const slaY = H - ((35-min)/(max-min+1))*(H-14) - 7;
  return (
    <div style={{ marginTop:10, background:'#0d0e12', borderRadius:6, padding:'8px 12px' }}>
      <div style={{ fontSize:10, color:C.muted, marginBottom:4 }}>CE-Hub-2 Tunnel Latency (ms)</div>
      <svg viewBox={"0 0 "+W+" "+H} style={{ width:'100%', height:H }}>
        <defs><linearGradient id="lg" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={C.info} stopOpacity="0.3"/>
          <stop offset="100%" stopColor={C.info} stopOpacity="0"/>
        </linearGradient></defs>
        <polygon points={"0,"+H+" "+pts+" "+W+","+H} fill="url(#lg)"/>
        <polyline points={pts} fill="none" stroke={C.info} strokeWidth="2" strokeLinecap="round"/>
        <line x1="0" y1={slaY} x2={W} y2={slaY} stroke={C.warning} strokeWidth="1" strokeDasharray="3,3"/>
        <text x="2" y={slaY-2} fill={C.warning} fontSize="8">SLA 35ms</text>
        <text x={W-28} y="12" fill={C.warning} fontSize="10" fontWeight="700">42ms</text>
      </svg>
    </div>
  );
}

function Line({ text }: { text: string }) {
  if (!text) return <div style={{ height:6 }}/>;
  if (text.startsWith('**') && !text.slice(2).includes('**') && text.endsWith('**'))
    return <div style={{ fontWeight:700, color:'#c5c5d2', fontSize:11, letterSpacing:'0.08em', marginTop:10, marginBottom:3 }}>{text.slice(2,-2)}</div>;
  if (text.startsWith('|')) {
    const cells = text.split('|').filter(Boolean).map(c=>c.trim());
    if (cells.every(c=>!c.replace(/-/g,'').trim())) return null;
    return <div style={{ display:'flex', gap:8, fontSize:12, padding:'3px 0', borderBottom:'1px solid #2d3035' }}>
      {cells.map((c,j)=><span key={j} style={{ flex:j===1?2:1, color:j===0?'#8e8e8e':'#d9d9d9' }}>{c}</span>)}
    </div>;
  }
  const parts = text.split(/(\*\*[^*]+\*\*)/g);
  return <div>{parts.map((p,j)=>p.startsWith('**')?<strong key={j} style={{ color:'#c5c5d2' }}>{p.slice(2,-2)}</strong>:<span key={j}>{p}</span>)}</div>;
}

export function CopilotPanel({ messages }: { messages: ChatMessage[] }) {
  return (
    <div style={{ display:'flex', flexDirection:'column', gap:14 }}>
      {messages.map((msg,i) => (
        <div key={i} style={{ display:'flex', gap:10, flexDirection:msg.role==='user'?'row-reverse':'row', alignItems:'flex-start' }}>
          <div style={{ width:28, height:28, borderRadius:'50%', flexShrink:0, marginTop:2,
            background:msg.role==='user'?'#1f3051':'#232842', display:'flex', alignItems:'center', justifyContent:'center',
            fontSize:13, border:'1px solid '+(msg.role==='user'?'#3b5a9a':'#2d3a5a') }}>
            {msg.role==='user'?'👤':'🤖'}
          </div>
          <div style={{ background:msg.role==='user'?'#1f3051':'#1a2535',
            border:'1px solid '+(msg.role==='user'?'#2d4878':'#252d42'),
            borderRadius:msg.role==='user'?'12px 4px 12px 12px':'4px 12px 12px 12px',
            padding:'10px 14px', maxWidth:'85%', fontSize:13, lineHeight:1.6, color:C.text }}>
            {msg.content.split('\n').map((l,j)=><Line key={j} text={l}/>)}
            {msg.showChart && <MiniChart/>}
          </div>
        </div>
      ))}
    </div>
  );
}
