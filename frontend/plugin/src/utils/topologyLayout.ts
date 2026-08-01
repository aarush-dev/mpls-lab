// Deterministic topology layout: assign every node a fixed (x, y) so nodes never overlap and links
// stay mostly inside a pop cluster. Pure function of the node list — no physics, no Date/Math.random,
// so the map is stable across renders and demo ticks.
//
// Structure: pops laid out on a 3-wide grid of clusters; inside each cluster, nodes are stacked in
// role tiers (core p on top -> pe -> customer-edge -> host leaves) and evenly spaced across the tier.

import { TopologyNode } from '../data/types';

export interface Point {
  x: number;
  y: number;
}

const CLUSTER_W = 520; // cluster cell width (px in cytoscape model coords)
const CLUSTER_H = 460; // cluster cell height
const CLUSTER_COLS = 3; // pops per row -> 6 pops become a 3x2 grid
const TIER_GAP = 92; // vertical gap between role tiers
const TIER_TOP = 40; // top padding inside a cluster before tier 0
const ROW_WRAP_GAP = 34; // vertical gap between wrapped sub-rows within one wide tier
const MAX_PER_ROW = 12; // wrap a tier into sub-rows past this many nodes (keeps host row from crowding)

// role -> tier index (top = core, bottom = leaves). Unknown roles land on the edge tier.
const TIER_BY_ROLE: Record<string, number> = {
  p: 0,
  pe: 1,
  ce_hub: 2,
  ce_dc: 2,
  ce_branch: 2,
  host: 3,
};

function tierOf(role: string): number {
  return TIER_BY_ROLE[role] ?? 2;
}

// Sort key within a tier: hosts cluster under their parent CE, everything else by id. Deterministic.
function slotKey(n: TopologyNode): string {
  return n.role === 'host' ? `${n.parent ?? ''}|${n.id}` : n.id;
}

/**
 * Compute a fixed position per node. Guarantees no two nodes share a slot (each gets a distinct
 * column within its tier row), so there is zero node overlap by construction.
 */
export function computePositions(nodes: TopologyNode[]): Map<string, Point> {
  const pos = new Map<string, Point>();

  // Group by pop, deterministic pop order.
  const byPop = new Map<string, TopologyNode[]>();
  for (const n of nodes) {
    const pop = n.pop ?? '_';
    (byPop.get(pop) ?? byPop.set(pop, []).get(pop)!).push(n);
  }
  const pops = Array.from(byPop.keys()).sort();

  pops.forEach((pop, pi) => {
    const originX = (pi % CLUSTER_COLS) * CLUSTER_W;
    const originY = Math.floor(pi / CLUSTER_COLS) * CLUSTER_H;

    // Bucket this pop's nodes into role tiers.
    const tiers = new Map<number, TopologyNode[]>();
    for (const n of byPop.get(pop)!) {
      const t = tierOf(n.role);
      (tiers.get(t) ?? tiers.set(t, []).get(t)!).push(n);
    }

    for (const [tier, group] of tiers) {
      group.sort((a, b) => (slotKey(a) < slotKey(b) ? -1 : slotKey(a) > slotKey(b) ? 1 : 0));
      const rows = Math.ceil(group.length / MAX_PER_ROW);
      const perRow = Math.ceil(group.length / rows);
      const baseY = originY + TIER_TOP + tier * TIER_GAP;

      group.forEach((n, i) => {
        const row = Math.floor(i / perRow);
        const col = i % perRow;
        const countInRow = row === rows - 1 ? group.length - row * perRow : perRow;
        // Evenly spread across the cluster width; +1 spacing keeps nodes off the cluster edges.
        const x = originX + ((col + 1) / (countInRow + 1)) * CLUSTER_W;
        const y = baseY + row * ROW_WRAP_GAP;
        pos.set(n.id, { x, y });
      });
    }
  });

  return pos;
}
