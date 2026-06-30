import React, { useState } from 'react';
import { AlertBar } from '../components/AlertBar';
import { NetworkSVG } from '../components/NetworkSVG';
import { NODES, LINKS } from '../mock/topology';
import type { NetworkNode } from '../types';


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

function Detail({ node }: { node: NetworkNode | null }) {
  if (!node) return (
    <div style={{ display:'flex',flexDirection:'column',alignItems:'center',justifyContent:'center',
      height:'100%',color:G.muted,fontSize:12,gap:8,padding:20 }}>
      <div style={{ fontSize:11,color:G.dim }}>Click a node to inspect</div>
    </div>
  );
  const hc={critical:G.crit,warning:G.warn,ok:G.ok}[node.health];
  return (
    <div style={{ padding:'12px 12px',display:'flex',flexDirection:'column',gap:12 }}>
      <div style={{ display:'flex',justifyContent:'space-between',alignItems:'flex-start' }}>
        <div>
          <div style={{ fontSize:14,fontWeight:700,color:G.text,fontFamily:'ui-monospace,monospace' }}>{node.label}</div>
          <div style={{ fontSize:10,color:G.muted,marginTop:2 }}>{node.role}</div>
        </div>
        <span style={{ background:hc+'12',border:'1px solid '+hc+'30',borderRadius:3,
          padding:'2px 7px',fontSize:10,fontWeight:700,color:hc,letterSpacing:'0.07em' }}>
          {node.health.toUpperCase()}
        </span>
      </div>
      <div style={{ background:G.elev,border:'1px solid '+G.bord,borderRadius:4,overflow:'hidden' }}>
        {([['IP',node.ip],['Role',node.role],['VRFs',(node.vrfs||[]).join(', ')||'—']] as [string,string][]).map(([k,v],i)=>(
          <div key={k} style={{ display:'flex',justifyContent:'space-between',padding:'6px 10px',
            borderBottom:i<2?'1px solid '+G.bord:'none' }}>
            <span style={{ fontSize:11,color:G.muted }}>{k}</span>
            <span style={{ fontSize:11,color:G.text,fontFamily:'ui-monospace,monospace' }}>{v}</span>
          </div>
        ))}
      </div>
      {(node.warnings||[]).length>0&&(
        <div>
          <div style={{ fontSize:10,fontWeight:700,color:G.muted,letterSpacing:'0.08em',marginBottom:6 }}>ACTIVE WARNINGS</div>
          {(node.warnings||[]).map((w,i)=>{
            const m=w.match(/TTI (\d+)/);
            const col=m&&parseInt(m[1])<15?G.crit:G.warn;
            return (
              <div key={i} style={{ background:col+'08',border:'1px solid '+col+'20',
                borderLeft:'3px solid '+col,borderRadius:4,
                padding:'7px 9px',marginBottom:5,fontSize:11,color:G.text,lineHeight:1.5 }}>
                {w}
              </div>
            );
          })}
        </div>
      )}
      {node.health!=='ok'&&(
        <a href={'/a/noc-copilot/diagnostic?device='+encodeURIComponent(node.id)} style={{
          display:'block',textAlign:'center',background:G.elev,border:'1px solid '+G.bord,
          borderRadius:4,padding:'8px 0',color:G.info,fontSize:12,fontWeight:600,
          textDecoration:'none' }}>
          Diagnose with Copilot &rarr;
        </a>
      )}
    </div>
  );
}

export function NetworkMapPage() {
  const [sel, setSel] = useState<NetworkNode|null>(null);
  return (
    <div style={{ display:'flex',flexDirection:'column',height:'100vh',background:G.bg }}>
      <AlertBar/>
      <div style={{ display:'flex',flex:1,overflow:'hidden',padding:10,gap:8 }}>
        <div style={{ flex:3,background:G.bg,borderRadius:4,border:'1px solid '+G.bord,
          overflow:'hidden',display:'flex',flexDirection:'column' }}>
          <div style={{ padding:'7px 12px',borderBottom:'1px solid '+G.bord,
            display:'flex',gap:14,alignItems:'center',background:G.card,flexShrink:0 }}>
            <span style={{ fontSize:12,fontWeight:600,color:G.text }}>sdwan_mpls_noc</span>
            <span style={{ fontSize:11,color:G.ok,display:'flex',alignItems:'center',gap:5 }}>
              <span style={{ width:6,height:6,borderRadius:'50%',background:G.ok,display:'inline-block' }}/>
              24P · 12PE · 6 hub · 24 branch · 4 DC
            </span>
            <div style={{ flex:1 }}/>
            <span style={{ fontSize:10,color:G.dim,fontFamily:'ui-monospace,monospace' }}>08:42 UTC</span>
          </div>
          <div style={{ flex:1,overflow:'hidden' }}>
            <NetworkSVG nodes={NODES} links={LINKS} onNodeClick={setSel} selectedId={sel?.id}/>
          </div>
        </div>
        <div style={{ width:255,flexShrink:0,background:G.card,border:'1px solid '+G.bord,
          borderRadius:4,display:'flex',flexDirection:'column',overflow:'auto' }}>
          <div style={{ padding:'7px 12px',borderBottom:'1px solid '+G.bord,
            fontSize:10,fontWeight:700,color:G.muted,letterSpacing:'0.08em',background:G.elev }}>DEVICE DETAIL</div>
          <Detail node={sel}/>
        </div>
      </div>
    </div>
  );
}
