import React, { useState } from 'react';
import type { NetworkNode, NetworkLink } from '../types';

const HC: Record<string, string> = { critical:'#f2495c', warning:'#ff9830', ok:'#73bf69' };
const SZ: Record<string, number> = { P:7, PE:8, 'CE-Hub':9, 'CE-Branch':7, 'CE-DC':8 };

// POP boundary boxes [x, y, w, h, label]
const POP_BOXES: [number, number, number, number, string][] = [
  [8,   10, 152, 100, 'POP-1 · Area 1'],
  [173, 10, 152, 100, 'POP-2 · Area 2'],
  [338, 10, 152, 100, 'POP-3 · Area 3'],
  [503, 10, 152, 100, 'POP-4 · Area 4'],
  [668, 10, 152, 100, 'POP-5 · Area 5'],
  [833, 10, 152, 100, 'POP-6 · Area 6'],
];

interface Props {
  nodes: NetworkNode[];
  links: NetworkLink[];
  onNodeClick: (n: NetworkNode) => void;
  selectedId?: string;
}

function NodeShape({ node, cx, cy, size, color, selected, hovered }:
  { node: NetworkNode; cx:number; cy:number; size:number; color:string; selected:boolean; hovered:boolean }) {
  const s = hovered ? size + 2 : size;
  const fill = selected ? color + '35' : node.health !== 'ok' ? color + '18' : '#1e2128';
  const stroke = selected ? '#d9d9d9' : color;
  const sw = selected ? 2 : node.health !== 'ok' ? 1.5 : 1;

  if (node.role === 'CE-Hub') {
    return <path d={`M${cx},${cy-s} L${cx+s},${cy} L${cx},${cy+s} L${cx-s},${cy} Z`}
      fill={fill} stroke={stroke} strokeWidth={sw}/>;
  }
  if (node.role === 'CE-DC') {
    return <rect x={cx-s} y={cy-s} width={s*2} height={s*2} rx="2"
      fill={fill} stroke={stroke} strokeWidth={sw}/>;
  }
  return <circle cx={cx} cy={cy} r={s} fill={fill} stroke={stroke} strokeWidth={sw}/>;
}

export function NetworkSVG({ nodes, links, onNodeClick, selectedId }: Props) {
  const [hovered, setHovered] = useState<string | null>(null);
  const nm = Object.fromEntries(nodes.map(n => [n.id, n]));

  return (
    <svg viewBox="0 0 1000 450" style={{ width:'100%', height:'100%', background:'#111217', display:'block', borderRadius:6 }}>

      {/* Layer labels */}
      {[
        { y:60,  lbl:'P CORE' },
        { y:190, lbl:'PE' },
        { y:295, lbl:'HUB / DC' },
        { y:390, lbl:'BRANCH' },
      ].map(({ y, lbl }) => (
        <text key={lbl} x="3" y={y} fill="#3a3a4a" fontSize="8" fontWeight="600"
          letterSpacing="0.08em" writingMode="tb" textAnchor="middle">{lbl}</text>
      ))}

      {/* POP boundary boxes */}
      {POP_BOXES.map(([x, y, w, h, lbl], i) => (
        <g key={i}>
          <rect x={x} y={y} width={w} height={h} rx="3"
            fill="rgba(255,255,255,0.01)" stroke="#2c2e33"
            strokeWidth="1" strokeDasharray="5,3"/>
          <text x={x + w / 2} y={y - 2} textAnchor="middle"
            fill="#5a5a6a" fontSize="7.5" letterSpacing="0.06em">{lbl}</text>
        </g>
      ))}

      {/* Links */}
      {links.map((l, i) => {
        const s = nm[l.source], t = nm[l.target];
        if (!s || !t) return null;
        const c = HC[l.health];
        const isOk = l.health === 'ok';
        return (
          <line key={i}
            x1={s.x} y1={s.y} x2={t.x} y2={t.y}
            stroke={c}
            strokeWidth={isOk ? 0.8 : 1.4}
            opacity={isOk ? 0.18 : 0.75}
            strokeDasharray={l.health === 'critical' ? '4,3' : undefined}/>
        );
      })}

      {/* Nodes */}
      {nodes.map(node => {
        const color = HC[node.health];
        const size = SZ[node.role] ?? 7;
        const isBad = node.health !== 'ok';
        return (
          <g key={node.id}
            onClick={() => onNodeClick(node)}
            onMouseEnter={() => setHovered(node.id)}
            onMouseLeave={() => setHovered(null)}
            style={{ cursor:'pointer' }}>
            <NodeShape node={node} cx={node.x} cy={node.y} size={size}
              color={color} selected={node.id === selectedId} hovered={hovered === node.id}/>
            <text x={node.x} y={node.y + size + 10} textAnchor="middle"
              fill={isBad ? color : '#5a5a6a'}
              fontSize={node.role === 'P' ? 7 : 8}
              fontWeight={isBad ? 600 : 400}>
              {node.label}
            </text>
          </g>
        );
      })}

      {/* Branch count note */}
      <text x="664" y="448" fill="#5a5a6a" fontSize="8" textAnchor="end">
        showing 10 of 24 branches
      </text>

      {/* Health legend */}
      <g transform="translate(820, 415)">
        <rect x="-4" y="-4" width="178" height="30" rx="3"
          fill="#181b1f" stroke="#2c2e33" strokeWidth="0.5"/>
        {([['Critical','#f2495c'],['Warning','#ff9830'],['OK','#73bf69']] as [string,string][]).map(([lbl, col], i) => (
          <g key={lbl} transform={`translate(${i * 58}, 0)`}>
            <circle cx="5" cy="8" r="4" fill={col + '20'} stroke={col} strokeWidth="1.5"/>
            <text x="13" y="12" fill="#8e8e8e" fontSize="9">{lbl}</text>
          </g>
        ))}
      </g>

      {/* Shape legend */}
      <g transform="translate(14, 415)">
        <rect x="-4" y="-4" width="185" height="30" rx="3"
          fill="#181b1f" stroke="#2c2e33" strokeWidth="0.5"/>
        {([
          ['circle', 'P / PE / Branch', 0],
          ['diamond', 'Hub CE', 80],
          ['square', 'DC CE', 148],
        ] as [string, string, number][]).map(([shape, lbl, ox]) => (
          <g key={shape} transform={`translate(${ox}, 0)`}>
            {shape === 'circle'  && <circle cx="5" cy="8" r="4" fill="#1e2128" stroke="#5a5a6a" strokeWidth="1"/>}
            {shape === 'diamond' && <path d="M5,3 L9,8 L5,13 L1,8 Z" fill="#1e2128" stroke="#5a5a6a" strokeWidth="1"/>}
            {shape === 'square'  && <rect x="1" y="3" width="8" height="8" rx="1" fill="#1e2128" stroke="#5a5a6a" strokeWidth="1"/>}
            <text x="14" y="12" fill="#8e8e8e" fontSize="8">{lbl}</text>
          </g>
        ))}
      </g>
    </svg>
  );
}
