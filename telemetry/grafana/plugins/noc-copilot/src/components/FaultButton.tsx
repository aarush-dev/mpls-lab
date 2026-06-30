import React from 'react';
import type { FaultScenario } from '../types';


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


interface Props { fault: FaultScenario; onInject: (id: string) => void; active: boolean; }

export function FaultButton({ fault, onInject, active }: Props) {
  const isCrit = fault.severity==='CRITICAL';
  const color = isCrit ? DS.critical : DS.warning;
  const bg = isCrit ? DS.criticalBg : DS.warningBg;
  const glow = isCrit ? DS.criticalGlow : 'rgba(245,158,11,0.2)';
  return (
    <div style={{
      background: active ? 'linear-gradient(135deg, '+color+'12 0%, '+DS.card+' 100%)' : DS.card,
      border:'1px solid '+(active ? color+'60' : DS.border),
      borderRadius:10, padding:'16px 14px',
      display:'flex', flexDirection:'column', gap:12,
      boxShadow: active ? '0 0 20px '+glow+', inset 0 1px 0 rgba(255,255,255,0.04)' : 'inset 0 1px 0 rgba(255,255,255,0.03)',
      transition:'all 0.2s ease',
    }}>
      <div style={{ display:'flex', justifyContent:'space-between', alignItems:'flex-start' }}>
        <div style={{ width:36, height:36, borderRadius:8, display:'flex', alignItems:'center',
          justifyContent:'center', fontSize:18, background:active?color+'18':DS.elevated,
          border:'1px solid '+(active?color+'30':DS.border) }}>
          {fault.icon}
        </div>
        <span style={{ background:bg, border:'1px solid '+color+'40', borderRadius:4,
          padding:'2px 8px', fontSize:10, fontWeight:700, color, letterSpacing:'0.08em',
          fontFamily:'ui-monospace,monospace' }}>
          {fault.severity}
        </span>
      </div>
      <div>
        <div style={{ fontWeight:600, fontSize:13, color:DS.text, marginBottom:4, lineHeight:1.3 }}>{fault.name}</div>
        <div style={{ fontSize:11, color:DS.muted, lineHeight:1.5 }}>{fault.description}</div>
        <div style={{ fontSize:11, color:DS.dim, marginTop:5, display:'flex', alignItems:'center', gap:4 }}>
          <span>Target</span>
          <span style={{ color:'#93C5FD', fontFamily:'ui-monospace,monospace', fontSize:10,
            background:'rgba(147,197,253,0.08)', border:'1px solid rgba(147,197,253,0.2)',
            borderRadius:3, padding:'0 5px' }}>{fault.target}</span>
        </div>
      </div>
      <button onClick={()=>onInject(fault.id)} style={{
        background: active ? color+'18' : DS.elevated,
        border:'1px solid '+(active ? color+'50' : DS.border),
        borderRadius:7, padding:'8px 0', cursor:'pointer',
        color: active ? color : DS.muted,
        fontSize:12, fontWeight:700, letterSpacing:'0.06em',
        display:'flex', alignItems:'center', justifyContent:'center', gap:7,
        transition:'all 0.15s',
      }}>
        {active
          ? <><span style={{ width:6, height:6, borderRadius:'50%', background:color,
              boxShadow:'0 0 6px '+color, display:'inline-block' }} className="noc-pulse"/>ACTIVE — CLICK TO REVERT</>
          : <>▶ INJECT FAULT</>
        }
      </button>
    </div>
  );
}
