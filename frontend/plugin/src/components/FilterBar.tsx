import React, { useEffect, useMemo, useState } from 'react';
import { css } from '@emotion/css';
import { GrafanaTheme2, SelectableValue } from '@grafana/data';
import { useStyles2, Select, Button } from '@grafana/ui';

import { useAppState, useAppDispatch } from '../state/AppContext';
import { useDataClient } from '../data/DataClientContext';
import { TopologyNode } from '../data/types';

// Global operator filters. Only pop + device are exposed because those are the keys the DataClient
// actually honors (MockDataClient) — no dead controls. Options derived once from the topology.
export function FilterBar() {
  const styles = useStyles2(getStyles);
  const { filters } = useAppState();
  const dispatch = useAppDispatch();
  const dataClient = useDataClient();

  const [nodes, setNodes] = useState<TopologyNode[]>([]);
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

  const popOptions = useMemo<Array<SelectableValue<string>>>(
    () =>
      Array.from(new Set(nodes.map((n) => n.pop).filter(Boolean) as string[]))
        .sort()
        .map((p) => ({ label: p, value: p })),
    [nodes]
  );
  const deviceOptions = useMemo<Array<SelectableValue<string>>>(
    () =>
      nodes
        .map((n) => n.id)
        .sort()
        .map((d) => ({ label: d, value: d })),
    [nodes]
  );

  const set = (key: 'pop' | 'device', v?: string) => dispatch({ type: 'SET_FILTER', payload: { key, value: v } });
  const hasFilters = Boolean(filters.pop || filters.device);

  return (
    <div className={styles.bar}>
      <Select
        width={20}
        placeholder="POP"
        isClearable
        options={popOptions}
        value={filters.pop ?? null}
        onChange={(v: SelectableValue<string>) => set('pop', v?.value)}
      />
      <Select
        width={28}
        placeholder="Device"
        isClearable
        options={deviceOptions}
        value={filters.device ?? null}
        onChange={(v: SelectableValue<string>) => set('device', v?.value)}
      />
      {hasFilters && (
        <Button variant="secondary" size="sm" onClick={() => dispatch({ type: 'CLEAR_FILTERS' })}>
          Clear
        </Button>
      )}
    </div>
  );
}

const getStyles = (theme: GrafanaTheme2) => ({
  bar: css`
    display: flex;
    align-items: center;
    gap: ${theme.spacing(1)};
  `,
});
