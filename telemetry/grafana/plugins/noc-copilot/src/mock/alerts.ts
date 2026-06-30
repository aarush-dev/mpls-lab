import type { Alert } from '../types';

export const MOCK_ALERTS: Alert[] = [
  { id: 'a1', device: 'PE-3',        faultType: 'BGP Route Flap Cascade',       severity: 'CRITICAL', tti: 8,  timestamp: '2026-06-30T08:41:00Z', confidence: 91.4 },
  { id: 'a2', device: 'CE-Branch-7', faultType: 'Interface Congestion',          severity: 'CRITICAL', tti: 11, timestamp: '2026-06-30T08:38:00Z', confidence: 87.2 },
  { id: 'a3', device: 'P-6',         faultType: 'MPLS LSP Forwarding Anomaly',   severity: 'WARNING',  tti: 18, timestamp: '2026-06-30T08:35:00Z', confidence: 74.1 },
  { id: 'a4', device: 'PE-8',        faultType: 'OSPF SPF Frequency Spike',      severity: 'WARNING',  tti: 23, timestamp: '2026-06-30T08:32:00Z', confidence: 68.9 },
  { id: 'a5', device: 'CE-Branch-12',faultType: 'WireGuard Rekey Timeout Risk',  severity: 'WARNING',  tti: 31, timestamp: '2026-06-30T08:29:00Z', confidence: 63.5 },
  { id: 'a6', device: 'CE-Hub-2',    faultType: 'Tunnel Jitter Accumulation',    severity: 'WARNING',  tti: 38, timestamp: '2026-06-30T08:26:00Z', confidence: 58.3 },
  { id: 'a7', device: 'PE-5',        faultType: 'BGP VRF Prefix Count Drift',    severity: 'WARNING',  tti: 45, timestamp: '2026-06-30T08:23:00Z', confidence: 54.7 },
];

export const ALERT_STATS = {
  total: 7,
  critical: 2,
  warning: 5,
  avgTti: 24.9,
  modelAccuracy: 94.2,
};
