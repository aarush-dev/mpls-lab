import type { NetworkNode, NetworkLink } from '../types';

export const NODES: NetworkNode[] = [
  // P Core (row y=60)
  { id:'P-1', role:'P', label:'P-1', ip:'172.16.0.1',  health:'ok',       x:80,  y:60,  vrfs:[], warnings:[] },
  { id:'P-2', role:'P', label:'P-2', ip:'172.16.0.2',  health:'ok',       x:200, y:60,  vrfs:[], warnings:[] },
  { id:'P-3', role:'P', label:'P-3', ip:'172.16.0.3',  health:'ok',       x:320, y:60,  vrfs:[], warnings:[] },
  { id:'P-4', role:'P', label:'P-4', ip:'172.16.0.4',  health:'ok',       x:440, y:60,  vrfs:[], warnings:[] },
  { id:'P-5', role:'P', label:'P-5', ip:'172.16.0.5',  health:'ok',       x:560, y:60,  vrfs:[], warnings:[] },
  { id:'P-6', role:'P', label:'P-6', ip:'172.16.0.6',  health:'warning',  x:680, y:60,  vrfs:[], warnings:['MPLS LSP Forwarding Anomaly — TTI 18 min'] },
  { id:'P-7', role:'P', label:'P-7', ip:'172.16.0.7',  health:'ok',       x:800, y:60,  vrfs:[], warnings:[] },
  { id:'P-8', role:'P', label:'P-8', ip:'172.16.0.8',  health:'ok',       x:920, y:60,  vrfs:[], warnings:[] },
  // PE Layer (row y=180)
  { id:'PE-1', role:'PE', label:'PE-1', ip:'10.0.0.1', health:'ok',       x:60,  y:180, vrfs:['BRANCH-VPN','MGMT'], warnings:[] },
  { id:'PE-2', role:'PE', label:'PE-2', ip:'10.0.0.2', health:'ok',       x:160, y:180, vrfs:['BRANCH-VPN','VOIP'], warnings:[] },
  { id:'PE-3', role:'PE', label:'PE-3', ip:'10.0.0.3', health:'critical', x:260, y:180, vrfs:['BRANCH-VPN','MGMT'], warnings:['BGP Route Flap Cascade — TTI 8 min','Hold timer 23s/90s consumed'] },
  { id:'PE-4', role:'PE', label:'PE-4', ip:'10.0.0.4', health:'ok',       x:360, y:180, vrfs:['BRANCH-VPN','SCADA'], warnings:[] },
  { id:'PE-5', role:'PE', label:'PE-5', ip:'10.0.0.5', health:'warning',  x:460, y:180, vrfs:['BRANCH-VPN','MGMT'], warnings:['BGP VRF Prefix Count Drift — TTI 45 min'] },
  { id:'PE-6', role:'PE', label:'PE-6', ip:'10.0.0.6', health:'ok',       x:560, y:180, vrfs:['DC-VPN','MGMT'],    warnings:[] },
  { id:'PE-7', role:'PE', label:'PE-7', ip:'10.0.0.7', health:'ok',       x:660, y:180, vrfs:['BRANCH-VPN'],       warnings:[] },
  { id:'PE-8', role:'PE', label:'PE-8', ip:'10.0.0.8', health:'warning',  x:760, y:180, vrfs:['BRANCH-VPN','VOIP'],warnings:['OSPF SPF Frequency Spike — TTI 23 min'] },
  { id:'PE-9', role:'PE', label:'PE-9', ip:'10.0.0.9', health:'ok',       x:860, y:180, vrfs:['DC-VPN'],           warnings:[] },
  { id:'PE-10',role:'PE', label:'PE-10',ip:'10.0.0.10',health:'ok',       x:960, y:180, vrfs:['BRANCH-VPN'],       warnings:[] },
  // Hub CEs (row y=300)
  { id:'CE-Hub-1',role:'CE-Hub',label:'Hub-1',ip:'10.1.0.1',health:'ok',      x:150, y:300, vrfs:['BRANCH-VPN','MGMT'],warnings:[] },
  { id:'CE-Hub-2',role:'CE-Hub',label:'Hub-2',ip:'10.1.0.2',health:'warning', x:350, y:300, vrfs:['BRANCH-VPN','VOIP'],warnings:['Tunnel Jitter Accumulation — TTI 38 min'] },
  { id:'CE-Hub-3',role:'CE-Hub',label:'Hub-3',ip:'10.1.0.3',health:'ok',      x:550, y:300, vrfs:['BRANCH-VPN','SCADA'],warnings:[] },
  { id:'CE-Hub-4',role:'CE-Hub',label:'Hub-4',ip:'10.1.0.4',health:'ok',      x:750, y:300, vrfs:['DC-VPN'],         warnings:[] },
  // Branch CEs (row y=420 — subset shown)
  { id:'CE-Branch-1', role:'CE-Branch',label:'Br-1', ip:'10.2.0.1', health:'ok',       x:50,  y:420, vrfs:['BRANCH-VPN'],warnings:[] },
  { id:'CE-Branch-2', role:'CE-Branch',label:'Br-2', ip:'10.2.0.2', health:'ok',       x:130, y:420, vrfs:['BRANCH-VPN'],warnings:[] },
  { id:'CE-Branch-3', role:'CE-Branch',label:'Br-3', ip:'10.2.0.3', health:'ok',       x:210, y:420, vrfs:['BRANCH-VPN'],warnings:[] },
  { id:'CE-Branch-7', role:'CE-Branch',label:'Br-7', ip:'10.2.0.7', health:'critical', x:290, y:420, vrfs:['BRANCH-VPN'],warnings:['Interface Congestion — TTI 11 min','RX queue 97%'] },
  { id:'CE-Branch-8', role:'CE-Branch',label:'Br-8', ip:'10.2.0.8', health:'ok',       x:370, y:420, vrfs:['BRANCH-VPN'],warnings:[] },
  { id:'CE-Branch-12',role:'CE-Branch',label:'Br-12',ip:'10.2.0.12',health:'warning',  x:450, y:420, vrfs:['BRANCH-VPN'],warnings:['WireGuard Rekey Timeout Risk — TTI 31 min'] },
  { id:'CE-Branch-15',role:'CE-Branch',label:'Br-15',ip:'10.2.0.15',health:'ok',       x:530, y:420, vrfs:['BRANCH-VPN'],warnings:[] },
  { id:'CE-Branch-18',role:'CE-Branch',label:'Br-18',ip:'10.2.0.18',health:'ok',       x:610, y:420, vrfs:['BRANCH-VPN'],warnings:[] },
  // DC CEs (row y=300 right side)
  { id:'CE-DC-1',role:'CE-DC',label:'DC-1',ip:'10.3.0.1',health:'ok',x:860,y:300,vrfs:['DC-VPN','MGMT'],warnings:[] },
  { id:'CE-DC-2',role:'CE-DC',label:'DC-2',ip:'10.3.0.2',health:'ok',x:960,y:300,vrfs:['DC-VPN'],       warnings:[] },
];

export const LINKS: NetworkLink[] = [
  // P mesh (core)
  {source:'P-1',target:'P-2',health:'ok'},{source:'P-2',target:'P-3',health:'ok'},
  {source:'P-3',target:'P-4',health:'ok'},{source:'P-4',target:'P-5',health:'ok'},
  {source:'P-5',target:'P-6',health:'warning'},{source:'P-6',target:'P-7',health:'warning'},
  {source:'P-7',target:'P-8',health:'ok'},{source:'P-1',target:'P-5',health:'ok'},
  {source:'P-3',target:'P-7',health:'ok'},
  // P -> PE
  {source:'P-1',target:'PE-1',health:'ok'},{source:'P-2',target:'PE-2',health:'ok'},
  {source:'P-2',target:'PE-3',health:'critical'},{source:'P-3',target:'PE-3',health:'critical'},
  {source:'P-3',target:'PE-4',health:'ok'},{source:'P-4',target:'PE-5',health:'warning'},
  {source:'P-5',target:'PE-6',health:'ok'},{source:'P-6',target:'PE-7',health:'warning'},
  {source:'P-6',target:'PE-8',health:'warning'},{source:'P-7',target:'PE-9',health:'ok'},
  {source:'P-8',target:'PE-10',health:'ok'},
  // PE -> Hub
  {source:'PE-1',target:'CE-Hub-1',health:'ok'},{source:'PE-3',target:'CE-Hub-1',health:'critical'},
  {source:'PE-4',target:'CE-Hub-2',health:'ok'},{source:'PE-5',target:'CE-Hub-2',health:'warning'},
  {source:'PE-6',target:'CE-Hub-3',health:'ok'},{source:'PE-8',target:'CE-Hub-4',health:'warning'},
  // PE/Hub -> Branch
  {source:'CE-Hub-1',target:'CE-Branch-1',health:'ok'},{source:'CE-Hub-1',target:'CE-Branch-2',health:'ok'},
  {source:'CE-Hub-1',target:'CE-Branch-3',health:'ok'},{source:'CE-Hub-2',target:'CE-Branch-7',health:'critical'},
  {source:'CE-Hub-2',target:'CE-Branch-8',health:'ok'},{source:'CE-Hub-2',target:'CE-Branch-12',health:'warning'},
  {source:'CE-Hub-3',target:'CE-Branch-15',health:'ok'},{source:'CE-Hub-3',target:'CE-Branch-18',health:'ok'},
  // PE -> DC
  {source:'PE-9',target:'CE-DC-1',health:'ok'},{source:'PE-10',target:'CE-DC-2',health:'ok'},
  {source:'PE-6',target:'CE-DC-1',health:'ok'},
];
