import cytoscape from 'cytoscape';
import topo from '../fixtures/topology.json';
import { computePositions } from '../utils/topologyLayout';
import { TopologyNodeLive } from '../data/MockDataClient';
import { TopologyLink } from '../data/types';
import { buildElements, applyLayout, POP_PREFIX } from './TopologyGraph';

// Regression: Cytoscape's built-in `cose` corrupts a COMPOUND graph to NaN positions. Auto mode must
// detach the pop parents before running cose, and Grouped must re-parent + restore exact slots so the
// toggle round-trips. If someone drops the detach, `anyNaN` flips true and this fails.
test('Auto (cose) then Grouped round-trips: no NaN, exact restore', () => {
  const nodes: TopologyNodeLive[] = (topo.nodes as Array<{ id: string; role: string; pop?: string; parent?: string }>).map(
    (n) => ({ id: n.id, role: n.role, pop: n.pop, parent: n.parent, state: 'green' })
  );
  const links: TopologyLink[] = (topo.links as Array<{ source: string; target: string; kind?: string }>).map((l) => ({
    source: l.source,
    target: l.target,
    kind: l.kind as TopologyLink['kind'],
  }));
  const positions = computePositions(nodes);
  const parentById = new Map(nodes.filter((n) => n.pop).map((n) => [n.id, POP_PREFIX + n.pop]));

  const cy = cytoscape({ headless: true, elements: buildElements(nodes, links, positions), styleEnabled: false });

  applyLayout(cy, 'auto', positions, parentById);
  const anyNaN = cy
    .nodes('[!isPop]')
    .toArray()
    .some((n) => {
      const p = n.position();
      return Number.isNaN(p.x) || Number.isNaN(p.y);
    });
  expect(anyNaN).toBe(false);

  applyLayout(cy, 'grouped', positions, parentById);
  let drift = 0;
  for (const n of nodes) {
    const p = cy.getElementById(n.id).position();
    const want = positions.get(n.id)!;
    drift = Math.max(drift, Math.abs(p.x - want.x), Math.abs(p.y - want.y));
  }
  expect(drift).toBeLessThan(0.01);
});
