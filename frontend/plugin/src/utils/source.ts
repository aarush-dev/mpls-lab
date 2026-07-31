// Helpers over DataSourceKind. Per plan decision #2, provenance is kept in the data model/types
// for future integration honesty, but is NOT surfaced in the UI by default — these maps exist for
// internal/optional use (e.g. a future debug panel), not for default rendering.

import { DataSourceKind } from '../data/types';

export const SOURCE_LABELS: Record<DataSourceKind, string> = {
  mock: 'Mock',
  measured: 'Measured',
  simulated: 'Simulated',
  modelled: 'Modelled',
  ground_truth: 'Ground Truth',
  prediction: 'Prediction',
};

/** Grafana state palette (plan §12): green/amber/red/gray/blue. */
export type SourceColor = 'green' | 'amber' | 'red' | 'gray' | 'blue';

export const SOURCE_COLORS: Record<DataSourceKind, SourceColor> = {
  mock: 'gray',
  measured: 'green',
  simulated: 'blue',
  modelled: 'amber',
  ground_truth: 'green',
  prediction: 'amber',
};

export function sourceLabel(source: DataSourceKind): string {
  return SOURCE_LABELS[source] ?? source;
}

export function sourceColor(source: DataSourceKind): SourceColor {
  return SOURCE_COLORS[source] ?? 'gray';
}
