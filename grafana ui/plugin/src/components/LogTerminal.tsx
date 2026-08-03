import React, { useEffect, useMemo, useRef, useState } from 'react';
import { css } from '@emotion/css';
import { GrafanaTheme2, SelectableValue } from '@grafana/data';
import { useStyles2, Select, Input } from '@grafana/ui';

import { NetworkEvent } from '../data/types';

interface Props {
  events: NetworkEvent[];
  height?: number;
}

const SEVERITY_OPTIONS: Array<SelectableValue<string>> = [
  { label: 'All', value: 'all' },
  { label: 'Error', value: 'error' },
  { label: 'Warning', value: 'warning' },
  { label: 'Info', value: 'info' },
];

// RFC5424: "<pri>version timestamp host app - - - message". Strip through the structured-data
// terminator " - - - " so the terminal shows just the message; leave non-syslog lines untouched.
export function cleanLine(line: string): string {
  const marker = ' - - - ';
  const idx = line.indexOf(marker);
  return idx === -1 ? line : line.slice(idx + marker.length);
}

function severityBucket(severity?: string): 'error' | 'warning' | 'info' | 'debug' {
  const s = (severity ?? '').toLowerCase();
  if (['error', 'critical', 'alert', 'emergency'].includes(s)) {
    return 'error';
  }
  if (s === 'warning') {
    return 'warning';
  }
  if (['notice', 'info', 'informational'].includes(s)) {
    return 'info';
  }
  return 'debug';
}

function hhmmss(tsMs: number): string {
  const d = new Date(tsMs);
  const pad = (n: number, w = 2) => String(n).padStart(w, '0');
  return `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:${pad(d.getUTCSeconds())}.${pad(d.getUTCMilliseconds(), 3)}`;
}

export function LogTerminal({ events, height = 320 }: Props) {
  const styles = useStyles2(getStyles);
  const [severity, setSeverity] = useState<string>('all');
  const [query, setQuery] = useState('');
  const scrollRef = useRef<HTMLDivElement>(null);

  const rows = useMemo(() => {
    const sorted = [...events].sort((a, b) => a.tsMs - b.tsMs);
    return sorted.filter((e) => {
      if (severity !== 'all' && severityBucket(e.severity) !== severity) {
        return false;
      }
      if (query && !cleanLine(e.line).toLowerCase().includes(query.toLowerCase())) {
        return false;
      }
      return true;
    });
  }, [events, severity, query]);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) {
      return;
    }
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 48;
    if (nearBottom) {
      el.scrollTop = el.scrollHeight;
    }
  }, [rows]);

  return (
    <div>
      <div className={styles.filterRow}>
        <Select width={18} options={SEVERITY_OPTIONS} value={severity} onChange={(v: SelectableValue<string>) => setSeverity(v?.value ?? 'all')} />
        <Input placeholder="filter message…" value={query} onChange={(e) => setQuery(e.currentTarget.value)} width={40} />
      </div>
      <div className={styles.terminal} style={{ maxHeight: height }} ref={scrollRef}>
        {rows.length === 0 && <div className={styles.dim}>no logs in window</div>}
        {rows.map((e, i) => {
          const bucket = severityBucket(e.severity);
          return (
            <div key={i} className={styles.line}>
              <span className={styles.ts}>{hhmmss(e.tsMs)}</span>{'  '}
              <span className={styles.device}>{e.device ?? '—'}</span>{'  '}
              <span className={styles.dim}>{e.app ?? '—'}</span>{'  '}
              <span className={styles[bucket]}>{cleanLine(e.line)}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

const getStyles = (theme: GrafanaTheme2) => ({
  filterRow: css`
    display: flex;
    gap: ${theme.spacing(1)};
    margin-bottom: ${theme.spacing(1)};
  `,
  terminal: css`
    background: #0b0e14;
    border-radius: ${theme.shape.radius.default};
    padding: ${theme.spacing(1)};
    overflow-y: auto;
    font-family: ${theme.typography.fontFamilyMonospace};
    font-size: ${theme.typography.bodySmall.fontSize};
  `,
  line: css`
    white-space: pre-wrap;
    word-break: break-word;
    line-height: 1.5;
  `,
  ts: css`
    color: #6b7280;
  `,
  device: css`
    color: #56d1e0;
  `,
  error: css`
    color: #ff5f56;
  `,
  warning: css`
    color: #f5a623;
  `,
  info: css`
    color: #3ddc84;
  `,
  debug: css`
    color: #8a8f98;
  `,
  dim: css`
    color: #6b7280;
  `,
});
