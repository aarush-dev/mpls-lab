import React, { useState } from 'react';
import { AlertBar } from '../components/AlertBar';
import { NetworkSVG } from '../components/NetworkSVG';
import { NODES, LINKS } from '../mock/topology';
import type { NetworkNode } from '../types';


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


function Detail({ node }: { node: NetworkNode | null }) {
  if (!node) return (
    <div style={{ display:'flex', flexDirection:'column', alignItems:'center', justifyContent:'center',
      height:'100%', gap:10, padding:20, color:DS.muted }}>
      <div style={{ fontSize:28, opacity:0.4 }}>⊕</div>
      <div style={{ fontSize:12 }}>Click a node to inspect</div>
    </div>
  );
  const hc={critical:DS.critical,warning:DS.warning,ok:DS.ok}[node.health];
  return (
    <div style={{ padding:'14px 14px', display:'flex', flexDirection:'column', gap:14 }}>
      <div style={{ display:'flex', justifyContent:'space-between', alignItems:'flex-start' }}>
        <div>
          <div style={{ fontSize:16, fontWeight:700, color:DS.text, fontFamily:'ui-monospace,monospace' }}>{node.label}</div>
          <div style={{ fontSize:10, color:DS.muted, marginTop:2, letterSpacing:'0.06em' }}>{node.role}</div>
        </div>
        <span style={{ background:hc+'15', border:'1px solid '+hc+'40', borderRadius:5,
          padding:'3px 9px', fontSize:10, fontWeight:700, color:hc, letterSpacing:'0.08em',
          display:'flex', alignItems:'center', gap:5 }}>
          {node.health!=='ok'&&<span className="noc-pulse" style={{ width:5,height:5,borderRadius:'50%',background:hc,display:'inline-block'}}/>}
          {node.health.toUpperCase()}
        </span>
      </div>
      {/* Stats */}
      <div style={{ background:DS.elevated, border:'1px solid '+DS.border, borderRadius:7, overflow:'hidden' }}>
        {([['IP Address',node.ip],['Role',node.role],['VRFs',(node.vrfs||[]).join(', ')||'—']] as [string,string][]).map(([k,v],i)=>(
          <div key={k} style={{ display:'flex', justifyContent:'space-between', padding:'7px 12px',
            borderBottom:i<2?'1px solid '+DS.borderSubtle:'none' }}>
            <span style={{ fontSize:11, color:DS.muted }}>{k}</span>
            <span style={{ fontSize:11, color:DS.text, fontFamily:'ui-monospace,monospace' }}>{v}</span>
          </div>
        ))}
      </div>
      {/* Warnings */}
      {(node.warnings||[]).length > 0 && (
        <div>
          <div style={{ fontSize:10, fontWeight:700, color:DS.muted, letterSpacing:'0.1em', marginBottom:7 }}>ACTIVE WARNINGS</div>
          {(node.warnings||[]).map((w,i)=>{
            const m=w.match(/TTI (\d+)/);
            const col=m&&parseInt(m[1])<15?DS.critical:DS.warning;
            return (
              <div key={i} style={{ background:col+'08', border:'1px solid '+col+'25',
                borderLeft:'3px solid '+col, borderRadius:5,
                padding:'8px 10px', marginBottom:6, fontSize:11, color:DS.text, lineHeight:1.5 }}>
                {w}
              </div>
            );
          })}
        </div>
      )}
      {/* Diagnose link */}
      {node.health!=='ok' && (
        <a href={"/a/noc-copilot/diagnostic?device="+encodeURIComponent(node.id)} style={{
          display:'flex', alignItems:'center', justifyContent:'center', gap:8,
          background:'rgba(129,140,248,0.08)', border:'1px solid rgba(129,140,248,0.25)',
          borderRadius:7, padding:'10px 0', color:DS.ai, fontSize:12, fontWeight:600,
          textDecoration:'none', letterSpacing:'0.03em' }}>
          ✦ Diagnose with Copilot →
        </a>
      )}
    </div>
  );
}

export function NetworkMapPage() {
  const [sel, setSel] = useState<NetworkNode|null>(null);
  return (
    <div style={{ display:'flex', flexDirection:'column', height:'100vh', background:DS.bg }}>
      <AlertBar/>
      <div style={{ display:'flex', flex:1, overflow:'hidden', padding:12, gap:10 }}>
        {/* Map panel */}
        <div style={{ flex:3, background:DS.bg, borderRadius:8, border:'1px solid '+DS.border,
          overflow:'hidden', display:'flex', flexDirection:'column' }}>
          <div style={{ padding:'9px 14px', borderBottom:'1px solid '+DS.border,
            display:'flex', gap:16, alignItems:'center', background:DS.card }}>
            <span style={{ fontSize:12, fontWeight:700, color:DS.text, letterSpacing:'0.02em' }}>
              Live Topology — sdwan_mpls_noc
            </span>
            <span style={{ display:'flex', alignItems:'center', gap:5, fontSize:11, color:DS.ok }}>
              <span className="noc-pulse" style={{ width:5,height:5,borderRadius:'50%',background:DS.ok,display:'inline-block',boxShadow:'0 0 4px '+DS.ok }}/>
              52 devices · 130 containers
            </span>
            <div style={{ flex:1 }}/>
            <span style={{ fontSize:10, color:DS.dim, fontFamily:'ui-monospace,monospace' }}>Last sync 08:42 UTC</span>
          </div>
          <div style={{ flex:1, padding:8, overflow:'hidden' }}>
            <NetworkSVG nodes={NODES} links={LINKS} onNodeClick={setSel} selectedId={sel?.id}/>
          </div>
        </div>
        {/* Detail panel */}
        <div style={{ width:264, flexShrink:0, background:DS.card,
          border:'1px solid '+DS.border, borderRadius:8, display:'flex', flexDirection:'column', overflow:'auto' }}>
          <div style={{ padding:'9px 14px', borderBottom:'1px solid '+DS.border,
            fontSize:10, fontWeight:700, color:DS.muted, letterSpacing:'0.1em',
            background:'rgba(248,250,252,0.02)' }}>DEVICE DETAIL</div>
          <Detail node={sel}/>
        </div>
      </div>
    </div>
  );
}
