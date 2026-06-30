import React from 'react';
import type { ChatMessage } from '../types';


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


function MiniChart() {
  const data = [12,14,13,16,18,17,21,24,23,27,31,29,33,38,42];
  const W=260,H=75;
  const min=Math.min(...data),max=Math.max(...data);
  const tx=(i:number)=>(i/(data.length-1))*W;
  const ty=(v:number)=>H-((v-min)/(max-min+1))*(H-18)-8;
  const pts=data.map((v,i)=>tx(i)+','+ty(v)).join(' ');
  const slaY=ty(35);
  const gridYs=[0.25,0.5,0.75].map(p=>H-(p*(H-18))-8);
  return (
    <div style={{ marginTop:10, background:'rgba(0,0,0,0.3)', borderRadius:6, padding:'8px 10px', border:'1px solid '+DS.border }}>
      <div style={{ fontSize:10, color:DS.muted, marginBottom:5, fontFamily:'ui-monospace,monospace' }}>CE-Hub-2 · Tunnel Latency (ms) · 2h window</div>
      <svg viewBox={"0 0 "+W+" "+H} style={{ width:'100%', height:H }}>
        <defs>
          <linearGradient id="mcg" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={DS.info} stopOpacity="0.25"/>
            <stop offset="100%" stopColor={DS.info} stopOpacity="0"/>
          </linearGradient>
        </defs>
        {gridYs.map((y,i)=><line key={i} x1="0" y1={y} x2={W} y2={y} stroke={DS.border} strokeWidth="0.5" opacity="0.6"/>)}
        <line x1="0" y1={slaY} x2={W} y2={slaY} stroke={DS.warning} strokeWidth="1" strokeDasharray="4,3" opacity="0.8"/>
        <text x={W-2} y={slaY-3} fill={DS.warning} fontSize="8" textAnchor="end">SLA 35ms</text>
        <polygon points={"0,"+H+" "+pts+" "+W+","+H} fill="url(#mcg)"/>
        <polyline points={pts} fill="none" stroke={DS.info} strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
        <circle cx={tx(data.length-1)} cy={ty(data[data.length-1])} r="3" fill={DS.info}/>
        <text x={tx(data.length-1)-4} y={ty(data[data.length-1])-6} fill={DS.critical} fontSize="10" fontWeight="700" textAnchor="end">42ms ▲</text>
      </svg>
    </div>
  );
}

function Line({ text }: { text: string }) {
  if (!text.trim()) return <div style={{ height:5 }}/>;
  // Section headers: **WORD WORD**
  if (/^\*\*[A-Z][^a-z]+\*\*$/.test(text.trim())) {
    const label = text.trim().slice(2,-2);
    return (
      <div style={{ fontSize:10, fontWeight:700, color:DS.muted, letterSpacing:'0.1em',
        marginTop:10, marginBottom:3, textTransform:'uppercase' as const }}>
        {label}
      </div>
    );
  }
  // Table separator rows (|---|---|)
  if (text.startsWith('|') && /^[|\s-]+$/.test(text)) return null;
  // Table data rows
  if (text.startsWith('|')) {
    const cells = text.split('|').filter(c=>c.trim()).map(c=>c.trim());
    return (
      <div style={{ display:'grid', gridTemplateColumns:'repeat('+cells.length+',1fr)',
        gap:6, fontSize:11, padding:'4px 0', borderBottom:'1px solid '+DS.borderSubtle }}>
        {cells.map((c,j)=>{
          const bold = c.match(/^\*\*(.+)\*\*$/);
          return (
            <span key={j} style={{ color:j===0?DS.muted:DS.text, fontWeight:j===0?400:400 }}>
              {bold ? <strong style={{ color:'#C7D2FE' }}>{bold[1]}</strong> : c}
            </span>
          );
        })}
      </div>
    );
  }
  // Bullet points
  if (text.startsWith('• ') || text.startsWith('* ')) {
    const content = text.slice(2);
    const parts = content.split(/(\*\*[^*]+\*\*)/g);
    return (
      <div style={{ display:'flex', gap:7, fontSize:13, lineHeight:1.6, color:DS.text, marginBottom:1 }}>
        <span style={{ color:DS.muted, flexShrink:0, marginTop:2 }}>·</span>
        <span>{parts.map((p,j)=>p.startsWith('**')
          ? <strong key={j} style={{ color:'#C7D2FE', fontWeight:600 }}>{p.slice(2,-2)}</strong>
          : <span key={j}>{p}</span>)}</span>
      </div>
    );
  }
  // Numbered list
  if (/^\d+\.\s/.test(text)) {
    const [num, ...rest] = text.split(/\.\s(.+)/);
    const content = rest.join('. ');
    const parts = content.split(/(`[^`]+`)/g);
    return (
      <div style={{ display:'flex', gap:8, fontSize:13, lineHeight:1.6, color:DS.text, marginBottom:2 }}>
        <span style={{ color:DS.ai, fontWeight:700, flexShrink:0, fontFamily:'ui-monospace,monospace', fontSize:11 }}>{num}.</span>
        <span>{parts.map((p,j)=>p.startsWith('`')
          ? <code key={j} style={{ background:'rgba(129,140,248,0.12)', border:'1px solid rgba(129,140,248,0.25)',
              borderRadius:3, padding:'0 4px', fontSize:11, color:'#C7D2FE', fontFamily:'ui-monospace,monospace' }}>{p.slice(1,-1)}</code>
          : <span key={j}>{p}</span>)}</span>
      </div>
    );
  }
  // Regular line with inline bold + code
  const parts = text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g);
  return (
    <div style={{ fontSize:13, lineHeight:1.7, color:DS.text }}>
      {parts.map((p,j)=>{
        if (p.startsWith('**')) return <strong key={j} style={{ color:'#C7D2FE', fontWeight:600 }}>{p.slice(2,-2)}</strong>;
        if (p.startsWith('`')) return <code key={j} style={{ background:'rgba(129,140,248,0.12)', border:'1px solid rgba(129,140,248,0.25)',
          borderRadius:3, padding:'0 4px', fontSize:11, color:'#C7D2FE', fontFamily:'ui-monospace,monospace' }}>{p.slice(1,-1)}</code>;
        return <span key={j}>{p}</span>;
      })}
    </div>
  );
}

export function CopilotPanel({ messages }: { messages: ChatMessage[] }) {
  return (
    <div style={{ display:'flex', flexDirection:'column', gap:16 }}>
      {messages.map((msg,i)=>{
        const isUser = msg.role==='user';
        return (
          <div key={i} style={{ display:'flex', gap:10, flexDirection:isUser?'row-reverse':'row', alignItems:'flex-start' }}>
            {/* Avatar */}
            <div style={{ width:26, height:26, borderRadius:'50%', flexShrink:0, marginTop:1,
              display:'flex', alignItems:'center', justifyContent:'center', fontSize:10, fontWeight:700,
              background: isUser ? '#1D4ED8' : '#312E81',
              color: '#E0E7FF',
              border: '1px solid '+(isUser ? 'rgba(59,130,246,0.4)' : 'rgba(129,140,248,0.4)'),
            }}>
              {isUser?'ME':'AI'}
            </div>
            {/* Bubble */}
            <div style={{
              background: isUser
                ? 'linear-gradient(135deg, #1E3A5F 0%, #1a3158 100%)'
                : 'linear-gradient(135deg, #0A1628 0%, #0D1B35 100%)',
              border: '1px solid '+(isUser ? 'rgba(59,130,246,0.25)' : DS.borderSubtle),
              borderLeft: isUser ? undefined : '2px solid '+DS.ai,
              borderRadius: isUser ? '12px 3px 12px 12px' : '3px 12px 12px 12px',
              padding:'10px 14px', maxWidth:'86%',
            }}>
              {msg.content.split('\n').map((l,j)=><Line key={j} text={l}/>)}
              {msg.showChart && <MiniChart/>}
            </div>
          </div>
        );
      })}
    </div>
  );
}
