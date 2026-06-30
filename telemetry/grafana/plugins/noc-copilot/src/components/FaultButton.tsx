import React from 'react';
import type { FaultScenario } from '../types';

const C = { critical:'#f2495c', warning:'#ff9830', ok:'#73bf69', border:'#2d3035', text:'#d9d9d9', muted:'#8e8e8e' };

interface Props { fault: FaultScenario; onInject: (id: string) => void; active: boolean; }

export function FaultButton({ fault, onInject, active }: Props) {
  const color = fault.severity === 'CRITICAL' ? C.critical : C.warning;
  return (
    <div style={{ background:'#141618', border:'1px solid '+(active ? color : C.border), borderRadius:8, padding:16,
      display:'flex', flexDirection:'column', gap:10, boxShadow: active ? '0 0 16px '+color+'20' : 'none' }}>
      <div style={{ display:'flex', justifyContent:'space-between', alignItems:'flex-start' }}>
        <div style={{ fontSize:22 }}>{fault.icon}</div>
        <span style={{ background:color+'20', border:'1px solid '+color+'40', borderRadius:4, padding:'2px 8px',
          fontSize:10, fontWeight:700, color, letterSpacing:'0.08em' }}>{fault.severity}</span>
      </div>
      <div>
        <div style={{ fontWeight:600, fontSize:14, color:C.text, marginBottom:4 }}>{fault.name}</div>
        <div style={{ fontSize:12, color:C.muted, lineHeight:1.4 }}>{fault.description}</div>
        <div style={{ fontSize:11, color:C.muted, marginTop:4 }}>Target: <span style={{ color:'#8ab4f8' }}>{fault.target}</span></div>
      </div>
      <button onClick={() => onInject(fault.id)} style={{
        background: active ? color+'20' : '#1a1c21', border:'1px solid '+(active ? color : '#3d4050'),
        borderRadius:6, padding:'8px 0', cursor:'pointer', color: active ? color : '#9aa0b0',
        fontSize:13, fontWeight:600, letterSpacing:'0.05em', display:'flex', alignItems:'center', justifyContent:'center', gap:6,
      }}>{active ? '⚡ ACTIVE' : '▶ INJECT'}</button>
    </div>
  );
}
