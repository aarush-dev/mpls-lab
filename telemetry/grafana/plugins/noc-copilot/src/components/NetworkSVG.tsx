import React, { useState } from 'react';
import type { NetworkNode, NetworkLink } from '../types';


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


const HC: Record<string,string> = { critical:DS.critical, warning:DS.warning, ok:DS.ok };
const SZ: Record<string,number> = { P:17, PE:13, 'CE-Hub':13, 'CE-Branch':10, 'CE-DC':12 };

interface Props {
  nodes: NetworkNode[];
  links: NetworkLink[];
  onNodeClick: (n: NetworkNode) => void;
  selectedId?: string;
}

function NodeShape({ node, cx, cy, size, color, selected, hovered }:
  { node: NetworkNode; cx:number; cy:number; size:number; color:string; selected:boolean; hovered:boolean }) {
  const s = size + (hovered ? 3 : 0);
  const isBad = node.health !== 'ok';
  const fill = selected ? color+'40' : isBad ? color+'20' : 'rgba(248,250,252,0.04)';
  const stroke = selected ? '#F8FAFC' : color;
  const sw = selected ? 2.5 : isBad ? 2 : 1;
  const glowEl = isBad ? (
    <>
      <circle cx={cx} cy={cy} r={s+10} fill="none" stroke={color} strokeWidth="0.5" opacity="0.2"/>
      <circle cx={cx} cy={cy} r={s+5} fill="none" stroke={color} strokeWidth="1" opacity="0.35"/>
    </>
  ) : null;
  if (node.role==='P') {
    const pts=Array.from({length:6},(_,i)=>{const a=(i*60-30)*Math.PI/180;return (cx+s*Math.cos(a))+','+(cy+s*Math.sin(a));}).join(' ');
    return <g>{glowEl}<polygon points={pts} fill={fill} stroke={stroke} strokeWidth={sw}/></g>;
  }
  if (node.role==='CE-Hub') {
    return <g>{glowEl}<path d={"M"+cx+","+(cy-s)+" L"+(cx+s)+","+cy+" L"+cx+","+(cy+s)+" L"+(cx-s)+","+cy+" Z"} fill={fill} stroke={stroke} strokeWidth={sw}/></g>;
  }
  if (node.role==='CE-DC') {
    return <g>{glowEl}<rect x={cx-s} y={cy-s} width={s*2} height={s*2} rx="3" fill={fill} stroke={stroke} strokeWidth={sw}/></g>;
  }
  return <g>{glowEl}<circle cx={cx} cy={cy} r={s} fill={fill} stroke={stroke} strokeWidth={sw}/></g>;
}

export function NetworkSVG({ nodes, links, onNodeClick, selectedId }: Props) {
  const [hovered, setHovered] = useState<string|null>(null);
  const nm = Object.fromEntries(nodes.map(n=>[n.id,n]));
  const bands = [{y:40,lbl:'P CORE'},{y:160,lbl:'PE LAYER'},{y:275,lbl:'HUB / DC'},{y:390,lbl:'BRANCHES'}];
  return (
    <svg viewBox="0 0 1020 470" style={{ width:'100%', height:'100%', background:DS.bg, borderRadius:8, display:'block' }}>
      {/* Layer bands */}
      {[{y:10,h:100},{y:130,h:100},{y:248,h:100},{y:358,h:90}].map((b,i)=>(
        <rect key={i} x={0} y={b.y} width={1020} height={b.h} fill="rgba(248,250,252,0.01)" rx={0}/>
      ))}
      {bands.map(b=>(
        <text key={b.lbl} x="8" y={b.y+12} fill={DS.dim} fontSize="9" fontWeight="600" letterSpacing="0.1em" textTransform="uppercase">{b.lbl}</text>
      ))}
      {/* Links */}
      {links.map((l,i)=>{
        const s=nm[l.source],t=nm[l.target];
        if (!s||!t) return null;
        const c=HC[l.health];
        return <line key={i} x1={s.x} y1={s.y} x2={t.x} y2={t.y}
          stroke={c} strokeWidth={l.health==='ok'?1:1.5}
          opacity={l.health==='ok'?0.12:0.7}
          strokeDasharray={l.health==='critical'?'5,3':'none'}/>;
      })}
      {/* Nodes */}
      {nodes.map(node=>{
        const color=HC[node.health];
        const size=SZ[node.role]||11;
        const isBad=node.health!=='ok';
        return (
          <g key={node.id} onClick={()=>onNodeClick(node)}
            onMouseEnter={()=>setHovered(node.id)} onMouseLeave={()=>setHovered(null)}
            style={{ cursor:'pointer' }}>
            <NodeShape node={node} cx={node.x} cy={node.y} size={size}
              color={color} selected={node.id===selectedId} hovered={hovered===node.id}/>
            <text x={node.x} y={node.y+size+14} textAnchor="middle"
              fill={isBad?color:DS.dim}
              fontSize={isBad?9.5:8.5} fontWeight={isBad?700:400}
              letterSpacing={isBad?"0.01em":"0"}>
              {node.label}
            </text>
          </g>
        );
      })}
      {/* Legend */}
      <g transform="translate(830,14)">
        <rect x="-8" y="-4" width="108" height="62" rx="5" fill={DS.card} stroke={DS.border} strokeWidth="0.5"/>
        {([['Critical',DS.critical],['Warning',DS.warning],['OK',DS.ok]] as [string,string][]).map(([lbl,col],i)=>(
          <g key={lbl} transform={"translate(0,"+(i*18)+")"}>
            <circle cx="5" cy="8" r="4" fill={col+'20'} stroke={col} strokeWidth="1.5"/>
            <text x="14" y="12" fill={DS.muted} fontSize="10">{lbl}</text>
          </g>
        ))}
      </g>
    </svg>
  );
}
