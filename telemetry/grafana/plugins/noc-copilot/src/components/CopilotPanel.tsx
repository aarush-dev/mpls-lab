import React from 'react';
import type { ChatMessage } from '../types';


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

function MiniChart() {
  const data = [12,14,13,16,18,17,21,24,23,27,31,29,33,38,42];
  const W=260, H=72;
  const min=Math.min(...data), max=Math.max(...data);
  const tx=(i:number)=>(i/(data.length-1))*W;
  const ty=(v:number)=>H-((v-min)/(max-min+1))*(H-16)-6;
  const pts=data.map((v,i)=>tx(i)+','+ty(v)).join(' ');
  const slaY=ty(35);
  const grids=[0.3,0.6].map(p=>H-(p*(H-16))-6);
  return (
    <div style={{ marginTop:10, background:G.bg, borderRadius:4, padding:'8px 10px', border:'1px solid '+G.bord }}>
      <div style={{ fontSize:10, color:G.muted, marginBottom:5, fontFamily:'ui-monospace,monospace' }}>
        CE-Hub-2 · Tunnel Latency (ms) · 2h
      </div>
      <svg viewBox={'0 0 '+W+' '+H} style={{ width:'100%', height:H }}>
        <defs>
          <linearGradient id="clg" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={G.info} stopOpacity="0.2"/>
            <stop offset="100%" stopColor={G.info} stopOpacity="0"/>
          </linearGradient>
        </defs>
        {grids.map((y,i)=><line key={i} x1="0" y1={y} x2={W} y2={y} stroke={G.bord} strokeWidth="0.5"/>)}
        <line x1="0" y1={slaY} x2={W} y2={slaY} stroke={G.warn} strokeWidth="1" strokeDasharray="4,3" opacity="0.7"/>
        <text x={W-2} y={slaY-3} fill={G.warn} fontSize="8" textAnchor="end">SLA 35ms</text>
        <polygon points={'0,'+H+' '+pts+' '+W+','+H} fill="url(#clg)"/>
        <polyline points={pts} fill="none" stroke={G.info} strokeWidth="1.5" strokeLinecap="round"/>
        <circle cx={tx(data.length-1)} cy={ty(data[data.length-1])} r="3" fill={G.crit}/>
        <text x={tx(data.length-1)-4} y={ty(data[data.length-1])-6} fill={G.crit} fontSize="10"
          fontWeight="700" textAnchor="end">42ms</text>
      </svg>
    </div>
  );
}

function Line({ text }: { text: string }) {
  if (!text.trim()) return <div style={{ height:4 }}/>;

  // ALL-CAPS header lines: **WORD WORD**
  if (/^\*\*[A-Z][^a-z]+\*\*$/.test(text.trim())) {
    return (
      <div style={{ fontSize:10, fontWeight:700, color:G.muted, letterSpacing:'0.1em',
        marginTop:10, marginBottom:2 }}>
        {text.trim().slice(2,-2)}
      </div>
    );
  }

  // Table separator row
  if (text.startsWith('|') && /^[|\s-]+$/.test(text)) return null;

  // Table data row
  if (text.startsWith('|')) {
    const cells = text.split('|').filter(c => c.trim()).map(c => c.trim());
    return (
      <div style={{ display:'flex', gap:8, fontSize:11, padding:'3px 0',
        borderBottom:'1px solid '+G.bord }}>
        {cells.map((c, j) => {
          const parts = c.split(/(\*\*[^*]+\*\*)/g);
          return (
            <span key={j} style={{ flex:1, color:j===0?G.muted:G.text }}>
              {parts.map((p,k) => p.startsWith('**')
                ? <strong key={k} style={{ color:G.text, fontWeight:600 }}>{p.slice(2,-2)}</strong>
                : p)}
            </span>
          );
        })}
      </div>
    );
  }

  // Bullet
  if (text.startsWith('• ') || text.startsWith('* ')) {
    const body = text.slice(2);
    const parts = body.split(/(\*\*[^*]+\*\*)/g);
    return (
      <div style={{ display:'flex', gap:7, fontSize:13, lineHeight:1.6, color:G.text, marginBottom:1 }}>
        <span style={{ color:G.muted, flexShrink:0 }}>·</span>
        <span>{parts.map((p,j) => p.startsWith('**')
          ? <strong key={j} style={{ color:G.text, fontWeight:600 }}>{p.slice(2,-2)}</strong>
          : <span key={j}>{p}</span>)}</span>
      </div>
    );
  }

  // Numbered
  if (/^\d+\.\s/.test(text)) {
    const m = text.match(/^(\d+)\.\s(.+)$/);
    if (m) {
      const body = m[2];
      const parts = body.split(/(`[^`]+`)/g);
      return (
        <div style={{ display:'flex', gap:8, fontSize:13, lineHeight:1.6, color:G.text, marginBottom:2 }}>
          <span style={{ color:G.info, fontWeight:700, flexShrink:0,
            fontFamily:'ui-monospace,monospace', fontSize:11 }}>{m[1]}.</span>
          <span>{parts.map((p,j) => p.startsWith('`')
            ? <code key={j} style={{ background:G.elev, border:'1px solid '+G.bord,
                borderRadius:3, padding:'0 4px', fontSize:11,
                fontFamily:'ui-monospace,monospace' }}>{p.slice(1,-1)}</code>
            : <span key={j}>{p}</span>)}</span>
        </div>
      );
    }
  }

  // Regular with bold + code
  const parts = text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g);
  return (
    <div style={{ fontSize:13, lineHeight:1.7, color:G.text }}>
      {parts.map((p, j) => {
        if (p.startsWith('**')) return <strong key={j} style={{ color:G.text, fontWeight:600 }}>{p.slice(2,-2)}</strong>;
        if (p.startsWith('`')) return <code key={j} style={{ background:G.elev, border:'1px solid '+G.bord,
          borderRadius:3, padding:'0 4px', fontSize:11,
          fontFamily:'ui-monospace,monospace' }}>{p.slice(1,-1)}</code>;
        return <span key={j}>{p}</span>;
      })}
    </div>
  );
}

export function CopilotPanel({ messages }: { messages: ChatMessage[] }) {
  return (
    <div style={{ display:'flex', flexDirection:'column', gap:14 }}>
      {messages.map((msg, i) => {
        const isUser = msg.role === 'user';
        return (
          <div key={i} style={{ display:'flex', gap:8, flexDirection:isUser?'row-reverse':'row', alignItems:'flex-start' }}>
            {/* Label badge */}
            <div style={{ width:28, height:20, borderRadius:3, flexShrink:0, marginTop:2,
              display:'flex', alignItems:'center', justifyContent:'center',
              fontSize:9, fontWeight:700, letterSpacing:'0.06em',
              background: isUser ? G.elev : G.elev,
              color: isUser ? G.info : G.muted,
              border:'1px solid '+G.bord }}>
              {isUser ? 'YOU' : 'AI'}
            </div>
            {/* Bubble */}
            <div style={{
              background: isUser ? '#1a2233' : G.card,
              border:'1px solid '+(isUser ? '#2d4a6a' : G.bord),
              borderLeft: isUser ? undefined : '2px solid '+G.info,
              borderRadius:4,
              padding:'9px 12px', maxWidth:'87%',
            }}>
              {msg.content.split('\n').map((l,j) => <Line key={j} text={l}/>)}
              {msg.showChart && <MiniChart/>}
            </div>
          </div>
        );
      })}
    </div>
  );
}
