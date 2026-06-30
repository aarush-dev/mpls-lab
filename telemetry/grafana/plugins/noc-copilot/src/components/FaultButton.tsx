import React from 'react';
import type { FaultScenario } from '../types';


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

interface Props { fault: FaultScenario; onInject: (id: string) => void; active: boolean; }

const ABBR: Record<string, string> = {
  'bgp-flap':   'BGP',
  'mpls-fail':  'MPLS',
  'congestion': 'ETH',
  'wg-rekey':   'WG',
  'ospf-storm': 'OSPF',
  'drift':      'CTRL',
};

export function FaultButton({ fault, onInject, active }: Props) {
  const isCrit = fault.severity === 'CRITICAL';
  const color = isCrit ? G.crit : G.warn;
  return (
    <div style={{
      background: G.card,
      border:'1px solid '+(active ? color+'60' : G.bord),
      borderRadius:6, padding:'14px 12px',
      display:'flex', flexDirection:'column', gap:10,
      boxShadow: active ? '0 0 12px '+color+'20' : 'none',
    }}>
      <div style={{ display:'flex', justifyContent:'space-between', alignItems:'flex-start' }}>
        <div style={{ width:34, height:22, borderRadius:3, display:'flex', alignItems:'center',
          justifyContent:'center', fontSize:10, fontWeight:700, letterSpacing:'0.06em',
          background:G.elev, border:'1px solid '+G.bord, color:G.muted }}>
          {ABBR[fault.id] || 'NET'}
        </div>
        <span style={{ background:color+'15', border:'1px solid '+color+'35', borderRadius:3,
          padding:'2px 7px', fontSize:9, fontWeight:700, color, letterSpacing:'0.08em',
          fontFamily:'ui-monospace,monospace' }}>
          {fault.severity}
        </span>
      </div>
      <div>
        <div style={{ fontWeight:600, fontSize:13, color:G.text, marginBottom:4 }}>{fault.name}</div>
        <div style={{ fontSize:11, color:G.muted, lineHeight:1.5 }}>{fault.description}</div>
        <div style={{ fontSize:11, color:G.dim, marginTop:5 }}>
          Target: <span style={{ color:G.info, fontFamily:'ui-monospace,monospace', fontSize:10 }}>{fault.target}</span>
        </div>
      </div>
      <button onClick={() => onInject(fault.id)} style={{
        background: active ? color+'15' : G.elev,
        border:'1px solid '+(active ? color+'50' : G.bord),
        borderRadius:4, padding:'7px 0', cursor:'pointer',
        color: active ? color : G.muted,
        fontSize:12, fontWeight:600, letterSpacing:'0.05em',
        display:'flex', alignItems:'center', justifyContent:'center', gap:6,
      }}>
        {active ? <><span style={{ width:6, height:6, borderRadius:'50%', background:color, display:'inline-block' }}/>ACTIVE — CLICK TO REVERT</> : '▶  INJECT FAULT'}
      </button>
    </div>
  );
}
