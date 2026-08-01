import { computePositions } from './topologyLayout';
import { TopologyNode } from '../data/types';
import topology from '../fixtures/topology.json';

const nodes = (topology as { nodes: TopologyNode[] }).nodes;

describe('computePositions', () => {
  it('gives every node a distinct slot (zero overlap)', () => {
    const pos = computePositions(nodes);
    expect(pos.size).toBe(nodes.length);
    const seen = new Set<string>();
    for (const { x, y } of pos.values()) {
      const key = `${x.toFixed(2)},${y.toFixed(2)}`;
      expect(seen.has(key)).toBe(false);
      seen.add(key);
    }
  });

  it('stacks role tiers top-down within a pop (p < pe < ce < host)', () => {
    const pos = computePositions(nodes);
    const yOf = (id: string) => pos.get(id)!.y;
    // pop1 has p1, pe*, ce_branch1, hosts.
    const p = yOf('p1');
    const pe = yOf(nodes.find((n) => n.role === 'pe' && n.pop === 'pop1')!.id);
    const ce = yOf('ce_branch1');
    const host = yOf(nodes.find((n) => n.role === 'host' && n.pop === 'pop1')!.id);
    expect(p).toBeLessThan(pe);
    expect(pe).toBeLessThan(ce);
    expect(ce).toBeLessThan(host);
  });

  it('is deterministic — two calls produce identical positions', () => {
    const a = computePositions(nodes);
    const b = computePositions(nodes);
    for (const [id, p] of a) {
      expect(b.get(id)).toEqual(p);
    }
  });
});
