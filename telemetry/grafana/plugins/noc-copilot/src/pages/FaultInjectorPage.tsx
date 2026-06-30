import React, { useState } from 'react';
import { AlertBar } from '../components/AlertBar';
import { FaultButton } from '../components/FaultButton';
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

const SCENARIOS: FaultScenario[] = [
  { id:'bgp-flap',   name:'BGP Route Flap',          description:'Induces iBGP session oscillation via hold-timer manipulation on selected PE.',   severity:'CRITICAL', target:'PE-3',          icon:'' },
  { id:'mpls-fail',  name:'MPLS Underlay Failure',   description:'Kills MPLS forwarding on P-core link, forcing LSP reroute cascade.',             severity:'CRITICAL', target:'P-6 to P-7',    icon:'' },
  { id:'congestion', name:'Interface Congestion',     description:'Applies tc-netem bandwidth cap to simulate RX saturation on branch uplink.',      severity:'WARNING',  target:'CE-Branch-7',   icon:'' },
  { id:'wg-rekey',   name:'WireGuard Rekey Anomaly', description:'Delays WireGuard session rekeying to simulate timeout-risk condition.',            severity:'WARNING',  target:'CE-Branch-12',  icon:'' },
  { id:'ospf-storm', name:'OSPF SPF Storm',           description:'Triggers rapid topology changes to force continuous SPF recalculation.',           severity:'WARNING',  target:'PE-8',          icon:'' },
  { id:'drift',      name:'Controller Drift',         description:'Injects stale policy config into SD-WAN controller to simulate drift condition.',  severity:'WARNING',  target:'noc-controller', icon:'' },
];

const SEED = [
  { scenario:'BGP Route Flap',        target:'PE-3',        at:'08:41:03', duration:'00:01:23', severity:'CRITICAL', status:'ACTIVE'   },
  { scenario:'Interface Congestion',  target:'CE-Branch-7', at:'08:38:15', duration:'00:04:07', severity:'CRITICAL', status:'ACTIVE'   },
  { scenario:'MPLS Underlay Failure', target:'P-6 to P-7',  at:'08:30:00', duration:'00:12:07', severity:'CRITICAL', status:'REVERTED' },
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

  return (
    <div style={{ display:'flex',flexDirection:'column',height:'100vh',background:G.bg }}>
      <AlertBar/>
      <div style={{ flex:1,overflowY:'auto',padding:'14px 16px' }}>
        <div style={{ marginBottom:14 }}>
          <div style={{ fontSize:14,fontWeight:600,color:G.text,marginBottom:3 }}>Fault Injector</div>
          <div style={{ fontSize:12,color:G.muted }}>Toggle to inject or revert. All faults are reversible. Visual only.</div>
        </div>
        <div style={{ display:'grid',gridTemplateColumns:'repeat(3,1fr)',gap:10,marginBottom:16 }}>
          {SCENARIOS.map(s=><FaultButton key={s.id} fault={s} active={active.has(s.id)} onInject={toggle}/>)}
        </div>
        <div style={{ background:G.card,border:'1px solid '+G.bord,borderRadius:4,overflow:'hidden' }}>
          <div style={{ padding:'8px 14px',borderBottom:'1px solid '+G.bord,
            fontSize:10,fontWeight:700,color:G.muted,letterSpacing:'0.08em' }}>INJECTION HISTORY</div>
          <table style={{ width:'100%',borderCollapse:'collapse',fontSize:12 }}>
            <thead><tr style={{ borderBottom:'1px solid '+G.bord }}>
              {['Scenario','Target','Injected At','Duration','Severity','Status'].map(h=>(
                <th key={h} style={{ padding:'7px 12px',color:G.dim,fontWeight:600,textAlign:'left',fontSize:10 }}>{h}</th>
              ))}
            </tr></thead>
            <tbody>
              {rows.map((r,i)=>{
                const sc=r.severity==='CRITICAL'?G.crit:G.warn;
                const isActive=r.status==='ACTIVE';
                return (
                  <tr key={i} style={{ borderBottom:'1px solid '+G.bord+'20' }}>
                    <td style={{ padding:'8px 12px',color:G.text }}>{r.scenario}</td>
                    <td style={{ padding:'8px 12px',color:G.info,fontFamily:'ui-monospace,monospace',fontSize:11 }}>{r.target}</td>
                    <td style={{ padding:'8px 12px',color:G.muted,fontFamily:'ui-monospace,monospace',fontSize:11 }}>{r.at}</td>
                    <td style={{ padding:'8px 12px',color:G.muted,fontFamily:'ui-monospace,monospace',fontSize:11 }}>{r.duration}</td>
                    <td style={{ padding:'8px 12px' }}>
                      <span style={{ color:sc,background:sc+'12',border:'1px solid '+sc+'25',
                        borderRadius:3,padding:'2px 6px',fontSize:10,fontWeight:700 }}>{r.severity}</span>
                    </td>
                    <td style={{ padding:'8px 12px' }}>
                      <span style={{ color:isActive?G.ok:G.dim,background:isActive?G.ok+'12':G.elev,
                        border:'1px solid '+(isActive?G.ok+'25':G.bord),
                        borderRadius:3,padding:'2px 7px',fontSize:10,fontWeight:700 }}>{r.status}</span>
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
