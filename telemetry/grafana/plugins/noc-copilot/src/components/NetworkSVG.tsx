import React, { useState } from 'react';
import type { NetworkNode, NetworkLink } from '../types';

const HC: Record<string,string> = { critical:'#f2495c', warning:'#ff9830', ok:'#73bf69' };
const SZ: Record<string,number> = { P:18, PE:14, 'CE-Hub':13, 'CE-Branch':11, 'CE-DC':12 };

interface Props {
  nodes: NetworkNode[];
  links: NetworkLink[];
  onNodeClick: (n: NetworkNode) => void;
  selectedId?: string;
}

function NodeShape({ node, cx, cy, size, color, selected, hovered }:
  { node: NetworkNode; cx:number; cy:number; size:number; color:string; selected:boolean; hovered:boolean }) {
  const s = size + (hovered ? 3 : 0);
  const sw = selected ? 3 : 1.5;
  const stroke = selected ? '#ffffff' : color;
  const fill = color+'25';
  const glow = node.health !== 'ok'
    ? <circle cx={cx} cy={cy} r={s+7} fill="none" stroke={color} strokeWidth="1" opacity="0.3"/>
    : null;
  if (node.role === 'P') {
    const pts = Array.from({length:6},(_,i)=>{ const a=(i*60-30)*Math.PI/180; return (cx+s*Math.cos(a))+','+(cy+s*Math.sin(a)); }).join(' ');
    return <g>{glow}<polygon points={pts} fill={fill} stroke={stroke} strokeWidth={sw}/></g>;
  }
  if (node.role === 'CE-Hub') {
    return <g>{glow}<path d={"M"+cx+","+(cy-s)+" L"+(cx+s)+","+cy+" L"+cx+","+(cy+s)+" L"+(cx-s)+","+cy+" Z"} fill={fill} stroke={stroke} strokeWidth={sw}/></g>;
  }
  if (node.role === 'CE-DC') {
    return <g>{glow}<rect x={cx-s} y={cy-s} width={s*2} height={s*2} rx="3" fill={fill} stroke={stroke} strokeWidth={sw}/></g>;
  }
  return <g>{glow}<circle cx={cx} cy={cy} r={s} fill={fill} stroke={stroke} strokeWidth={sw}/></g>;
}

export function NetworkSVG({ nodes, links, onNodeClick, selectedId }: Props) {
  const [hovered, setHovered] = useState<string|null>(null);
  const nm = Object.fromEntries(nodes.map(n=>[n.id,n]));
  return (
    <svg viewBox="0 0 1020 500" style={{ width:'100%', height:'100%', background:'#0d0e12', borderRadius:8 }}>
      <text x="8" y="65"  fill="#3a3a4a" fontSize="10">P CORE</text>
      <text x="8" y="185" fill="#3a3a4a" fontSize="10">PE LAYER</text>
      <text x="8" y="305" fill="#3a3a4a" fontSize="10">HUB / DC</text>
      <text x="8" y="425" fill="#3a3a4a" fontSize="10">BRANCHES</text>
      {links.map((l,i)=>{
        const s=nm[l.source], t=nm[l.target];
        if (!s||!t) return null;
        return <line key={i} x1={s.x} y1={s.y} x2={t.x} y2={t.y}
          stroke={HC[l.health]} strokeWidth={l.health==='ok'?1:2}
          opacity={l.health==='ok'?0.2:0.85} strokeDasharray={l.health==='critical'?'4,3':'none'}/>;
      })}
      {nodes.map(node=>{
        const color=HC[node.health];
        const size=SZ[node.role]||12;
        return (
          <g key={node.id} onClick={()=>onNodeClick(node)}
            onMouseEnter={()=>setHovered(node.id)} onMouseLeave={()=>setHovered(null)}
            style={{ cursor:'pointer' }}>
            <NodeShape node={node} cx={node.x} cy={node.y} size={size}
              color={color} selected={node.id===selectedId} hovered={hovered===node.id}/>
            <text x={node.x} y={node.y+size+13} textAnchor="middle"
              fill={node.health!=='ok'?color:'#5a5a6a'}
              fontSize={node.health!=='ok'?10:9} fontWeight={node.health!=='ok'?700:400}>
              {node.label}
            </text>
          </g>
        );
      })}
      <g transform="translate(830,20)">
        {[['Critical','#f2495c'],['Warning','#ff9830'],['OK','#73bf69']].map(([lbl,col],i)=>(
          <g key={lbl} transform={"translate(0,"+(i*18)+")"}>
            <line x1="0" y1="7" x2="14" y2="7" stroke={col} strokeWidth="2"/>
            <text x="18" y="11" fill="#8e8e8e" fontSize="10">{lbl}</text>
          </g>
        ))}
      </g>
    </svg>
  );
}
