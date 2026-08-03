import React, { useEffect, useMemo, useState } from 'react';
import { css } from '@emotion/css';
import { PluginPage } from '@grafana/runtime';
import { GrafanaTheme2, SelectableValue } from '@grafana/data';
import { useStyles2, Select, Button, Icon, Alert } from '@grafana/ui';

import { useAppState, useAppDispatch } from '../state/AppContext';
import { useDataClient } from '../data/DataClientContext';
import { FaultScenario, InjectFaultRequest, TopologyNode } from '../data/types';
import { nodeDetailPath } from '../constants';

// Fault Injection: fires a REAL fault in the simulated lab (dataapi /faults/inject -> orchestrator
// -> docker exec) so live graphs + logs actually break, AND drives the client-side visual escalation
// (state.injectedFaults) for instant amber->red feedback. Auto-reverts after `duration`; "Revert now"
// (/faults/revert) cancels early. If the real inject fails, the optimistic visual is rolled back.

// Fallback scenario list if the backend /faults/scenarios probe fails (e.g. mock mode).
const FALLBACK_SCENARIOS = [
  'node_failure', 'congestion', 'bgp_flap', 'ospf_area_flap', 'ldp_session_flap', 'tunnel_degrade',
  'controller_drift', 'policy_drift', 'core_partition', 'pop_isolation', 'srlg_cut', 'gray_failure',
];
const DURATIONS = [30, 60, 90, 180];

interface FaultApi {
  getScenarios?: () => Promise<FaultScenario[]>;
  injectFault?: (r: InjectFaultRequest) => Promise<{ scenario_id: string }>;
  revertFault?: (id: string) => Promise<unknown>;
}

export function FaultInjectionPage() {
  const styles = useStyles2(getStyles);
  const { injectedFaults } = useAppState();
  const dispatch = useAppDispatch();
  const dataClient = useDataClient();
  const faultApi = dataClient as unknown as FaultApi;

  const [nodes, setNodes] = useState<TopologyNode[]>([]);
  const [scenarios, setScenarios] = useState<FaultScenario[]>([]);
  const [node, setNode] = useState<string | undefined>(undefined);
  const [scenario, setScenario] = useState<string>(FALLBACK_SCENARIOS[0]);
  const [duration, setDuration] = useState<number>(90);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  // node -> backend scenario_id, so "Revert now" can cancel the real fault early.
  const [injectIds, setInjectIds] = useState<Record<string, string>>({});

  useEffect(() => {
    let cancelled = false;
    dataClient.getTopology({}).then((g) => {
      if (!cancelled) {
        setNodes(g.nodes);
      }
    });
    if (typeof faultApi.getScenarios === 'function') {
      faultApi
        .getScenarios()
        .then((s) => !cancelled && Array.isArray(s) && s.length > 0 && setScenarios(s))
        .catch(() => undefined);
    }
    return () => {
      cancelled = true;
    };
  }, [dataClient, faultApi]);

  const scenarioNames: string[] = scenarios.length > 0 ? scenarios.map((s) => String(s.name)) : FALLBACK_SCENARIOS;

  const nodeOptions = useMemo<Array<SelectableValue<string>>>(
    () => nodes.map((n) => ({ label: `${n.id} (${n.role})`, value: n.id })),
    [nodes]
  );
  const scenarioOptions = useMemo<Array<SelectableValue<string>>>(
    () => scenarioNames.map((f) => ({ label: f, value: f })),
    [scenarioNames]
  );

  const inject = async () => {
    if (!node) {
      return;
    }
    setErr(null);
    // Optimistic client-side visual escalation (instant feedback).
    dispatch({ type: 'INJECT_FAULT', payload: { node, faultType: scenario } });

    if (typeof faultApi.injectFault !== 'function') {
      return; // mock mode: visual only.
    }
    setBusy(true);
    try {
      const res = await faultApi.injectFault({ scenario, target: node, duration });
      if (res?.scenario_id) {
        setInjectIds((m) => ({ ...m, [node]: res.scenario_id }));
      }
    } catch (e) {
      // Real inject failed — roll back the visual so we never show a fault the sim didn't take.
      dispatch({ type: 'CLEAR_FAULT', payload: { node } });
      const msg = (e as { message?: string })?.message ?? 'inject failed';
      setErr(`Could not inject ${scenario} on ${node}: ${msg}`);
    } finally {
      setBusy(false);
    }
  };

  const clearOne = (n: string) => {
    dispatch({ type: 'CLEAR_FAULT', payload: { node: n } });
    const id = injectIds[n];
    if (id && typeof faultApi.revertFault === 'function') {
      faultApi.revertFault(id).catch(() => undefined);
    }
    setInjectIds((m) => {
      const next = { ...m };
      delete next[n];
      return next;
    });
  };

  const clearAll = () => {
    if (typeof faultApi.revertFault === 'function') {
      Object.values(injectIds).forEach((id) => faultApi.revertFault!(id).catch(() => undefined));
    }
    setInjectIds({});
    dispatch({ type: 'CLEAR_INJECTED' });
  };

  const liveApi = typeof faultApi.injectFault === 'function';

  return (
    <PluginPage>
      <h1>Fault Injection</h1>
      <p className={styles.help}>
        Fires a <strong>real</strong> fault in the simulated lab — live graphs and logs actually break —
        and shows instant escalation: healthy ~5s, then a prediction (<span className={styles.amber}>amber</span>,
        30/60/90s lead — Grafana Alerting + toast), then <span className={styles.red}>red</span> across every page.
        The fault auto-reverts after the chosen duration; <em>Revert now</em> cancels it early.
        {!liveApi && ' (Backend offline — running in visual-only demo mode.)'}
      </p>

      {err && (
        <Alert title="Injection failed" severity="error" onRemove={() => setErr(null)}>
          {err}
        </Alert>
      )}

      <div className={styles.controls}>
        <Select
          placeholder="Select node"
          options={nodeOptions}
          value={node ? { label: node, value: node } : null}
          onChange={(v) => setNode(v?.value)}
          width={32}
        />
        <Select
          options={scenarioOptions}
          value={{ label: scenario, value: scenario }}
          onChange={(v) => v?.value && setScenario(v.value)}
          width={28}
        />
        <Select<number>
          options={DURATIONS.map((d) => ({ label: `${d}s`, value: d }))}
          value={{ label: `${duration}s`, value: duration }}
          onChange={(v) => v?.value && setDuration(v.value)}
          width={14}
        />
        <Button icon="bolt" variant="destructive" onClick={inject} disabled={!node || busy}>
          {busy ? 'Injecting…' : 'Inject fault'}
        </Button>
      </div>

      <div className={styles.activeHead}>
        <h3 className={styles.sectionTitle}>Active injected faults ({injectedFaults.length})</h3>
        {injectedFaults.length > 0 && (
          <Button size="sm" variant="secondary" fill="outline" onClick={clearAll}>
            Revert all
          </Button>
        )}
      </div>

      {injectedFaults.length === 0 ? (
        <span className={styles.empty}>None. Inject a fault above.</span>
      ) : (
        <ul className={styles.list}>
          {injectedFaults.map((f) => (
            <li key={f.node} className={styles.row}>
              <Icon
                name={f.phase === 'down' ? 'exclamation-circle' : f.phase === 'predicted' ? 'exclamation-triangle' : 'clock-nine'}
                className={f.phase === 'predicted' ? styles.amber : f.phase === 'pending' ? styles.pending : styles.red}
              />
              <a className={styles.node} href={nodeDetailPath(f.node)}>
                {f.node}
              </a>
              <span className={styles.fault}>
                {f.faultType} ·{' '}
                {f.phase === 'pending' ? 'arming…' : f.phase === 'predicted' ? `predicted in ${f.leadSec}s` : 'down'}
                {injectIds[f.node] ? ' · live' : ''}
              </span>
              <Button size="sm" variant="secondary" fill="outline" onClick={() => clearOne(f.node)}>
                Revert now
              </Button>
            </li>
          ))}
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
