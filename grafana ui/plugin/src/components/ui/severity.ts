import { GrafanaTheme2 } from '@grafana/data';

// Single source of truth for state/severity colors. Both vocabularies fold onto four Grafana
// semantic tones so red/amber/green reads the same on every page and adapts to light/dark.
// No hardcoded hex — every color comes from GrafanaTheme2.

export type State = 'red' | 'amber' | 'green' | 'unknown';
export type Severity = 'low' | 'med' | 'high' | 'unknown';
export type Tone = 'error' | 'warning' | 'success' | 'neutral';

const STATE_TONE: Record<State, Tone> = { red: 'error', amber: 'warning', green: 'success', unknown: 'neutral' };
const SEVERITY_TONE: Record<Severity, Tone> = { high: 'error', med: 'warning', low: 'success', unknown: 'neutral' };

export interface ToneColor {
  /** readable-on-background text color */
  text: string;
  /** solid fill (dots, bars) */
  main: string;
  /** translucent tint for backgrounds */
  bg: string;
}

export function toneColor(theme: GrafanaTheme2, tone: Tone): ToneColor {
  switch (tone) {
    case 'error':
      return { text: theme.colors.error.text, main: theme.colors.error.main, bg: theme.colors.error.transparent };
    case 'warning':
      return { text: theme.colors.warning.text, main: theme.colors.warning.main, bg: theme.colors.warning.transparent };
    case 'success':
      return { text: theme.colors.success.text, main: theme.colors.success.main, bg: theme.colors.success.transparent };
    case 'neutral':
      return { text: theme.colors.text.secondary, main: theme.colors.text.secondary, bg: theme.colors.background.secondary };
  }
}

export function colorForState(theme: GrafanaTheme2, state?: State): ToneColor {
  return toneColor(theme, STATE_TONE[state ?? 'unknown']);
}

export function colorForSeverity(theme: GrafanaTheme2, severity?: Severity): ToneColor {
  return toneColor(theme, SEVERITY_TONE[severity ?? 'unknown']);
}
