import React, { useEffect, useRef, useState } from 'react';
import cytoscape, { Core, ElementDefinition } from 'cytoscape';
import { css } from '@emotion/css';
import { GrafanaTheme2 } from '@grafana/data';
import { useStyles2, RadioButtonGroup } from '@grafana/ui';

import { TopologyLink } from '../data/types';
import { TopologyNodeLive } from '../data/MockDataClient';
import { styleForRole, colorForState } from '../data/topologyStyles';
import { computePositions, Point } from '../utils/topologyLayout';

type LayoutMode = 'grouped' | 'auto';
const LAYOUT_OPTIONS: Array<{ label: string; value: LayoutMode }> = [
  { label: 'Grouped', value: 'grouped' },
  { label: 'Auto', value: 'auto' },
];

interface Props {
  nodes: TopologyNodeLive[];
  links: TopologyLink[];
  onSelectNode: (id: string) => void;
  /** Hover a device -> id + rendered position (relative to the graph container); null on mouse-out. */
  onHoverNode?: (id: string | null, pos: { x: number; y: number } | null) => void;
}

const POP_PREFIX = 'pop::';

function edgeId(l: TopologyLink, i: number): string {
  return `${l.source}->${l.target}::${i}`;
}

function buildElements(nodes: TopologyNodeLive[], links: TopologyLink[], positions: Map<string, Point>): ElementDefinition[] {
  const pops = new Set<string>();
  nodes.forEach((n) => n.pop && pops.add(n.pop));

  const popNodes: ElementDefinition[] = Array.from(pops).map((pop) => ({
    data: { id: POP_PREFIX + pop, label: pop, isPop: true },
  }));

  const realNodes: ElementDefinition[] = nodes.map((n) => {
    const style = styleForRole(n.role);
    return {
      data: {
        id: n.id,
        label: n.id,
        role: n.role,
        state: n.state ?? '',
        shape: style.shape,
        baseColor: style.color,
        size: style.size,
        parent: n.pop ? POP_PREFIX + n.pop : undefined,
      },
      position: positions.get(n.id),
    };
  });

  const edges: ElementDefinition[] = links.map((l, i) => ({
    data: {
      id: edgeId(l, i),
      source: l.source,
      target: l.target,
      kind: l.kind ?? 'physical',
    },
  }));

  return [...popNodes, ...realNodes, ...edges];
}

function nodeSetKey(nodes: TopologyNodeLive[], links: TopologyLink[]): string {
  return `${nodes.map((n) => n.id).join(',')}|${links.map((l) => `${l.source}-${l.target}`).join(',')}`;
}

// Grouped = tidy preset positions (computePositions); auto = Cytoscape's built-in cose (force-directed
// crossing-minimizer, in core — no extra dependency), seeded non-random from the current positions so
// it's stable/repeatable. Preset restores the grouped positions explicitly, so a toggle round-trips.
function applyLayout(cy: Core, mode: LayoutMode, positions: Map<string, Point>) {
  if (cy.elements().length === 0) {
    return;
  }
  if (mode === 'auto') {
    cy.layout({ name: 'cose', randomize: false, animate: false, fit: true } as cytoscape.LayoutOptions).run();
    cy.fit(undefined, 30);
    return;
  }
  // Grouped: snap every real node straight back to its computed slot. cose physically moves nodes (and
  // the compound pop parents), and the `preset` layout's positions-function doesn't reliably restore
  // that on a round-trip — so set positions directly. Pop parents re-fit around their children.
  cy.batch(() => {
    positions.forEach((pt, id) => {
      const el = cy.getElementById(id);
      if (el.nonempty()) {
        el.position({ x: pt.x, y: pt.y });
      }
    });
  });
  cy.fit(undefined, 30);
}

export function TopologyGraph({ nodes, links, onSelectNode, onHoverNode }: Props) {
  const styles = useStyles2(getStyles);
  const containerRef = useRef<HTMLDivElement>(null);
  const cyRef = useRef<Core | null>(null);
  const setKeyRef = useRef<string>('');
  const positionsRef = useRef<Map<string, Point>>(new Map());
  const [layoutMode, setLayoutMode] = useState<LayoutMode>('grouped');
  const layoutModeRef = useRef(layoutMode);
  layoutModeRef.current = layoutMode;
  const onSelectRef = useRef(onSelectNode);
  onSelectRef.current = onSelectNode;
  const onHoverRef = useRef(onHoverNode);
  onHoverRef.current = onHoverNode;

  // Init cytoscape once.
  useEffect(() => {
    if (!containerRef.current) {
      return undefined;
    }
    const cy = cytoscape({
      container: containerRef.current,
      elements: [],
      style: [
        {
          selector: 'node[?isPop]',
          style: {
            'background-color': '#2c3235',
            'background-opacity': 0.35,
            shape: 'round-rectangle',
            label: 'data(label)',
            'text-valign': 'top',
            'font-size': 10,
            color: '#8e9297',
          },
        },
        {
          selector: 'node[!isPop]',
          style: {
            // cytoscape supports data() mappers for shape at runtime; its TS types don't model it.
            shape: 'data(shape)' as cytoscape.Css.Node['shape'],
            width: 'data(size)',
            height: 'data(size)',
            'background-color': 'data(baseColor)',
            label: 'data(label)',
            'font-size': 8,
            color: '#c7d0d9',
            'text-valign': 'bottom',
            'text-margin-y': 4,
          },
        },
        {
          selector: 'edge',
          style: {
            width: 1.5,
            'line-color': '#5c6370',
            'target-arrow-shape': 'none',
            'curve-style': 'bezier',
          },
        },
        {
          // Tunnels are overlay links (~171 of them) — keep them faint so they don't tangle the map.
          // Revealed on node hover via the edge-hl class below.
          selector: 'edge[kind = "tunnel"]',
          style: { 'line-style': 'dashed', 'line-opacity': 0.12, width: 1 },
        },
        {
          // Edges incident to a hovered node: fully visible, on top.
          selector: 'edge.edge-hl',
          style: { 'line-opacity': 1, width: 2.5, 'z-index': 10 },
        },
        {
          selector: 'node[state = "red"]',
          style: { 'background-color': colorForState('red') },
        },
        {
          selector: 'node[state = "amber"]',
          style: { 'background-color': colorForState('amber') },
        },
        {
          selector: 'node[state = "green"]',
          style: { 'background-color': colorForState('green') },
        },
        {
          selector: 'edge.state-red',
          style: { 'line-color': colorForState('red') },
        },
        {
          selector: 'edge.state-amber',
          style: { 'line-color': colorForState('amber') },
        },
      ],
      layout: { name: 'grid' },
    });

    cy.on('tap', 'node', (evt) => {
      const target = evt.target;
      if (!target.data('isPop')) {
        onSelectRef.current(target.id());
      }
    });

    // Hover a device -> light up its links (incl. otherwise-faint tunnels) + surface a mini card
    // (via onHoverNode) so it can be inspected without navigating. Clear on mouseout.
    cy.on('mouseover', 'node[!isPop]', (evt) => {
      evt.target.connectedEdges().addClass('edge-hl');
      const rp = evt.target.renderedPosition();
      onHoverRef.current?.(evt.target.id(), { x: rp.x, y: rp.y });
    });
    cy.on('mouseout', 'node[!isPop]', (evt) => {
      evt.target.connectedEdges().removeClass('edge-hl');
      onHoverRef.current?.(null, null);
    });

    cyRef.current = cy;
    return () => {
      cy.destroy();
      cyRef.current = null;
    };
  }, []);

  // Diff/replace elements + recolor, without re-layout unless the node/link set changed.
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) {
      return;
    }
    const key = nodeSetKey(nodes, links);
    const setChanged = key !== setKeyRef.current;

    if (setChanged) {
      cy.elements().remove();
      positionsRef.current = computePositions(nodes);
      cy.add(buildElements(nodes, links, positionsRef.current));
      applyLayout(cy, layoutModeRef.current, positionsRef.current);
      setKeyRef.current = key;
    } else {
      // Same topology shape: just recolor nodes/edges for the new cursor tick.
      const stateById = new Map(nodes.map((n) => [n.id, n.state]));
      cy.nodes('[!isPop]').forEach((n) => {
        n.data('state', stateById.get(n.id()) ?? '');
      });
      cy.edges().forEach((e) => {
        const sState = stateById.get(e.data('source'));
        const tState = stateById.get(e.data('target'));
        e.removeClass('state-red state-amber');
        if (sState === 'red' || tState === 'red') {
          e.addClass('state-red');
        } else if (sState === 'amber' || tState === 'amber') {
          e.addClass('state-amber');
        }
      });
    }
  }, [nodes, links]);

  // Re-run the chosen layout when the toggle flips (element set unchanged).
  useEffect(() => {
    const cy = cyRef.current;
    if (cy) {
      applyLayout(cy, layoutMode, positionsRef.current);
    }
  }, [layoutMode]);

  return (
    <div className={styles.wrap}>
      <div className={styles.toggle}>
        <RadioButtonGroup
          size="sm"
          options={LAYOUT_OPTIONS}
          value={layoutMode}
          onChange={(v) => v && setLayoutMode(v)}
        />
      </div>
      <div ref={containerRef} className={styles.container} />
    </div>
  );
}

const getStyles = (theme: GrafanaTheme2) => ({
  wrap: css`
    position: relative;
  `,
  toggle: css`
    position: absolute;
    top: ${theme.spacing(1)};
    right: ${theme.spacing(1)};
    z-index: 1;
  `,
  container: css`
    width: 100%;
    min-height: 560px;
    height: 560px;
    background: ${theme.colors.background.primary};
    border: 1px solid ${theme.colors.border.weak};
    border-radius: ${theme.shape.radius.default};
  `,
});
