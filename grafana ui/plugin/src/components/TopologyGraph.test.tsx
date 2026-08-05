import cytoscape from 'cytoscape';
import topo from '../fixtures/topology.json';
import { computePositions } from '../utils/topologyLayout';
import { TopologyNodeLive, TopologyLink } from '../data/types';
import { buildElements, applyLayout } from './TopologyGraph';

// Grouped layout: applyLayout must place every node at its computed slot (cy.add alone does not reliably
// position compound children, so applyLayout's explicit el.position() is what tiers the graph).
test('applyLayout places every node at its computed slot', () => {
  const nodes: TopologyNodeLive[] = (topo.nodes as Array<{ id: string; role: string; pop?: string; parent?: string }>).map(
    (n) => ({ id: n.id, role: n.role, pop: n.pop, parent: n.parent, state: 'green' })
  );
  const links: TopologyLink[] = (topo.links as Array<{ source: string; target: string; kind?: string }>).map((l) => ({
    source: l.source,
    target: l.target,
    kind: l.kind as TopologyLink['kind'],
  }));
  const positions = computePositions(nodes);

  const cy = cytoscape({ headless: true, elements: buildElements(nodes, links, positions), styleEnabled: false });
  applyLayout(cy, positions);

  let drift = 0;
  let anyNaN = false;
  for (const n of nodes) {
    const p = cy.getElementById(n.id).position();
    if (Number.isNaN(p.x) || Number.isNaN(p.y)) {
      anyNaN = true;
    }
    const want = positions.get(n.id)!;
    drift = Math.max(drift, Math.abs(p.x - want.x), Math.abs(p.y - want.y));
  }
  expect(anyNaN).toBe(false);
  expect(drift).toBeLessThan(0.01);
});
