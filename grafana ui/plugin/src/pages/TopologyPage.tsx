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
import { nodeDetailPath } from '../constants';
import { stateColors, roleStyles } from '../data/topologyStyles';
import { fetchPaAlerts, precursorDevices, PA_POLL_MS } from '../data/paAlerts';

// Role/shape legend — `role` keys into `roleStyles` for the color; `shape` names the swatch style so
// the swatch matches the on-map node shape. CE hub/dc/branch all render as hexagons -> one entry.
const ROLE_LEGEND: Array<{ role: keyof typeof roleStyles; label: string; shape: 'diamond' | 'roundRect' | 'hexagon' | 'ellipse' }> = [
  { role: 'p', label: 'P · core', shape: 'diamond' },
  { role: 'pe', label: 'PE · provider edge', shape: 'roundRect' },
  { role: 'ce_hub', label: 'CE · customer edge', shape: 'hexagon' },
  { role: 'host', label: 'Host', shape: 'ellipse' },
];

export function TopologyPage() {
  const styles = useStyles2(getStyles);
  const history = useHistory();
  const { range, filters } = useAppState();
  const dataClient = useDataClient();

  const [nodes, setNodes] = useState<TopologyNodeLive[]>([]);
  const [links, setLinks] = useState<TopologyLink[]>([]);
  const [status, setStatus] = useState<'loading' | 'ready' | 'error'>('loading');
  const [search, setSearch] = useState('');
  const [reloadToken, setReloadToken] = useState(0);
  const [hover, setHover] = useState<{ id: string; x: number; y: number } | null>(null);
  const [snapshot, setSnapshot] = useState<MetricSeries[] | null>(null);
  const [precursorIds, setPrecursorIds] = useState<Set<string>>(new Set());

  // ponytail: refetch on hovered node change only, not every refreshTick.
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
  }, [dataClient, hoverId]);

  // ponytail: graph structure is static — refetch on filter change / mount / manual reload only,
  // not on every refreshTick.
  useEffect(() => {
    let cancelled = false;
    dataClient
      .getTopology(filters)
      .then((graph) => {
        if (cancelled) {
          return;
        }
        setNodes(graph.nodes as TopologyNodeLive[]);
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
  }, [dataClient, filters, reloadToken]);

  // Poll the live PA pipeline for currently-flagged devices (the blink set). Keep the last-good set
  // on a transient fetch failure so a single blip doesn't clear the blink.
  useEffect(() => {
    let alive = true;
    const tick = async () => {
      try {
        const resp = await fetchPaAlerts();
        if (alive) {
          setPrecursorIds(precursorDevices(resp));
        }
      } catch {
        /* PA service down/unreachable — keep last set */
      }
    };
    tick();
    const id = setInterval(tick, PA_POLL_MS);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);

  // Search highlights rather than filters: matching ids get a white outline + glow in the graph;
  // every node/link stays rendered. Empty search -> empty set -> nothing highlighted.
  const matchIds = useMemo(() => {
    const term = search.trim().toLowerCase();
    if (!term) {
      return new Set<string>();
    }
    return new Set(nodes.filter((n) => n.id.toLowerCase().includes(term)).map((n) => n.id));
  }, [nodes, search]);

  const matchCount = matchIds.size;

  return (
    <PluginPage>
      <div className={styles.header}>
        <h1 className={styles.h1}>Topology</h1>
        <div className={styles.summary}>
          <span>{nodes.length} nodes</span>
          {precursorIds.size > 0 && (
            <span className={styles.precursorCount}>
              {precursorIds.size} PA precursor{precursorIds.size > 1 ? 's' : ''}
            </span>
          )}
          {search.trim() && (
            <span className={styles.matchCount}>
              {matchCount} match{matchCount === 1 ? '' : 'es'}
            </span>
          )}
        </div>
      </div>

      <div className={styles.toolbar}>
        <Input
          placeholder="Search node id…"
          value={search}
          onChange={(e) => setSearch(e.currentTarget.value)}
          width={40}
        />
        <div className={styles.legends}>
          <div className={styles.legend}>
            <span className={styles.legendLabel}>State</span>
            <span className={styles.legendItem}>
              <span className={styles.dotSwatch} style={{ background: stateColors.green }} /> Healthy
            </span>
            <span className={styles.legendItem}>
              <span className={styles.dotSwatch} style={{ background: stateColors.amber }} /> Precursor
            </span>
            <span className={styles.legendItem}>
              <span className={styles.dotSwatch} style={{ background: stateColors.yellow }} /> Stressed
            </span>
            <span className={styles.legendItem}>
              <span className={styles.dotSwatch} style={{ background: stateColors.red }} /> Down
            </span>
          </div>
          <div className={styles.legend}>
            <span className={styles.legendLabel}>Roles</span>
            {ROLE_LEGEND.map((r) => (
              <span key={r.label} className={styles.legendItem}>
                <span className={styles[r.shape]} style={{ background: roleStyles[r.role].color }} /> {r.label}
              </span>
            ))}
          </div>
        </div>
      </div>

      {status === 'loading' && <EmptyState message="Loading topology…" />}
      {status === 'error' && <ErrorState onRetry={() => setReloadToken((t) => t + 1)} />}
      {status === 'ready' && nodes.length === 0 && <EmptyState />}
      {status === 'ready' && nodes.length > 0 && (
        <div className={styles.graphCard}>
          <div className={styles.graphWrap}>
            <TopologyGraph
              nodes={nodes}
              links={links}
              precursorIds={precursorIds}
              matchIds={matchIds}
              onSelectNode={(id) => history.push(nodeDetailPath(id))}
              onHoverNode={(id, pos) => setHover(id && pos ? { id, x: pos.x, y: pos.y } : null)}
            />
            {hover && (
              <NodeHoverCard
                node={nodes.find((n) => n.id === hover.id)}
                snapshot={snapshot}
                x={hover.x}
                y={hover.y}
              />
            )}
          </div>
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
  snapshot: MetricSeries[] | null;
  x: number;
  y: number;
}

/** Compact "inspect without clicking" card: node identity, live state, and a few headline metrics. */
function NodeHoverCard({ node, snapshot, x, y }: HoverCardProps) {
  const styles = useStyles2(getStyles);
  if (!node) {
    return null;
  }
  const status =
    node.state === 'red' ? 'Down' : node.state === 'amber' ? 'Precursor' : node.state === 'yellow' ? 'Stressed' : 'Healthy';
  const color =
    node.state === 'red'
      ? stateColors.red
      : node.state === 'amber'
      ? stateColors.amber
      : node.state === 'yellow'
      ? stateColors.yellow
      : stateColors.green;
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
  header: css`
    display: flex;
    align-items: baseline;
    gap: ${theme.spacing(2)};
    flex-wrap: wrap;
  `,
  h1: css`
    margin: 0;
  `,
  summary: css`
    display: flex;
    gap: ${theme.spacing(1.5)};
    color: ${theme.colors.text.secondary};
    font-size: ${theme.typography.bodySmall.fontSize};
  `,
  precursorCount: css`
    color: ${stateColors.amber};
    font-weight: ${theme.typography.fontWeightMedium};
  `,
  matchCount: css`
    color: ${theme.colors.text.primary};
  `,
  toolbar: css`
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: ${theme.spacing(2)};
    margin: ${theme.spacing(2)} 0;
    flex-wrap: wrap;
  `,
  legends: css`
    display: flex;
    gap: ${theme.spacing(3)};
    flex-wrap: wrap;
  `,
  legend: css`
    display: flex;
    align-items: center;
    gap: ${theme.spacing(2)};
    color: ${theme.colors.text.secondary};
    font-size: ${theme.typography.bodySmall.fontSize};
  `,
  legendLabel: css`
    text-transform: uppercase;
    letter-spacing: 0.4px;
    font-size: 10px;
    color: ${theme.colors.text.disabled};
  `,
  legendItem: css`
    display: flex;
    align-items: center;
    gap: ${theme.spacing(0.5)};
  `,
  dotSwatch: css`
    width: 10px;
    height: 10px;
    border-radius: 50%;
    display: inline-block;
  `,
  // Role swatches mirror the on-map cytoscape node shapes.
  diamond: css`
    width: 11px;
    height: 11px;
    display: inline-block;
    clip-path: polygon(50% 0%, 100% 50%, 50% 100%, 0% 50%);
  `,
  roundRect: css`
    width: 13px;
    height: 9px;
    display: inline-block;
    border-radius: 2px;
  `,
  hexagon: css`
    width: 12px;
    height: 11px;
    display: inline-block;
    clip-path: polygon(25% 0%, 75% 0%, 100% 50%, 75% 100%, 25% 100%, 0% 50%);
  `,
  ellipse: css`
    width: 11px;
    height: 11px;
    display: inline-block;
    border-radius: 50%;
  `,
  graphCard: css`
    background: ${theme.colors.background.secondary};
    border: 1px solid ${theme.colors.border.weak};
    border-radius: ${theme.shape.radius.default};
    box-shadow: ${theme.shadows.z1};
    padding: ${theme.spacing(1)};
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
