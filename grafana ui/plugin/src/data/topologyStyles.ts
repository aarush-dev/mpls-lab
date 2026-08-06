// Data-driven role -> visual style registry for the topology graph.
// Adding a new node role later needs no code change: unknown roles fall back to `defaultRoleStyle`.

export interface RoleStyle {
  shape: 'ellipse' | 'round-rectangle' | 'diamond' | 'triangle' | 'hexagon';
  color: string;
  size: number;
}

// Keys are the fixture role strings (lowercase). Core > edge > leaf, distinguished by shape + size.
export const roleStyles: Record<string, RoleStyle> = {
  p: { shape: 'diamond', color: '#5794f2', size: 42 },
  pe: { shape: 'round-rectangle', color: '#8ab8ff', size: 36 },
  ce_hub: { shape: 'hexagon', color: '#b877d9', size: 32 },
  ce_dc: { shape: 'hexagon', color: '#d98cc9', size: 34 },
  ce_branch: { shape: 'hexagon', color: '#b877d9', size: 26 },
  host: { shape: 'ellipse', color: '#8e9297', size: 16 },
};

export const defaultRoleStyle: RoleStyle = { shape: 'ellipse', color: '#8ab8ff', size: 28 };

export function styleForRole(role: string): RoleStyle {
  return roleStyles[role] ?? defaultRoleStyle;
}

// Health-state colors. red = down, amber = precursor (blue), yellow = stressed, green = healthy.
export const stateColors: Record<'red' | 'amber' | 'yellow' | 'green', string> = {
  red: '#e02f44',
  amber: '#5794f2',
  yellow: '#f2cc0c',
  green: '#56a64b',
};

export const neutralColor = '#8e9297';

export function colorForState(state?: 'red' | 'amber' | 'yellow' | 'green'): string {
  return state ? stateColors[state] : neutralColor;
}

// "Stressed" (yellow) thresholds for live sdwan_tunnel_latency_ms/jitter_ms/loss_pct — real
// backend only (baseline ~1-2ms per controller.py, congestion-scenario impact fires at 8ms).
// Scaled by node degree (connection count): a node with more links has more redundancy, so it
// takes a bigger excursion to call it stressed. Effective threshold = BASE * (1 + degree / 5).
export const STRESS_LATENCY_MS = 10;
export const STRESS_JITTER_MS = 5;
export const STRESS_LOSS_PCT = 2;
