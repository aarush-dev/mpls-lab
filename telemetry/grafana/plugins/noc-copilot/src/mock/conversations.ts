import type { ChatMessage } from '../types';

export const PREDICTIVE_CONVO: ChatMessage[] = [
  { role: 'user', content: 'What is causing the latency spike on Hub-2 tunnels?' },
  { role: 'ai', content: `**Issue Type:** Tunnel Jitter Accumulation
**Confidence:** 87% ████████████░░
**Root Cause:** Interface buffer overflow on CE-Hub-2 eth1 (RX utilization 91% over 12 min). Contributing: OSPF SPF events on PE-8 adding 3–5 ms path latency. Jitter σ has risen from 2.1 ms to 8.7 ms over the past 28 minutes — trajectory exceeds VoIP SLA threshold in ~38 min.
**Affected Sites:** CE-Branch-7, CE-Branch-8, CE-Branch-12 via Hub-2
**RAG Sources:** topology-meta.json · runbook-jitter.md` },
  { role: 'user', content: 'Show me the tunnel latency trend for the last 2 hours.' },
  { role: 'ai', content: 'Generating visualization for CE-Hub-2 tunnel latency over the last 2 hours…', showChart: true },
];

export const DIAGNOSTIC_CONVO: ChatMessage[] = [
  {
    role: 'ai',
    content: `**PREDICTED ISSUE TYPE**
BGP Route Flap Cascade

**CONFIDENCE SCORE**
91.4%  ████████████████░░

**ROOT CAUSE HYPOTHESIS**
Sustained BGP KEEPALIVE miss-fires on PE-3 eBGP session to CE-Branch-7. Hold timer at 23 s of 90 s consumed. Contributing signal: interface utilization spike on PE-3 eth2 (94% for 7 min) causing scheduler latency and delayed keepalive processing.

**AFFECTED SCOPE**
PE-3, CE-Branch-7, CE-Branch-12, CE-Hub-2
VRFs: BRANCH-VPN, MGMT

**ESTIMATED TIME-TO-IMPACT**
8 minutes

**RECOMMENDED ACTIONS**
1. \`clear ip bgp 10.0.0.7 soft\`  (PE-3, no traffic hit)
2. Monitor CE-Branch-7 hold timer — escalate if < 10 s remaining
3. Pre-position: set PE-4 as preferred next-hop via existing route-map RM-FAILOVER

**RAG SOURCES**
topology-meta.json · runbook-bgp-flap.md · incident-2024-03-15.md`,
  },
  { role: 'user', content: 'What is the blast radius if this flap propagates?' },
  {
    role: 'ai',
    content: `**Blast Radius Analysis**

If PE-3 eBGP session to CE-Branch-7 drops and hold timer expires:

• **Route withdrawals:** 47 prefixes withdrawn from BRANCH-VPN VRF across all PEs via iBGP RR at PE-1/PE-2
• **Reconvergence path:** Fallback route via PE-4 → CE-Hub-2 → CE-Branch-7 activates (pre-configured RM-FAILOVER). Estimated reconvergence: ~45 s for BGP + BFD convergence.
• **Downstream VRFs impacted:** BRANCH-VPN (primary), MGMT (secondary — management traffic loss for 45 s)
• **Host impact:** 47 branch hosts lose reachability for ~45 s. SCADA VRF isolated on separate PE — unaffected.
• **SLA breach:** 45 s exceeds BRANCH-VPN SLA of 30 s maximum outage.

**Immediate recommendation:** Execute action 1 (soft-clear) now — prevents hold timer expiry with zero traffic impact.`,
  },
];

export const ASSISTANT_CONVO: ChatMessage[] = [
  { role: 'user', content: 'What is the current health of the MPLS core?' },
  {
    role: 'ai',
    content: `**MPLS Core Status — 08:42 UTC**

• **P routers:** 7/8 fully operational. P-6 flagged — MPLS LSP forwarding anomaly (TTI 18 min, confidence 74%).
• **LDP sessions:** 119/120 established. One session (P-6 ↔ PE-7) in OPERATIONAL state with elevated miss-count.
• **LSP count:** 1,847 active label-switched paths. 3 LSPs rerouted via backup paths in last 30 min.
• **BFD:** All 52 BFD sessions active. No detection events in last 15 min.

**Risk summary:** Low-moderate. P-6 anomaly is the single active concern in the core. Recommend monitoring LSP count on P-6 — if LSP withdrawals begin, intervention needed within 18 min.`,
  },
  { role: 'user', content: 'Show me traffic distribution across VRFs.' },
  { role: 'ai', content: 'Generating VRF traffic breakdown from the last 30-minute sample window…', showChart: true },
  { role: 'user', content: 'Which branch sites are at highest risk in the next 30 minutes?' },
  {
    role: 'ai',
    content: `**30-Minute Risk Forecast — Branch Sites**

| Rank | Site | Risk Score | Primary Signal | TTI |
|------|------|-----------|----------------|-----|
| 1 | CE-Branch-7  | 94% CRIT | Interface congestion, RX queue 97% | 11 min |
| 2 | CE-Branch-12 | 71% WARN | WireGuard rekey timeout risk | 31 min |
| 3 | CE-Branch-3  | 44% WARN | BGP hold timer drift (indirect — PE-3 cascade) | ~25 min |
| 4 | CE-Branch-8  | 38% WARN | Tunnel jitter accumulation via Hub-2 | ~38 min |

**Recommended pre-actions:**
1. CE-Branch-7: Immediately reduce traffic via QoS DSCP remarking or reroute to Hub-3 alternate path
2. CE-Branch-12: Trigger manual WireGuard rekey now (\`wg set wg0 peer <pubkey>\`) — prevents service drop
3. CE-Branch-3: No action required yet — monitor PE-3 BGP session status`,
  },
];
