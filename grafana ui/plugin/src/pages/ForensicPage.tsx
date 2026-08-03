import React, { useEffect, useMemo, useState } from 'react';
import { css } from '@emotion/css';
import { PluginPage } from '@grafana/runtime';
import { GrafanaTheme2, renderMarkdown, textUtil } from '@grafana/data';
import { useStyles2, Alert, Button } from '@grafana/ui';

import { useDataClient } from '../data/DataClientContext';
import { CaseDetail, CaseSummary } from '../data/types';

// UI-4 (#53): forensic postmortems. The copilot exposes no case-listing route yet, so this runs
// on sample data behind a getCases/getCase seam — feature-detect a real client method, else fall
// back to the fixture. Wiring the real route later is a one-line swap (the seam already exists).
interface CaseApi {
  getCases?: () => Promise<CaseSummary[]>;
  getCase?: (id: string) => Promise<CaseDetail>;
}

const SAMPLE_CASES: CaseSummary[] = [
  { id: 'case-ldp-pe1', device: 'pe1', cause: 'ldp_session_flap', alert: true, abstain: false, ts: '2026-08-03T10:03:23Z' },
  { id: 'case-bgp-pe4', device: 'pe4', cause: 'bgp_cascade', alert: true, abstain: true, ts: '2026-08-03T09:41:02Z' },
];

const SAMPLE_MD: Record<string, string> = {
  'case-ldp-pe1': `# Forensic case case-ldp-pe1

- **device:** pe1
- **predicted cause:** ldp_session_flap (mpls)
- **alert:** true (calibrated p=0.91, threshold=0.6)
- **abstain:** false   **model-health:** R1
- **window:** [1785..., 1785...] frozen

## Report

LDP session on pe1 flapped [events:0], dropping label bindings; downstream pe3/pe4 lost
VPNv4 reachability [metrics:1]. Blast radius: 2 PE, 6 CE [topology:0].

## Trace

- tool \`search_logs\`({"device":"pe1"})
- gate ok=true missing=[]`,
  'case-bgp-pe4': `# Forensic case case-bgp-pe4

- **device:** pe4
- **predicted cause:** bgp_cascade (bgp)
- **alert:** true (calibrated p=0.63, threshold=0.6)
- **abstain:** true   **model-health:** R3

## Report

Anomalous BGP churn on pe4 [events:0], but no confident single root cause — the model abstained.
Evidence attached for the operator.`,
};

export function ForensicPage() {
  const styles = useStyles2(getStyles);
  const client = useDataClient() as unknown as CaseApi;

  const [cases, setCases] = useState<CaseSummary[]>([]);
  const [sample, setSample] = useState(true);
  const [selected, setSelected] = useState<string | null>(null);
  const [detailMd, setDetailMd] = useState<string>('');

  // Try the real seam; fall back to the fixture. `sample` drives the pending-backend banner.
  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      if (client.getCases) {
        try {
          const live = await client.getCases();
          if (!cancelled && live.length) {
            setCases(live);
            setSample(false);
            return;
          }
        } catch {
          /* fall through to sample */
        }
      }
      if (!cancelled) {
        setCases(SAMPLE_CASES);
        setSample(true);
      }
    };
    load();
    return () => {
      cancelled = true;
    };
  }, [client]);

  useEffect(() => {
    let cancelled = false;
    if (!selected) {
      setDetailMd('');
      return;
    }
    const load = async () => {
      if (!sample && client.getCase) {
        try {
          const d = await client.getCase(selected);
          if (!cancelled) {
            setDetailMd(d.caseMd);
          }
          return;
        } catch {
          /* fall through */
        }
      }
      if (!cancelled) {
        setDetailMd(SAMPLE_MD[selected] ?? '_case report unavailable_');
      }
    };
    load();
    return () => {
      cancelled = true;
    };
  }, [selected, sample, client]);

  const html = useMemo(() => textUtil.sanitize(renderMarkdown(detailMd)), [detailMd]);

  return (
    <PluginPage>
      <h1>Forensics</h1>
      {sample && (
        <Alert title="Sample data — pending copilot backend" severity="info">
          Auto-generated forensic postmortems will appear here once the copilot exposes a case-listing route.
          Showing sample cases for now.
        </Alert>
      )}

      <div className={styles.layout}>
        <ul className={styles.list}>
          {cases.map((c) => (
            <li key={c.id}>
              <Button
                variant={selected === c.id ? 'primary' : 'secondary'}
                size="sm"
                fill="outline"
                onClick={() => setSelected(c.id)}
              >
                <span className={styles.caseLabel}>
                  <strong>{c.device}</strong> · {c.cause}
                  {c.abstain ? ' · abstained' : c.alert ? ' · alert' : ''}
                </span>
              </Button>
            </li>
          ))}
        </ul>

        <div className={styles.detail}>
          {selected ? (
            <div className={styles.markdown} dangerouslySetInnerHTML={{ __html: html }} />
          ) : (
            <span className={styles.empty}>Select a case to read its report.</span>
          )}
        </div>
      </div>
    </PluginPage>
  );
}

const getStyles = (theme: GrafanaTheme2) => ({
  layout: css`
    display: grid;
    grid-template-columns: minmax(220px, 280px) 1fr;
    gap: ${theme.spacing(2)};
    margin-top: ${theme.spacing(1)};
  `,
  list: css`
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: ${theme.spacing(1)};
  `,
  caseLabel: css`
    font-size: ${theme.typography.bodySmall.fontSize};
  `,
  detail: css`
    border-left: 1px solid ${theme.colors.border.weak};
    padding-left: ${theme.spacing(2)};
    min-height: 200px;
  `,
  empty: css`
    color: ${theme.colors.text.secondary};
  `,
  markdown: css`
    h1 {
      font-size: ${theme.typography.h3.fontSize};
    }
    pre,
    code {
      font-family: ${theme.typography.fontFamilyMonospace};
      background: ${theme.colors.background.secondary};
      border-radius: ${theme.shape.radius.default};
    }
  `,
});
