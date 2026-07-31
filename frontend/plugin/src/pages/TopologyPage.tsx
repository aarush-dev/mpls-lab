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
import { TopologyLink } from '../data/types';
import { TopologyNodeLive } from '../data/MockDataClient';
import { nodeDetailPath } from '../constants';
import { stateColors } from '../data/topologyStyles';

export function TopologyPage() {
  const styles = useStyles2(getStyles);
  const history = useHistory();
  const { cursor, filters } = useAppState();
  const dataClient = useDataClient();

  const [nodes, setNodes] = useState<TopologyNodeLive[]>([]);
  const [links, setLinks] = useState<TopologyLink[]>([]);
  const [status, setStatus] = useState<'loading' | 'ready' | 'error'>('loading');
  const [search, setSearch] = useState('');
  const [reloadToken, setReloadToken] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setStatus('loading');

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
          setStatus('error');
        }
      });

    return () => {
      cancelled = true;
    };
  }, [dataClient, cursor, filters, reloadToken]);

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
        <TopologyGraph
          nodes={filteredNodes}
          links={filteredLinks}
          onSelectNode={(id) => history.push(nodeDetailPath(id))}
        />
      )}
    </PluginPage>
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
});
