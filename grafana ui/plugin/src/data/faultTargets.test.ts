import { roleOf, isValidTarget, targetOptions } from './faultTargets';
import type { TopologyNode } from './types';

const NODES: TopologyNode[] = [
  { id: 'p1', role: 'p', pop: 'pop1' },
  { id: 'p5', role: 'p', pop: 'pop2' },
  { id: 'pe1', role: 'pe', pop: 'pop1' },
  { id: 'pe2', role: 'pe', pop: 'pop1' },
  { id: 'ce_branch2', role: 'ce_branch', pop: 'pop2' },
  { id: 'ce_hub1', role: 'ce_hub', pop: 'pop1' },
  { id: 'host_b2', role: 'host', pop: 'pop2' },
];

describe('roleOf', () => {
  it('classifies ids by prefix, matching the backend (pe before p)', () => {
    expect(roleOf('pe1')).toBe('pe'); // must not fall through to 'p'
    expect(roleOf('p1')).toBe('p');
    expect(roleOf('ce_branch2')).toBe('ce_branch');
    expect(roleOf('ce_hub1')).toBe('ce_hub');
    expect(roleOf('ce_dc1')).toBe('ce_dc');
    expect(roleOf('pop1')).toBe('pop');
    expect(roleOf('srlg_link_7')).toBe('srlg');
    expect(roleOf('host_b2')).toBe('unknown');
  });
});

describe('isValidTarget', () => {
  it('passes on a matching coarse role, fails otherwise', () => {
    expect(isValidTarget('ce_branch2', ['ce_branch', 'ce_hub'])).toBe(true);
    expect(isValidTarget('pe1', ['ce_branch', 'ce_hub'])).toBe(false);
  });
});

describe('targetOptions', () => {
  it('filters topology nodes to the scenario roles (congestion: CE spokes)', () => {
    const opts = targetOptions(['ce_branch', 'ce_hub'], NODES);
    expect(opts.map((o) => o.value)).toEqual(['ce_branch2', 'ce_hub1']);
    expect(opts[0].label).toBe('ce_branch2 (ce_branch)');
  });

  it('synthesizes POP options from distinct derived node POPs (pop_isolation)', () => {
    const opts = targetOptions(['pop'], NODES);
    expect(opts.map((o) => o.value)).toEqual(['pop1', 'pop2']);
  });

  it('returns no concrete option for the srlg long-tail (custom entry only)', () => {
    expect(targetOptions(['srlg'], NODES)).toEqual([]);
  });

  it('unions device roles + POP synthesis and sorts numerically', () => {
    const opts = targetOptions(['pe', 'pop'], NODES);
    expect(opts.map((o) => o.value)).toEqual(['pe1', 'pe2', 'pop1', 'pop2']);
  });
});
