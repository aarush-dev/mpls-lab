import React, { useState } from 'react';
import { AlertBar } from '../components/AlertBar';
import { FaultButton } from '../components/FaultButton';
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


const SCENARIOS: FaultScenario[] = [
  { id:'bgp-flap',   name:'BGP Route Flap',          description:'Induces iBGP session oscillation via hold-timer manipulation on selected PE.',   severity:'CRITICAL', target:'PE-3',          icon:'🔁' },
  { id:'mpls-fail',  name:'MPLS Underlay Failure',   description:'Kills MPLS forwarding on P-core link, forcing LSP reroute cascade.',             severity:'CRITICAL', target:'P-6 ↔ P-7',     icon:'⚡' },
  { id:'congestion', name:'Interface Congestion',     description:'Applies tc-netem bandwidth cap to simulate RX saturation on branch uplink.',      severity:'WARNING',  target:'CE-Branch-7',   icon:'📈' },
  { id:'wg-rekey',   name:'WireGuard Rekey Anomaly', description:'Delays WireGuard session rekeying to simulate timeout-risk condition.',            severity:'WARNING',  target:'CE-Branch-12',  icon:'🔑' },
  { id:'ospf-storm', name:'OSPF SPF Storm',           description:'Triggers rapid topology changes to force continuous SPF recalculation.',           severity:'WARNING',  target:'PE-8',          icon:'🌪️' },
  { id:'drift',      name:'Controller Drift',         description:'Injects stale policy config into SD-WAN controller to simulate drift condition.',  severity:'WARNING',  target:'noc-controller', icon:'⚠️' },
];

const SEED = [
  { scenario:'BGP Route Flap',        target:'PE-3',        at:'08:41:03', duration:'00:01:23', severity:'CRITICAL', status:'ACTIVE'   },
  { scenario:'Interface Congestion',  target:'CE-Branch-7', at:'08:38:15', duration:'00:04:07', severity:'CRITICAL', status:'ACTIVE'   },
  { scenario:'MPLS Underlay Failure', target:'P-6 ↔ P-7',  at:'08:30:00', duration:'00:12:07', severity:'CRITICAL', status:'REVERTED' },
];
type Row = typeof SEED[number];

export function FaultInjectorPage() {
  const [active, setActive] = useState(new Set(['bgp-flap','congestion']));
  const [rows, setRows] = useState<Row[]>(SEED);

  const toggle=(id:string)=>{
    const s=SCENARIOS.find(sc=>sc.id===id)!;
    const now=new Date();
    const ts=[now.getHours(),now.getMinutes(),now.getSeconds()].map(n=>String(n).padStart(2,'0')).join(':');
    setActive(prev=>{
      const next=new Set(prev);
      if(next.has(id)){next.delete(id);setRows(r=>r.map(row=>row.scenario===s.name?{...row,status:'REVERTED'}:row));}
      else{next.add(id);setRows(r=>[...r,{scenario:s.name,target:s.target,at:ts,duration:'00:00:01',severity:s.severity,status:'ACTIVE'}]);}
      return next;
    });
  };

  const activeCount=active.size;
  return (
    <div style={{ display:'flex', flexDirection:'column', height:'100vh', background:DS.bg }}>
      <AlertBar/>
      <div style={{ flex:1, overflowY:'auto', padding:'16px 18px' }}>
        {/* Header */}
        <div style={{ display:'flex', alignItems:'center', justifyContent:'space-between', marginBottom:16 }}>
          <div>
            <h2 style={{ color:DS.text, margin:0, fontSize:16, fontWeight:700 }}>Fault Injector</h2>
            <p style={{ color:DS.muted, margin:'3px 0 0', fontSize:12 }}>
              Inject reversible network faults for validation. Toggle to activate or revert.
            </p>
          </div>
          {activeCount>0&&(
            <div style={{ display:'flex', alignItems:'center', gap:6, background:DS.criticalBg,
              border:'1px solid rgba(239,68,68,0.3)', borderRadius:7, padding:'6px 12px' }}>
              <span className="noc-pulse" style={{ width:7,height:7,borderRadius:'50%',background:DS.critical,display:'inline-block',boxShadow:'0 0 6px '+DS.critical }}/>
              <span style={{ fontSize:12, fontWeight:700, color:DS.critical }}>{activeCount} ACTIVE</span>
            </div>
          )}
        </div>
        {/* Scenario grid */}
        <div style={{ display:'grid', gridTemplateColumns:'repeat(3,1fr)', gap:10, marginBottom:18 }}>
          {SCENARIOS.map(s=><FaultButton key={s.id} fault={s} active={active.has(s.id)} onInject={toggle}/>)}
        </div>
        {/* History table */}
        <div style={{ background:DS.card, border:'1px solid '+DS.border, borderRadius:8, overflow:'hidden' }}>
          <div style={{ padding:'9px 14px', borderBottom:'1px solid '+DS.border,
            fontSize:10, fontWeight:700, color:DS.muted, letterSpacing:'0.1em' }}>INJECTION HISTORY</div>
          <table style={{ width:'100%', borderCollapse:'collapse', fontSize:12 }}>
            <thead>
              <tr style={{ borderBottom:'1px solid '+DS.border }}>
                {['Scenario','Target','Injected At','Duration','Severity','Status'].map(h=>(
                  <th key={h} style={{ padding:'7px 14px', color:DS.dim, fontWeight:600,
                    textAlign:'left', fontSize:10, letterSpacing:'0.06em' }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((r,i)=>{
                const sc=r.severity==='CRITICAL'?DS.critical:DS.warning;
                const isActive=r.status==='ACTIVE';
                return (
                  <tr key={i} style={{ borderBottom:'1px solid '+DS.borderSubtle,
                    background:i%2===0?'rgba(248,250,252,0.01)':'transparent' }}>
                    <td style={{ padding:'8px 14px', color:DS.text, fontWeight:isActive?600:400 }}>{r.scenario}</td>
                    <td style={{ padding:'8px 14px', fontFamily:'ui-monospace,monospace', color:'#93C5FD', fontSize:11 }}>{r.target}</td>
                    <td style={{ padding:'8px 14px', color:DS.muted, fontFamily:'ui-monospace,monospace', fontSize:11 }}>{r.at}</td>
                    <td style={{ padding:'8px 14px', color:DS.muted, fontFamily:'ui-monospace,monospace', fontSize:11 }}>{r.duration}</td>
                    <td style={{ padding:'8px 14px' }}>
                      <span style={{ color:sc, background:sc+'12', border:'1px solid '+sc+'30',
                        borderRadius:4, padding:'2px 7px', fontSize:10, fontWeight:700, letterSpacing:'0.06em' }}>{r.severity}</span>
                    </td>
                    <td style={{ padding:'8px 14px' }}>
                      <span style={{ color:isActive?DS.ok:DS.dim,
                        background:isActive?DS.okBg:'rgba(71,85,105,0.1)',
                        border:'1px solid '+(isActive?'rgba(34,197,94,0.25)':DS.border),
                        borderRadius:4, padding:'2px 8px', fontSize:10, fontWeight:700,
                        display:'inline-flex', alignItems:'center', gap:4, letterSpacing:'0.06em' }}>
                        {isActive&&<span className="noc-pulse" style={{ width:5,height:5,borderRadius:'50%',background:DS.ok,display:'inline-block'}}/>}
                        {r.status}
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
