import type { NetworkNode, NetworkLink } from '../types';

// 6 POPs x 4 P routers (24 total). Layout: POP boxes at y=10..120.
// Each POP: ABR1(col1,y=45), ABR2(col2,y=45), PE-f1(col1,y=90), PE-f2(col2,y=90)
// POP base-x: POP1=8, POP2=173, POP3=338, POP4=503, POP5=668, POP6=833
// Col offsets within POP: col1=+35, col2=+115
// Canvas: 1020 x 475

const P_BASES = [8, 173, 338, 503, 668, 833];

function px(pop: number, col: 0 | 1): number { return P_BASES[pop] + (col === 0 ? 37 : 117); }

export const NODES: NetworkNode[] = [
  // ── P Core: POP1 (Area 1) ──────────────────────────────────────────────────
  { id:'P-1',  role:'P', label:'P-1',  ip:'172.16.0.1',  health:'ok',       x:px(0,0), y:48, vrfs:[], warnings:[] },
  { id:'P-2',  role:'P', label:'P-2',  ip:'172.16.0.2',  health:'ok',       x:px(0,1), y:48, vrfs:[], warnings:[] },
  { id:'P-3',  role:'P', label:'P-3',  ip:'172.16.0.3',  health:'ok',       x:px(0,0), y:92, vrfs:[], warnings:[] },
  { id:'P-4',  role:'P', label:'P-4',  ip:'172.16.0.4',  health:'ok',       x:px(0,1), y:92, vrfs:[], warnings:[] },
  // ── P Core: POP2 (Area 2) ──────────────────────────────────────────────────
  { id:'P-5',  role:'P', label:'P-5',  ip:'172.16.0.5',  health:'ok',       x:px(1,0), y:48, vrfs:[], warnings:[] },
  { id:'P-6',  role:'P', label:'P-6',  ip:'172.16.0.6',  health:'warning',  x:px(1,1), y:48, vrfs:[], warnings:['MPLS LSP Forwarding Anomaly — TTI 18 min'] },
  { id:'P-7',  role:'P', label:'P-7',  ip:'172.16.0.7',  health:'ok',       x:px(1,0), y:92, vrfs:[], warnings:[] },
  { id:'P-8',  role:'P', label:'P-8',  ip:'172.16.0.8',  health:'ok',       x:px(1,1), y:92, vrfs:[], warnings:[] },
  // ── P Core: POP3 (Area 3) ──────────────────────────────────────────────────
  { id:'P-9',  role:'P', label:'P-9',  ip:'172.16.0.9',  health:'ok',       x:px(2,0), y:48, vrfs:[], warnings:[] },
  { id:'P-10', role:'P', label:'P-10', ip:'172.16.0.10', health:'ok',       x:px(2,1), y:48, vrfs:[], warnings:[] },
  { id:'P-11', role:'P', label:'P-11', ip:'172.16.0.11', health:'ok',       x:px(2,0), y:92, vrfs:[], warnings:[] },
  { id:'P-12', role:'P', label:'P-12', ip:'172.16.0.12', health:'ok',       x:px(2,1), y:92, vrfs:[], warnings:[] },
  // ── P Core: POP4 (Area 4) ──────────────────────────────────────────────────
  { id:'P-13', role:'P', label:'P-13', ip:'172.16.0.13', health:'ok',       x:px(3,0), y:48, vrfs:[], warnings:[] },
  { id:'P-14', role:'P', label:'P-14', ip:'172.16.0.14', health:'ok',       x:px(3,1), y:48, vrfs:[], warnings:[] },
  { id:'P-15', role:'P', label:'P-15', ip:'172.16.0.15', health:'ok',       x:px(3,0), y:92, vrfs:[], warnings:[] },
  { id:'P-16', role:'P', label:'P-16', ip:'172.16.0.16', health:'ok',       x:px(3,1), y:92, vrfs:[], warnings:[] },
  // ── P Core: POP5 (Area 5) ──────────────────────────────────────────────────
  { id:'P-17', role:'P', label:'P-17', ip:'172.16.0.17', health:'ok',       x:px(4,0), y:48, vrfs:[], warnings:[] },
  { id:'P-18', role:'P', label:'P-18', ip:'172.16.0.18', health:'ok',       x:px(4,1), y:48, vrfs:[], warnings:[] },
  { id:'P-19', role:'P', label:'P-19', ip:'172.16.0.19', health:'ok',       x:px(4,0), y:92, vrfs:[], warnings:[] },
  { id:'P-20', role:'P', label:'P-20', ip:'172.16.0.20', health:'ok',       x:px(4,1), y:92, vrfs:[], warnings:[] },
  // ── P Core: POP6 (Area 6) ──────────────────────────────────────────────────
  { id:'P-21', role:'P', label:'P-21', ip:'172.16.0.21', health:'ok',       x:px(5,0), y:48, vrfs:[], warnings:[] },
  { id:'P-22', role:'P', label:'P-22', ip:'172.16.0.22', health:'ok',       x:px(5,1), y:48, vrfs:[], warnings:[] },
  { id:'P-23', role:'P', label:'P-23', ip:'172.16.0.23', health:'ok',       x:px(5,0), y:92, vrfs:[], warnings:[] },
  { id:'P-24', role:'P', label:'P-24', ip:'172.16.0.24', health:'ok',       x:px(5,1), y:92, vrfs:[], warnings:[] },

  // ── PE Layer (12 PEs, y=190, x=40+i*88) ───────────────────────────────────
  { id:'PE-1',  role:'PE', label:'PE-1',  ip:'10.0.0.1',  health:'ok',       x:40,  y:190, vrfs:['CORP','VOICE'],     warnings:[] },
  { id:'PE-2',  role:'PE', label:'PE-2',  ip:'10.0.0.2',  health:'ok',       x:128, y:190, vrfs:['CORP','VOICE'],     warnings:[] },
  { id:'PE-3',  role:'PE', label:'PE-3',  ip:'10.0.0.3',  health:'critical', x:216, y:190, vrfs:['CORP','VOICE'],     warnings:['BGP Route Flap Cascade — TTI 8 min','Hold timer 23s/90s consumed'] },
  { id:'PE-4',  role:'PE', label:'PE-4',  ip:'10.0.0.4',  health:'ok',       x:304, y:190, vrfs:['CORP','GUEST'],     warnings:[] },
  { id:'PE-5',  role:'PE', label:'PE-5',  ip:'10.0.0.5',  health:'warning',  x:392, y:190, vrfs:['CORP','VOICE'],     warnings:['BGP VRF Prefix Count Drift — TTI 45 min'] },
  { id:'PE-6',  role:'PE', label:'PE-6',  ip:'10.0.0.6',  health:'ok',       x:480, y:190, vrfs:['CORP','GUEST'],     warnings:[] },
  { id:'PE-7',  role:'PE', label:'PE-7',  ip:'10.0.0.7',  health:'ok',       x:568, y:190, vrfs:['CORP','VOICE'],     warnings:[] },
  { id:'PE-8',  role:'PE', label:'PE-8',  ip:'10.0.0.8',  health:'warning',  x:656, y:190, vrfs:['CORP','VOICE'],     warnings:['OSPF SPF Frequency Spike — TTI 23 min'] },
  { id:'PE-9',  role:'PE', label:'PE-9',  ip:'10.0.0.9',  health:'ok',       x:744, y:190, vrfs:['CORP','VOICE'],     warnings:[] },
  { id:'PE-10', role:'PE', label:'PE-10', ip:'10.0.0.10', health:'ok',       x:832, y:190, vrfs:['CORP','VOICE'],     warnings:[] },
  { id:'PE-11', role:'PE', label:'PE-11', ip:'10.0.0.11', health:'ok',       x:920, y:190, vrfs:['CORP'],             warnings:[] },
  { id:'PE-12', role:'PE', label:'PE-12', ip:'10.0.0.12', health:'ok',       x:985, y:190, vrfs:['CORP'],             warnings:[] },

  // ── Hub CEs (6, y=295, x=70+i*165) ────────────────────────────────────────
  { id:'CE-Hub-1', role:'CE-Hub', label:'Hub-1', ip:'10.1.0.1', health:'ok',      x:70,  y:295, vrfs:['CORP','VOICE','GUEST'], warnings:[] },
  { id:'CE-Hub-2', role:'CE-Hub', label:'Hub-2', ip:'10.1.0.2', health:'warning', x:235, y:295, vrfs:['CORP','VOICE','GUEST'], warnings:['Tunnel Jitter Accumulation — TTI 38 min'] },
  { id:'CE-Hub-3', role:'CE-Hub', label:'Hub-3', ip:'10.1.0.3', health:'ok',      x:400, y:295, vrfs:['CORP','VOICE','GUEST'], warnings:[] },
  { id:'CE-Hub-4', role:'CE-Hub', label:'Hub-4', ip:'10.1.0.4', health:'ok',      x:565, y:295, vrfs:['CORP','VOICE','GUEST'], warnings:[] },
  { id:'CE-Hub-5', role:'CE-Hub', label:'Hub-5', ip:'10.1.0.5', health:'ok',      x:730, y:295, vrfs:['CORP','VOICE'],         warnings:[] },
  { id:'CE-Hub-6', role:'CE-Hub', label:'Hub-6', ip:'10.1.0.6', health:'ok',      x:895, y:295, vrfs:['CORP','VOICE'],         warnings:[] },

  // ── DC CEs (4, y=380 right side) ───────────────────────────────────────────
  { id:'CE-DC-1', role:'CE-DC', label:'DC-1', ip:'10.3.0.1', health:'ok', x:790, y:385, vrfs:['CORP','VOICE','GUEST'], warnings:[] },
  { id:'CE-DC-2', role:'CE-DC', label:'DC-2', ip:'10.3.0.2', health:'ok', x:855, y:385, vrfs:['CORP','VOICE','GUEST'], warnings:[] },
  { id:'CE-DC-3', role:'CE-DC', label:'DC-3', ip:'10.3.0.3', health:'ok', x:920, y:385, vrfs:['CORP'],                 warnings:[] },
  { id:'CE-DC-4', role:'CE-DC', label:'DC-4', ip:'10.3.0.4', health:'ok', x:985, y:385, vrfs:['CORP'],                 warnings:[] },

  // ── Branch CEs (10 representative of 24, y=385) ────────────────────────────
  { id:'CE-Branch-1',  role:'CE-Branch', label:'Br-1',  ip:'10.2.0.1',  health:'ok',       x:30,  y:385, vrfs:['CORP','VOICE'], warnings:[] },
  { id:'CE-Branch-2',  role:'CE-Branch', label:'Br-2',  ip:'10.2.0.2',  health:'ok',       x:100, y:385, vrfs:['CORP','VOICE'], warnings:[] },
  { id:'CE-Branch-3',  role:'CE-Branch', label:'Br-3',  ip:'10.2.0.3',  health:'ok',       x:170, y:385, vrfs:['CORP','VOICE'], warnings:[] },
  { id:'CE-Branch-7',  role:'CE-Branch', label:'Br-7',  ip:'10.2.0.7',  health:'critical', x:240, y:385, vrfs:['CORP','VOICE'], warnings:['Interface Congestion — TTI 11 min','RX queue 97%'] },
  { id:'CE-Branch-8',  role:'CE-Branch', label:'Br-8',  ip:'10.2.0.8',  health:'ok',       x:310, y:385, vrfs:['CORP','VOICE'], warnings:[] },
  { id:'CE-Branch-12', role:'CE-Branch', label:'Br-12', ip:'10.2.0.12', health:'warning',  x:380, y:385, vrfs:['CORP','VOICE'], warnings:['WireGuard Rekey Timeout Risk — TTI 31 min'] },
  { id:'CE-Branch-15', role:'CE-Branch', label:'Br-15', ip:'10.2.0.15', health:'ok',       x:450, y:385, vrfs:['CORP','VOICE'], warnings:[] },
  { id:'CE-Branch-16', role:'CE-Branch', label:'Br-16', ip:'10.2.0.16', health:'ok',       x:520, y:385, vrfs:['CORP','VOICE'], warnings:[] },
  { id:'CE-Branch-18', role:'CE-Branch', label:'Br-18', ip:'10.2.0.18', health:'ok',       x:590, y:385, vrfs:['CORP','VOICE'], warnings:[] },
  { id:'CE-Branch-20', role:'CE-Branch', label:'Br-20', ip:'10.2.0.20', health:'ok',       x:660, y:385, vrfs:['CORP','VOICE'], warnings:[] },
];

export const LINKS: NetworkLink[] = [
  // ── Inter-POP ring (ABR layer, area-0 backbone) ───────────────────────────
  { source:'P-1',  target:'P-5',  health:'ok'      },  // POP1→POP2
  { source:'P-2',  target:'P-6',  health:'warning'  },  // POP1→POP2 (P-6 fault)
  { source:'P-5',  target:'P-9',  health:'ok'      },  // POP2→POP3
  { source:'P-6',  target:'P-10', health:'warning'  },  // POP2→POP3 (P-6 fault)
  { source:'P-9',  target:'P-13', health:'ok'      },  // POP3→POP4
  { source:'P-10', target:'P-14', health:'ok'      },
  { source:'P-13', target:'P-17', health:'ok'      },  // POP4→POP5
  { source:'P-14', target:'P-18', health:'ok'      },
  { source:'P-17', target:'P-21', health:'ok'      },  // POP5→POP6
  { source:'P-18', target:'P-22', health:'ok'      },
  { source:'P-21', target:'P-1',  health:'ok'      },  // POP6→POP1 (ring closure)
  { source:'P-22', target:'P-2',  health:'ok'      },
  // inter-POP chords: [1,4] [2,5] [3,6]
  { source:'P-1',  target:'P-13', health:'ok'      },
  { source:'P-5',  target:'P-17', health:'ok'      },
  { source:'P-9',  target:'P-21', health:'ok'      },

  // ── Intra-POP (ABR → PE-facing) ───────────────────────────────────────────
  { source:'P-1',  target:'P-3',  health:'ok'      },
  { source:'P-2',  target:'P-4',  health:'ok'      },
  { source:'P-5',  target:'P-7',  health:'ok'      },
  { source:'P-6',  target:'P-8',  health:'warning'  },
  { source:'P-9',  target:'P-11', health:'ok'      },
  { source:'P-10', target:'P-12', health:'ok'      },
  { source:'P-13', target:'P-15', health:'ok'      },
  { source:'P-14', target:'P-16', health:'ok'      },
  { source:'P-17', target:'P-19', health:'ok'      },
  { source:'P-18', target:'P-20', health:'ok'      },
  { source:'P-21', target:'P-23', health:'ok'      },
  { source:'P-22', target:'P-24', health:'ok'      },

  // ── PE-facing P → PE ──────────────────────────────────────────────────────
  { source:'P-3',  target:'PE-1',  health:'ok'       },
  { source:'P-4',  target:'PE-2',  health:'ok'       },
  { source:'P-7',  target:'PE-3',  health:'critical'  },
  { source:'P-8',  target:'PE-4',  health:'ok'       },
  { source:'P-11', target:'PE-5',  health:'warning'   },
  { source:'P-12', target:'PE-6',  health:'ok'       },
  { source:'P-15', target:'PE-7',  health:'ok'       },
  { source:'P-16', target:'PE-8',  health:'warning'   },
  { source:'P-19', target:'PE-9',  health:'ok'       },
  { source:'P-20', target:'PE-10', health:'ok'       },
  { source:'P-23', target:'PE-11', health:'ok'       },
  { source:'P-24', target:'PE-12', health:'ok'       },

  // ── PE → Hub CE ───────────────────────────────────────────────────────────
  { source:'PE-1',  target:'CE-Hub-1', health:'ok'       },
  { source:'PE-2',  target:'CE-Hub-1', health:'ok'       },
  { source:'PE-3',  target:'CE-Hub-2', health:'critical'  },
  { source:'PE-4',  target:'CE-Hub-2', health:'ok'       },
  { source:'PE-5',  target:'CE-Hub-3', health:'warning'   },
  { source:'PE-6',  target:'CE-Hub-3', health:'ok'       },
  { source:'PE-7',  target:'CE-Hub-4', health:'ok'       },
  { source:'PE-8',  target:'CE-Hub-4', health:'warning'   },
  { source:'PE-9',  target:'CE-Hub-5', health:'ok'       },
  { source:'PE-10', target:'CE-Hub-5', health:'ok'       },
  { source:'PE-11', target:'CE-Hub-6', health:'ok'       },
  { source:'PE-12', target:'CE-Hub-6', health:'ok'       },

  // ── PE → DC CE ────────────────────────────────────────────────────────────
  { source:'PE-9',  target:'CE-DC-1', health:'ok' },
  { source:'PE-10', target:'CE-DC-2', health:'ok' },
  { source:'PE-11', target:'CE-DC-3', health:'ok' },
  { source:'PE-12', target:'CE-DC-4', health:'ok' },

  // ── Hub → Branch CE ───────────────────────────────────────────────────────
  { source:'CE-Hub-1', target:'CE-Branch-1',  health:'ok'       },
  { source:'CE-Hub-1', target:'CE-Branch-2',  health:'ok'       },
  { source:'CE-Hub-1', target:'CE-Branch-3',  health:'ok'       },
  { source:'CE-Hub-2', target:'CE-Branch-7',  health:'critical'  },
  { source:'CE-Hub-2', target:'CE-Branch-8',  health:'ok'       },
  { source:'CE-Hub-2', target:'CE-Branch-12', health:'warning'   },
  { source:'CE-Hub-3', target:'CE-Branch-15', health:'ok'       },
  { source:'CE-Hub-3', target:'CE-Branch-16', health:'ok'       },
  { source:'CE-Hub-5', target:'CE-Branch-18', health:'ok'       },
  { source:'CE-Hub-5', target:'CE-Branch-20', health:'ok'       },
];
