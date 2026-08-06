import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { css } from '@emotion/css';
import { PluginPage } from '@grafana/runtime';
import { GrafanaTheme2, SelectableValue } from '@grafana/data';
import { useStyles2, Select, Button, Icon, Alert } from '@grafana/ui';

import { LIVE_REFRESH_MS } from '../state/AppContext';
import { useDataClient } from '../data/DataClientContext';
import { ActiveFault, FaultScenario, InjectFaultRequest, TopologyNode } from '../data/types';
import { targetOptions, isValidTarget } from '../data/faultTargets';
import { nodeDetailPath } from '../constants';

// Fault Injection: fires a REAL fault in the simulated lab (dataapi /faults/inject -> orchestrator
// -> docker exec) and watches each injection follow the backend's true lifecycle
// (buildup -> impact -> hold -> revert) via GET /faults/active. No client-side visual layer: the
// rows ARE the registry, so they survive a page refresh and clear when the backend reverts.

const SEVERITIES: Array<InjectFaultRequest['severity']> = ['low', 'medium', 'high'];
const DEFAULT_DURATION = 90;

// The fault methods live on HttpDataClient beyond the shared DataClient interface; mock mode has
// none of them, so the page degrades to an empty (visual-only) list.
interface FaultApi {
  getScenarios?: () => Promise<FaultScenario[]>;
  injectFault?: (r: InjectFaultRequest) => Promise<{ scenario_id: string }>;
  getActiveFaults?: () => Promise<ActiveFault[]>;
  revertFault?: (id: string) => Promise<unknown>;
}

const parseMs = (iso?: string | null): number | undefined => {
  if (!iso) {
    return undefined;
  }
  const ms = Date.parse(iso);
  return Number.isNaN(ms) ? undefined : ms;
};

/** Phase-driven display: color, icon, and a live countdown label recomputed from cached times. */
function phaseView(f: ActiveFault, nowMs: number, styles: ReturnType<typeof getStyles>) {
  const impactMs = parseMs(f.t_impact);
  const sec = (ms: number) => Math.max(0, Math.round((ms - nowMs) / 1000));
  if (f.phase === 'reverting') {
    return { color: styles.pending, icon: 'clock-nine' as const, label: 'reverting…' };
  }
  if (f.phase === 'impact') {
    const revertMs = impactMs !== undefined && f.duration != null ? impactMs + f.duration * 1000 : undefined;
    return {
      color: styles.red,
      icon: 'exclamation-circle' as const,
      label: revertMs !== undefined ? `impact · hold, revert in ${sec(revertMs)}s` : 'impact · hold',
    };
  }
  // buildup (or a null phase in the sub-ms window before the worker's first status write).
  return {
    color: styles.amber,
    icon: 'exclamation-triangle' as const,
    label: impactMs !== undefined ? `buildup · impact in ${sec(impactMs)}s` : 'buildup · arming…',
  };
}

export function FaultInjectionPage() {
  const styles = useStyles2(getStyles);
  const dataClient = useDataClient();
  const faultApi = dataClient as unknown as FaultApi;

  const [nodes, setNodes] = useState<TopologyNode[]>([]);
  const [scenarios, setScenarios] = useState<FaultScenario[]>([]);
  const [scenario, setScenario] = useState<string | undefined>(undefined);
  const [target, setTarget] = useState<string | undefined>(undefined);
  const [severity, setSeverity] = useState<InjectFaultRequest['severity']>('medium');
  const [duration, setDuration] = useState<number>(DEFAULT_DURATION);
  const [active, setActive] = useState<ActiveFault[]>([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [nowMs, setNowMs] = useState(() => Date.now());

  // Topology + real scenario catalog. Re-runs if the client swaps (mock<->live); a client outage
  // just leaves the catalog empty (Inject stays disabled) rather than throwing.
  useEffect(() => {
    let cancelled = false;
    dataClient.getTopology({}).then((g) => !cancelled && setNodes(g.nodes)).catch(() => undefined);
    faultApi
      .getScenarios?.()
      .then((s) => {
        if (!cancelled && Array.isArray(s) && s.length > 0) {
          setScenarios(s);
          setScenario((cur) => cur ?? s[0].name);
          setDuration((cur) => (cur === DEFAULT_DURATION ? s[0].default_duration : cur));
        }
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [dataClient, faultApi]);

  // Active-fault registry: own ~5s poll (mode-independent — the tab drives real faults even when
  // the global clock is paused in history). `mutSeq` drops a stale in-flight response that would
  // otherwise resurrect a row an optimistic revert just cleared.
  const mutSeq = useRef(0);
  const fetchActive = useCallback(() => {
    const seq = mutSeq.current;
    faultApi
      .getActiveFaults?.()
      .then((a) => Array.isArray(a) && seq === mutSeq.current && setActive(a))
      .catch(() => undefined);
  }, [faultApi]);
  useEffect(() => {
    fetchActive();
    const id = setInterval(fetchActive, LIVE_REFRESH_MS);
    return () => clearInterval(id);
  }, [fetchActive]);

  // Local 1s display tick: recompute countdown labels from cached impact times (no network).
  useEffect(() => {
    if (active.length === 0) {
      return undefined;
    }
    setNowMs(Date.now()); // fresh clock on (re)start so the first label isn't stale
    const id = setInterval(() => setNowMs(Date.now()), 1000);
    return () => clearInterval(id);
  }, [active.length]);

  const current = useMemo(() => scenarios.find((s) => s.name === scenario), [scenarios, scenario]);
  const validRoles = current?.valid_roles ?? [];
  // Only device targets have a node-detail page; pop/srlg/custom targets render as plain text.
  const nodeIds = useMemo(() => new Set(nodes.map((n) => n.id)), [nodes]);

  const scenarioOptions = useMemo<Array<SelectableValue<string>>>(
    () => scenarios.map((s) => ({ label: s.name, value: s.name, description: s.description })),
    [scenarios]
  );
  const targetOpts = useMemo<Array<SelectableValue<string>>>(
    () => targetOptions(validRoles, nodes).map((o) => ({ label: o.label, value: o.value })),
    [validRoles, nodes]
  );

  const onScenario = (name?: string) => {
    if (!name) {
      return;
    }
    setScenario(name);
    setTarget(undefined);
    const s = scenarios.find((x) => x.name === name);
    if (s) {
      setDuration(s.default_duration);
    }
  };

  const inject = async () => {
    if (!scenario || !target) {
      return;
    }
    setErr(null);
    // Client-side coarse role pre-check; the backend 422 is the backstop for sub-role rules.
    if (validRoles.length > 0 && !isValidTarget(target, validRoles)) {
      setErr(`Target '${target}' is not valid for '${scenario}' (roles: ${validRoles.join(', ')}).`);
      return;
    }
    if (typeof faultApi.injectFault !== 'function') {
      setErr('Backend offline — fault injection unavailable in demo mode.');
      return;
    }
    setBusy(true);
    try {
      await faultApi.injectFault({ scenario, target, severity, duration });
      fetchActive(); // reflect the new injection immediately
    } catch (e) {
      const msg = (e as { message?: string })?.message ?? 'inject failed';
      setErr(`Could not inject ${scenario} on ${target}: ${msg}`);
    } finally {
      setBusy(false);
    }
  };

  const revertOne = (id: string) => {
    mutSeq.current++; // invalidate any in-flight fetch so it can't resurrect this row
    faultApi.revertFault?.(id).catch(() => undefined);
    setActive((a) => a.filter((f) => f.scenario_id !== id)); // optimistic; next poll confirms
  };
  const revertAll = () => {
    mutSeq.current++;
    active.forEach((f) => faultApi.revertFault?.(f.scenario_id).catch(() => undefined));
    setActive([]);
  };

  return (
    <PluginPage>
      <h1>Fault Injection</h1>
      <p className={styles.help}>
        Fires a <strong>real</strong> fault in the simulated lab — live graphs and logs actually break — then
        follows the backend lifecycle: <span className={styles.amber}>buildup</span> (precursor lead) →{' '}
        <span className={styles.red}>impact</span> → hold → auto-revert after the duration. <em>Revert now</em>{' '}
        cancels early.
      </p>

      {err && (
        <Alert title="Injection failed" severity="error" onRemove={() => setErr(null)}>
          {err}
        </Alert>
      )}

      <div className={styles.controls}>
        <Select
          placeholder="Scenario"
          options={scenarioOptions}
          value={scenario ? { label: scenario, value: scenario } : null}
          onChange={(v) => onScenario(v?.value)}
          width={28}
        />
        <Select
          placeholder="Target"
          options={targetOpts}
          value={target ? { label: target, value: target } : null}
          onChange={(v) => setTarget(v?.value)}
          allowCustomValue
          onCreateOption={(v) => setTarget(v)}
          width={30}
        />
        <Select<InjectFaultRequest['severity']>
          options={SEVERITIES.map((s) => ({ label: s!, value: s }))}
          value={{ label: severity!, value: severity }}
          onChange={(v) => v?.value && setSeverity(v.value)}
          width={16}
        />
        <Select<number>
          options={[30, 60, 90, 180].map((d) => ({ label: `${d}s`, value: d }))}
          value={{ label: `${duration}s`, value: duration }}
          onChange={(v) => v?.value && setDuration(v.value)}
          allowCustomValue
          onCreateOption={(v) => {
            const n = Number(v);
            if (Number.isFinite(n) && n > 0) {
              setDuration(Math.round(n));
            }
          }}
          width={14}
        />
        <Button icon="bolt" variant="destructive" onClick={inject} disabled={!scenario || !target || busy}>
          {busy ? 'Injecting…' : 'Inject fault'}
        </Button>
      </div>

      <div className={styles.activeHead}>
        <h3 className={styles.sectionTitle}>Active faults ({active.length})</h3>
        {active.length > 0 && (
          <Button size="sm" variant="secondary" fill="outline" onClick={revertAll}>
            Revert all
          </Button>
        )}
      </div>

      {active.length === 0 ? (
        <span className={styles.empty}>None. Inject a fault above.</span>
      ) : (
        <ul className={styles.list}>
          {active.map((f) => {
            const v = phaseView(f, nowMs, styles);
            return (
              <li key={f.scenario_id} className={styles.row}>
                <Icon name={v.icon} className={v.color} />
                {nodeIds.has(f.target) ? (
                  <a className={styles.node} href={nodeDetailPath(f.target)}>
                    {f.target}
                  </a>
                ) : (
                  <span className={styles.node}>{f.target}</span>
                )}
                <span className={styles.fault}>
                  {f.scenario} · {v.label}
                </span>
                <Button size="sm" variant="secondary" fill="outline" onClick={() => revertOne(f.scenario_id)}>
                  Revert now
                </Button>
              </li>
            );
          })}
        </ul>
      )}
    </PluginPage>
  );
}

const getStyles = (theme: GrafanaTheme2) => ({
  help: css`
    color: ${theme.colors.text.secondary};
    max-width: 680px;
  `,
  red: css`
    color: ${theme.colors.error.text};
  `,
  amber: css`
    color: ${theme.colors.warning.text};
  `,
  pending: css`
    color: ${theme.colors.text.secondary};
  `,
  controls: css`
    display: flex;
    gap: ${theme.spacing(2)};
    align-items: center;
    margin: ${theme.spacing(2)} 0;
    flex-wrap: wrap;
  `,
  activeHead: css`
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: ${theme.spacing(2)};
    max-width: 640px;
  `,
  sectionTitle: css`
    margin-top: ${theme.spacing(2)};
  `,
  empty: css`
    color: ${theme.colors.text.secondary};
  `,
  list: css`
    list-style: none;
    padding: 0;
    max-width: 640px;
  `,
  row: css`
    display: flex;
    align-items: center;
    gap: ${theme.spacing(1.5)};
    padding: ${theme.spacing(1)} 0;
    border-bottom: 1px solid ${theme.colors.border.weak};
  `,
  node: css`
    font-family: ${theme.typography.fontFamilyMonospace};
    font-weight: ${theme.typography.fontWeightMedium};
  `,
  fault: css`
    color: ${theme.colors.text.secondary};
    margin-right: auto;
  `,
});
