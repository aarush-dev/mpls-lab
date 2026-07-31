// Data-driven role -> visual style registry for the topology graph.
// Adding a new node role later needs no code change: unknown roles fall back to `defaultRoleStyle`.

export interface RoleStyle {
  shape: 'ellipse' | 'round-rectangle' | 'diamond' | 'triangle' | 'hexagon';
  color: string;
  size: number;
}

export const roleStyles: Record<string, RoleStyle> = {
  P: { shape: 'diamond', color: '#5794f2', size: 42 },
  PE: { shape: 'round-rectangle', color: '#8ab8ff', size: 36 },
  CE: { shape: 'hexagon', color: '#b877d9', size: 30 },
  host: { shape: 'ellipse', color: '#8e9297', size: 16 },
};

export const defaultRoleStyle: RoleStyle = { shape: 'ellipse', color: '#8ab8ff', size: 28 };

export function styleForRole(role: string): RoleStyle {
  return roleStyles[role] ?? defaultRoleStyle;
}

// Health-state colors. red = down, amber = precursor, green = healthy.
export const stateColors: Record<'red' | 'amber' | 'green', string> = {
  red: '#e02f44',
  amber: '#ff9830',
  green: '#56a64b',
};

export const neutralColor = '#8e9297';

export function colorForState(state?: 'red' | 'amber' | 'green'): string {
  return state ? stateColors[state] : neutralColor;
}
