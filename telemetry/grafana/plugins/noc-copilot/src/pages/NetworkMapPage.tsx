import React, { useState } from 'react';
import { AlertBar } from '../components/AlertBar';
import { NetworkSVG } from '../components/NetworkSVG';
import { NODES, LINKS } from '../mock/topology';
import type { NetworkNode } from '../types';

const C = { critical:'#f2495c', warning:'#ff9830', ok:'#73bf69', info:'#5794f2',
  bg:'#111217', cardBg:'#141618', border:'#2d3035', text:'#d9d9d9', muted:'#8e8e8e' };

function Detail({ node }: { node: NetworkNode | null }) {
  if (!node) return (
    <div style={{ display:'flex', flexDirection:'column', alignItems:'center', justifyContent:'center', height:'100%', color:C.muted, fontSize:13, gap:8, padding:20 }}>
      <div style={{ fontSize:32 }}>🖱️</div><div>Click a node to view details</div>
    </div>
  );
  const hc = { critical:C.critical, warning:C.warning, ok:C.ok }[node.health];
  return (
    <div style={{ padding:16, display:'flex', flexDirection:'column', gap:14 }}>
      <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center' }}>
        <div style={{ fontSize:17, fontWeight:700, color:C.text }}>{node.label}</div>
        <span style={{ background:hc+'20', border:'1px solid '+hc+'50', borderRadius:4, padding:'2px 8px', fontSize:10, fontWeight:700, color:hc }}>{node.health.toUpperCase()}</span>
      </div>
      <table style={{ borderCollapse:'collapse', fontSize:12, width:'100%' }}>
        <tbody>
          {([['Role',node.role],['IP',node.ip],['VRFs',(node.vrfs||[]).join(', ')||'—']] as [string,string][]).map(([k,v])=>(
            <tr key={k}><td style={{ color:C.muted, padding:'4px 0', paddingRight:12 }}>{k}</td><td style={{ color:C.text }}>{v}</td></tr>
          ))}
        </tbody>
      </table>
      {(node.warnings||[]).length > 0 && (
        <div>
          <div style={{ fontSize:11, fontWeight:700, color:C.muted, marginBottom:8, letterSpacing:'0.08em' }}>ACTIVE WARNINGS</div>
          {(node.warnings||[]).map((w,i) => {
            const m = w.match(/TTI (d+)/);
            const col = m && parseInt(m[1]) < 15 ? C.critical : C.warning;
            return <div key={i} style={{ background:col+'10', border:'1px solid '+col+'30', borderRadius:6,
              padding:'8px 10px', marginBottom:6, fontSize:12, color:C.text, borderLeft:'3px solid '+col }}>⚠ {w}</div>;
          })}
        </div>
      )}
      {node.health !== 'ok' && (
        <a href={"/a/noc-copilot/diagnostic?device="+encodeURIComponent(node.id)} style={{
          display:'block', textAlign:'center', background:C.info+'15', border:'1px solid '+C.info+'40',
          borderRadius:6, padding:'10px 0', color:C.info, fontSize:13, fontWeight:600, textDecoration:'none' }}>
          🤖 Diagnose with Copilot →
        </a>
      )}
    </div>
  );
}

export function NetworkMapPage() {
  const [sel, setSel] = useState<NetworkNode|null>(null);
  return (
    <div style={{ display:'flex', flexDirection:'column', height:'100vh', background:C.bg }}>
      <AlertBar/>
      <div style={{ display:'flex', flex:1, overflow:'hidden', padding:14, gap:12 }}>
        <div style={{ flex:3, background:'#0d0e12', borderRadius:8, border:'1px solid '+C.border, overflow:'hidden', display:'flex', flexDirection:'column' }}>
          <div style={{ padding:'10px 14px', borderBottom:'1px solid '+C.border, display:'flex', gap:14, alignItems:'center' }}>
            <span style={{ fontSize:13, fontWeight:700, color:C.text }}>Live Topology — sdwan_mpls_noc</span>
            <span style={{ fontSize:11, color:C.ok }}>● 52 devices · 130 containers</span>
            <div style={{ flex:1 }}/>
            <span style={{ fontSize:10, color:C.muted }}>Updated 08:42 UTC</span>
          </div>
          <div style={{ flex:1, padding:8 }}>
            <NetworkSVG nodes={NODES} links={LINKS} onNodeClick={setSel} selectedId={sel?.id}/>
          </div>
        </div>
        <div style={{ width:270, flexShrink:0, background:'#0d0f14', border:'1px solid '+C.border, borderRadius:8, overflow:'auto' }}>
          <div style={{ padding:'10px 14px', borderBottom:'1px solid '+C.border, fontSize:11, fontWeight:700, color:C.muted, letterSpacing:'0.08em' }}>DEVICE DETAIL</div>
          <Detail node={sel}/>
        </div>
      </div>
    </div>
  );
}
