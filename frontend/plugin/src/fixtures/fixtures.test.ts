import meta from './meta.json';
import topology from './topology.json';
import incidents from './incidents.json';
import predictions from './predictions.json';
import telemetry from './telemetry.json';
import nodeStates from './nodeStates.json';
import conversations from './conversations.json';

describe('meta.json', () => {
  it('has the expected bucket contract', () => {
    expect(meta.bucketCount).toBe(152);
    expect(meta.windowBuckets).toBe(50);
    expect(meta.buckets).toHaveLength(152);
  });
});

describe('topology.json', () => {
  it('has 148 nodes and 361 links', () => {
    expect(topology.nodes).toHaveLength(148);
    expect(topology.links).toHaveLength(361);
  });
});

describe('incidents.json', () => {
  it('has 28 incidents covering all 21 fault types', () => {
    expect(incidents).toHaveLength(28);
    const faultTypes = new Set(incidents.map((i: any) => i.faultType));
    expect(faultTypes.size).toBe(21);
  });

  it('every incident faultType has a matching conv-seed-<faultType> conversation', () => {
    const seedIds = new Set(conversations.map((c: any) => c.id));
    const faultTypes = new Set(incidents.map((i: any) => i.faultType));
    for (const faultType of faultTypes) {
      expect(seedIds.has(`conv-seed-${faultType}`)).toBe(true);
    }
  });
});

describe('predictions.json', () => {
  it('has 69 predictions, all mock-sourced', () => {
    expect(predictions).toHaveLength(69);
    for (const p of predictions as any[]) {
      expect(p.source).toBe('mock');
    }
  });
});

describe('telemetry.json', () => {
  it('has 27 device keys', () => {
    expect(Object.keys(telemetry)).toHaveLength(27);
  });
});

describe('nodeStates.json', () => {
  it('every key parses to an int bucket index in [0, 152)', () => {
    for (const key of Object.keys(nodeStates)) {
      const n = Number(key);
      expect(Number.isInteger(n)).toBe(true);
      expect(n).toBeGreaterThanOrEqual(0);
      expect(n).toBeLessThan(152);
    }
  });
});
