import React, { useEffect, useMemo, useState } from 'react';
import { useHistory } from 'react-router-dom';
import { css } from '@emotion/css';
import { PluginPage } from '@grafana/runtime';
import { GrafanaTheme2 } from '@grafana/data';
import { useStyles2, Input } from '@grafana/ui';

import { TopologyGraph } from '../components/TopologyGraph';
import { EmptyState } from '../components/EmptyState';
import { ErrorState } from '../components/ErrorState';
import { useAppState } from '../state/AppContext';
import { useDataClient } from '../data/DataClientContext';
import { MetricSeries, TopologyLink, TopologyNodeLive } from '../data/types';
import { InjectedFault } from '../state/reducer';
import { nodeDetailPath } from '../constants';
import { stateColors } from '../data/topologyStyles';

// Overlay manually-injected faults (Fault Injection page) onto the real /labels-derived node
// state, so a fresh injection colors the node instantly instead of waiting on the next real signal.
function applyInjectedFaults(nodes: TopologyNodeLive[], injectedFaults: InjectedFault[]): TopologyNodeLive[] {
  if (injectedFaults.length === 0) {
    return nodes;
  }
  const byNode = new Map(injectedFaults.map((f) => [f.node, f]));
  return nodes.map((n) => {
    const fault = byNode.get(n.id);
    if (!fault) {
      return n;
    }
    if (fault.phase === 'down') {
      return { ...n, state: 'red' };
    }
    if (fault.phase === 'predicted') {
      return { ...n, state: 'amber' };
    }
    // 'pending' — leave the real state as-is.
    return n;
  });
}

export function TopologyPage() {
  const styles = useStyles2(getStyles);
  const history = useHistory();
  const { refreshTick, range, filters, injectedFaults } = useAppState();
  const dataClient = useDataClient();

  const [nodes, setNodes] = useState<TopologyNodeLive[]>([]);
  const [links, setLinks] = useState<TopologyLink[]>([]);
  const [status, setStatus] = useState<'loading' | 'ready' | 'error'>('loading');
  const [search, setSearch] = useState('');
  const [reloadToken, setReloadToken] = useState(0);
  const [hover, setHover] = useState<{ id: string; x: number; y: number } | null>(null);
  const [snapshot, setSnapshot] = useState<MetricSeries[] | null>(null);

  // Lazily pull the hovered node's telemetry for the mini card (one small fetch per hover).
  const hoverId = hover?.id ?? null;
  useEffect(() => {
    if (!hoverId) {
      setSnapshot(null);
      return;
    }
    let cancelled = false;
    dataClient.getTelemetry({ deviceId: hoverId, timeRange: { fromMs: range.fromMs, toMs: range.toMs } }).then((s) => {
      if (!cancelled) {
        setSnapshot(s);
      }
    });
    return () => {
      cancelled = true;
    };
  }, [dataClient, hoverId, refreshTick, range.fromMs, range.toMs]);

  useEffect(() => {
    let cancelled = false;
    // Do NOT flip to 'loading' on refresh ticks — that unmounts the graph every 5s and makes it
    // blink/re-layout. Keep the mounted graph and just replace its data; only the very first load
    // (or a manual reload) shows the loading state.
    dataClient
      .getTopology(filters)
      .then((graph) => {
        if (cancelled) {
          return;
        }
        setNodes(applyInjectedFaults(graph.nodes as TopologyNodeLive[], injectedFaults));
        setLinks(graph.links);
        setStatus('ready');
      })
      .catch(() => {
        if (!cancelled) {
          // Only surface an error if we have nothing to show; a transient tick failure keeps stale.
          setStatus((s) => (s === 'ready' ? 'ready' : 'error'));
        }
      });

    return () => {
      cancelled = true;
    };
  }, [dataClient, refreshTick, filters, reloadToken, injectedFaults]);

  const filteredNodes = useMemo(() => {
    const term = search.trim().toLowerCase();
    if (!term) {
      return nodes;
    }
    return nodes.filter((n) => n.id.toLowerCase().includes(term));
  }, [nodes, search]);

  const filteredIds = useMemo(() => new Set(filteredNodes.map((n) => n.id)), [filteredNodes]);
  const filteredLinks = useMemo(
    () => links.filter((l) => filteredIds.has(l.source) && filteredIds.has(l.target)),
    [links, filteredIds]
  );

  return (
    <PluginPage>
      <h1>Topology</h1>

      <div className={styles.toolbar}>
        <Input
          placeholder="Search node id…"
          value={search}
          onChange={(e) => setSearch(e.currentTarget.value)}
          width={40}
        />
        <div className={styles.legend}>
          <span className={styles.legendItem}>
            <span className={styles.swatch} style={{ background: stateColors.green }} /> Healthy
          </span>
          <span className={styles.legendItem}>
            <span className={styles.swatch} style={{ background: stateColors.amber }} /> Precursor
          </span>
          <span className={styles.legendItem}>
            <span className={styles.swatch} style={{ background: stateColors.red }} /> Down
          </span>
        </div>
      </div>

      {status === 'loading' && <EmptyState message="Loading topology…" />}
      {status === 'error' && <ErrorState onRetry={() => setReloadToken((t) => t + 1)} />}
      {status === 'ready' && nodes.length === 0 && <EmptyState />}
      {status === 'ready' && nodes.length > 0 && (
        <div className={styles.graphWrap}>
          <TopologyGraph
            nodes={filteredNodes}
            links={filteredLinks}
            onSelectNode={(id) => history.push(nodeDetailPath(id))}
            onHoverNode={(id, pos) => setHover(id && pos ? { id, x: pos.x, y: pos.y } : null)}
          />
          {hover && (
            <NodeHoverCard
              node={nodes.find((n) => n.id === hover.id)}
              phase={injectedFaults.find((f) => f.node === hover.id)?.phase}
              snapshot={snapshot}
              x={hover.x}
              y={hover.y}
            />
          )}
        </div>
      )}
    </PluginPage>
  );
}

// Latest numeric value of the series whose key ends with `suffix`, formatted; '' if absent.
function latest(snapshot: MetricSeries[] | null, suffix: string): string {
  const s = snapshot?.find((m) => (m.key.split(':').pop() ?? '') === suffix);
  const v = s?.points[s.points.length - 1]?.value;
  if (v == null) {
    return '';
  }
  const num = Math.abs(v) >= 1000 ? Math.round(v).toLocaleString() : Math.round(v * 10) / 10;
  return `${num}${s?.unit && s.unit !== 'bytes' ? ` ${s.unit}` : ''}`;
}

interface HoverCardProps {
  node?: TopologyNodeLive;
  phase?: string;
  snapshot: MetricSeries[] | null;
  x: number;
  y: number;
}

/** Compact "inspect without clicking" card: node identity, live state, and a few headline metrics. */
function NodeHoverCard({ node, phase, snapshot, x, y }: HoverCardProps) {
  const styles = useStyles2(getStyles);
  if (!node) {
    return null;
  }
  const status =
    phase === 'down' || node.state === 'red'
      ? 'Down'
      : phase === 'predicted' || node.state === 'amber'
      ? phase === 'predicted'
        ? 'Predicted fault'
        : 'Precursor'
      : 'Healthy';
  const color = node.state === 'red' ? stateColors.red : node.state === 'amber' ? stateColors.amber : stateColors.green;
  const rows: Array<[string, string]> = [
    ['Role', node.role],
    ['POP', node.pop ?? '—'],
    ...(node.siteType ? ([['Site', node.siteType]] as Array<[string, string]>) : []),
    ['Status', status],
  ];
  const metrics: Array<[string, string]> = [
    ['Predictor', latest(snapshot, 'predictor')],
    ['CPU', latest(snapshot, 'cpu_pct')],
    ['Memory', latest(snapshot, 'mem_pct')],
  ].filter(([, v]) => v !== '') as Array<[string, string]>;

  return (
    <div className={styles.card} style={{ left: x + 14, top: y + 14 }}>
      <div className={styles.cardHead}>
        <span className={styles.dot} style={{ background: color }} />
        <span className={styles.cardTitle}>{node.id}</span>
      </div>
      <table className={styles.cardTable}>
        <tbody>
          {[...rows, ...metrics].map(([k, v]) => (
            <tr key={k}>
              <td className={styles.cardKey}>{k}</td>
              <td className={styles.cardVal}>{v}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <span className={styles.cardHint}>{snapshot ? 'click to open' : 'loading…'}</span>
    </div>
  );
}

const getStyles = (theme: GrafanaTheme2) => ({
  toolbar: css`
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: ${theme.spacing(2)};
    margin: ${theme.spacing(2)} 0;
  `,
  legend: css`
    display: flex;
    gap: ${theme.spacing(2)};
    color: ${theme.colors.text.secondary};
    font-size: ${theme.typography.bodySmall.fontSize};
  `,
  legendItem: css`
    display: flex;
    align-items: center;
    gap: ${theme.spacing(0.5)};
  `,
  swatch: css`
    width: 10px;
    height: 10px;
    border-radius: 50%;
    display: inline-block;
  `,
  graphWrap: css`
    position: relative;
  `,
  card: css`
    position: absolute;
    z-index: 5;
    pointer-events: none;
    min-width: 180px;
    max-width: 240px;
    padding: ${theme.spacing(1)} ${theme.spacing(1.5)};
    background: ${theme.colors.background.secondary};
    border: 1px solid ${theme.colors.border.medium};
    border-radius: ${theme.shape.radius.default};
    box-shadow: ${theme.shadows.z2};
    font-size: ${theme.typography.bodySmall.fontSize};
  `,
  cardHead: css`
    display: flex;
    align-items: center;
    gap: ${theme.spacing(1)};
    margin-bottom: ${theme.spacing(0.5)};
  `,
  dot: css`
    width: 10px;
    height: 10px;
    border-radius: 50%;
    flex: none;
  `,
  cardTitle: css`
    font-family: ${theme.typography.fontFamilyMonospace};
    font-weight: ${theme.typography.fontWeightMedium};
  `,
  cardTable: css`
    width: 100%;
    border-collapse: collapse;
  `,
  cardKey: css`
    color: ${theme.colors.text.secondary};
    padding-right: ${theme.spacing(1)};
    white-space: nowrap;
  `,
  cardVal: css`
    text-align: right;
    font-family: ${theme.typography.fontFamilyMonospace};
  `,
  cardHint: css`
    display: block;
    margin-top: ${theme.spacing(0.5)};
    color: ${theme.colors.text.disabled};
    font-size: ${theme.typography.bodySmall.fontSize};
  `,
});
