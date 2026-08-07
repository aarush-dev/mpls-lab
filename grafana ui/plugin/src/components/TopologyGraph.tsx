import React, { useEffect, useRef } from 'react';
import cytoscape, { Core, ElementDefinition } from 'cytoscape';
import { css } from '@emotion/css';
import { GrafanaTheme2 } from '@grafana/data';
import { useStyles2 } from '@grafana/ui';

import { TopologyLink, TopologyNodeLive } from '../data/types';
import { styleForRole, colorForState, BLINK_BLUE_A, BLINK_BLUE_B } from '../data/topologyStyles';
import { computePositions, Point } from '../utils/topologyLayout';

interface Props {
  nodes: TopologyNodeLive[];
  links: TopologyLink[];
  onSelectNode: (id: string) => void;
  /** Hover a device -> id + rendered position (relative to the graph container); null on mouse-out. */
  onHoverNode?: (id: string | null, pos: { x: number; y: number } | null) => void;
  /** Device ids the live PA pipeline currently flags — these nodes fade-blink blue. */
  precursorIds?: Set<string>;
  /** Device ids matching the search box — highlighted (white outline + glow), NOT filtered out. */
  matchIds?: Set<string>;
}

export const POP_PREFIX = 'pop::';

function edgeId(l: TopologyLink, i: number): string {
  return `${l.source}->${l.target}::${i}`;
}

export function buildElements(nodes: TopologyNodeLive[], links: TopologyLink[], positions: Map<string, Point>): ElementDefinition[] {
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

// Grouped preset: place every node at its computed slot. buildElements bakes positions into the element
// defs, but cy.add does not reliably honor them for compound (pop-parented) children, so set them
// explicitly here — this is what tiers the graph.
export function applyLayout(cy: Core, positions: Map<string, Point>) {
  if (cy.elements().length === 0) {
    return;
  }
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

export function TopologyGraph({ nodes, links, onSelectNode, onHoverNode, precursorIds, matchIds }: Props) {
  const styles = useStyles2(getStyles);
  const containerRef = useRef<HTMLDivElement>(null);
  const cyRef = useRef<Core | null>(null);
  const setKeyRef = useRef<string>('');
  const positionsRef = useRef<Map<string, Point>>(new Map());
  // Ids currently running a blink animation, so the effect only starts/stops the delta.
  const blinkingRef = useRef<Set<string>>(new Set());
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
          selector: 'node[state = "yellow"]',
          style: { 'background-color': colorForState('yellow') },
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
        {
          selector: 'edge.state-yellow',
          style: { 'line-color': colorForState('yellow') },
        },
        {
          // Search matches: white outline + a soft white halo (overlay = the "glow"). Other nodes
          // stay fully visible — search highlights, it does not filter.
          selector: 'node.search-hl',
          style: {
            'border-width': 3,
            'border-color': '#ffffff',
            'border-opacity': 1,
            'overlay-color': '#ffffff',
            'overlay-opacity': 0.25,
            'overlay-padding': 8,
          },
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
      // Elements are recreated -> any in-flight blink animations are now dead; forget them so the
      // blink effect (which also depends on `nodes`) restarts them on the fresh elements.
      blinkingRef.current.clear();
      positionsRef.current = computePositions(nodes);
      cy.add(buildElements(nodes, links, positionsRef.current));
      applyLayout(cy, positionsRef.current);
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
        e.removeClass('state-red state-amber state-yellow');
        if (sState === 'red' || tState === 'red') {
          e.addClass('state-red');
        } else if (sState === 'amber' || tState === 'amber') {
          e.addClass('state-amber');
        } else if (sState === 'yellow' || tState === 'yellow') {
          e.addClass('state-yellow');
        }
      });
    }
  }, [nodes, links]);

  // Search highlight: toggle the search-hl class so matches get a white outline + glow. Pure class
  // flip — no element add/remove, so the graph never relayouts on a keystroke.
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) {
      return;
    }
    const match = matchIds ?? new Set<string>();
    cy.batch(() => {
      cy.nodes('[!isPop]').forEach((n) => {
        if (match.has(n.id())) {
          n.addClass('search-hl');
        } else {
          n.removeClass('search-hl');
        }
      });
    });
  }, [matchIds, nodes]);

  // PA-precursor blink: fade flagged nodes between two blue shades via chained cytoscape animations.
  // Only start/stop the delta vs what's already blinking; on stop, revert to the state/base color.
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) {
      return;
    }
    const want = precursorIds ?? new Set<string>();
    const running = blinkingRef.current;

    // Deterministic flip between the two blue shades (don't read back computed style — cytoscape
    // returns it as rgb(), which never equals the hex constant, so a readback toggle would stick).
    const step = (id: string, toB: boolean) => {
      const ele = cy.getElementById(id);
      if (ele.empty() || !running.has(id)) {
        return;
      }
      ele.animate(
        { style: { 'background-color': toB ? BLINK_BLUE_B : BLINK_BLUE_A } },
        { duration: 650, easing: 'ease-in-out-sine', complete: () => step(id, !toB) }
      );
    };

    // stop cleared ids -> revert to stylesheet-driven color
    running.forEach((id) => {
      if (!want.has(id)) {
        running.delete(id);
        const ele = cy.getElementById(id);
        if (ele.nonempty()) {
          ele.stop();
          ele.removeStyle('background-color');
        }
      }
    });
    // start newly-flagged ids: seed at shade A, then fade toward B (loops from there)
    want.forEach((id) => {
      const ele = cy.getElementById(id);
      if (!running.has(id) && ele.nonempty()) {
        running.add(id);
        ele.style('background-color', BLINK_BLUE_A);
        step(id, true);
      }
    });
  }, [precursorIds, nodes]);

  return <div ref={containerRef} className={styles.container} />;
}

const getStyles = (theme: GrafanaTheme2) => ({
  container: css`
    width: 100%;
    min-height: 560px;
    height: 560px;
    background: ${theme.colors.background.primary};
    border: 1px solid ${theme.colors.border.weak};
    border-radius: ${theme.shape.radius.default};
  `,
});
