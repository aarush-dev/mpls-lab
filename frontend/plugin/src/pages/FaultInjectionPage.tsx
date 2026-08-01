import React, { useEffect, useMemo, useState } from 'react';
import { css } from '@emotion/css';
import { PluginPage } from '@grafana/runtime';
import { GrafanaTheme2, SelectableValue } from '@grafana/data';
import { useStyles2, Select, Button, Icon } from '@grafana/ui';

import { useAppState, useAppDispatch } from '../state/AppContext';
import { useDataClient } from '../data/DataClientContext';
import { TopologyNode } from '../data/types';
import { nodeDetailPath } from '../constants';

// Demo control page: fire a fault at a node -> node turns red everywhere + a NodeDown alert is
// pushed to Grafana Alerting + a toast pops. All driven by state.injectedFaults (see reducer).
const FAULT_TYPES = [
  'node_failure',
  'congestion',
  'bgp_flap',
  'ospf_area_flap',
  'ldp_session_flap',
  'tunnel_degrade',
  'controller_drift',
  'policy_drift',
  'core_partition',
  'pop_isolation',
  'srlg_cut',
  'gray_failure',
];

export function FaultInjectionPage() {
  const styles = useStyles2(getStyles);
  const { injectedFaults } = useAppState();
  const dispatch = useAppDispatch();
  const dataClient = useDataClient();

  const [nodes, setNodes] = useState<TopologyNode[]>([]);
  const [node, setNode] = useState<string | undefined>(undefined);
  const [faultType, setFaultType] = useState<string>(FAULT_TYPES[0]);

  useEffect(() => {
    let cancelled = false;
    dataClient.getTopology({}).then((g) => {
      if (!cancelled) {
        setNodes(g.nodes);
      }
    });
    return () => {
      cancelled = true;
    };
  }, [dataClient]);

  const nodeOptions = useMemo<Array<SelectableValue<string>>>(
    () => nodes.map((n) => ({ label: `${n.id} (${n.role})`, value: n.id })),
    [nodes]
  );
  const faultOptions = useMemo<Array<SelectableValue<string>>>(
    () => FAULT_TYPES.map((f) => ({ label: f, value: f })),
    []
  );

  const inject = () => {
    if (node) {
      dispatch({ type: 'INJECT_FAULT', payload: { node, faultType } });
    }
  };

  return (
    <PluginPage>
      <h1>Fault Injection</h1>
      <p className={styles.help}>
        Fire a fault at a node. It stays healthy for ~5s, then a prediction fires (
        <span className={styles.amber}>amber</span>, 30/60/90s lead — Grafana Alerting + toast), then the
        node goes <span className={styles.red}>red</span> across every page with a NodeDown alert. The
        node page shows a fault-predictor curve building the whole time. Clear it to restore the node.
      </p>

      <div className={styles.controls}>
        <Select
          placeholder="Select node"
          options={nodeOptions}
          value={node ? { label: node, value: node } : null}
          onChange={(v) => setNode(v?.value)}
          width={32}
        />
        <Select
          options={faultOptions}
          value={{ label: faultType, value: faultType }}
          onChange={(v) => v?.value && setFaultType(v.value)}
          width={28}
        />
        <Button icon="bolt" variant="destructive" onClick={inject} disabled={!node}>
          Inject fault
        </Button>
      </div>

      <div className={styles.activeHead}>
        <h3 className={styles.sectionTitle}>Active injected faults ({injectedFaults.length})</h3>
        {injectedFaults.length > 0 && (
          <Button size="sm" variant="secondary" fill="outline" onClick={() => dispatch({ type: 'CLEAR_INJECTED' })}>
            Clear all
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
                {f.phase === 'pending'
                  ? 'arming…'
                  : f.phase === 'predicted'
                  ? `predicted in ${f.leadSec}s`
                  : 'down'}
              </span>
              <Button
                size="sm"
                variant="secondary"
                fill="outline"
                onClick={() => dispatch({ type: 'CLEAR_FAULT', payload: { node: f.node } })}
              >
                Clear
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
    max-width: 640px;
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
