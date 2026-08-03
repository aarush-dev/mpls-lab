import { HttpDataClient } from './HttpDataClient';
import type { TopologyNodeLive } from './MockDataClient';

// Canned backend payloads. Faults use a live "now" so state derivation lands deterministically:
// r1 is inside its impact window (red), r2 is inside its pre-impact window (amber).
const nowMs = Date.now();
const nowS = Math.floor(nowMs / 1000);

const TOPOLOGY = {
  nodes: [
    { id: 'p1', role: 'p' },
    { id: 'r1', role: 'pe' },
    { id: 'r2', role: 'pe' },
  ],
  links: [
    { source: 'p1', target: 'r1', source_if: 'eth0', target_if: 'eth1' },
    { source: 'r1', target: 'ghost', source_if: 'eth2', target_if: 'eth0' }, // dangling -> dropped
  ],
};

const METRICS_RANGE = {
  result: [
    {
      metric: { __name__: 'node_cpu_pct', device: 'r1' },
      values: [
        [nowS - 60, '12.5'],
        [nowS - 30, 'NaN'],
        [nowS, '20'],
      ],
    },
  ],
};

const EVENTS = { rows: [{ ts: '2026-08-03T10:00:00.000Z', device: 'r1', app: 'bgpd', severity: 'warning', line: 'peer down' }] };
const FLOWS = { rows: [{ ts: '2026-08-03 10:00:00', device: 'r1', ip_src: '10.0.0.1', ip_dst: '10.0.0.2', port_src: 1234, port_dst: 179, proto: 'tcp', bytes: 100, packets: 2 }] };
const LABELS = {
  rows: [
    { scenario_id: 's-red', type: 'congestion', target: { device: 'r1' }, severity: 'high', t_start: nowS - 100, t_impact: nowS - 50, t_end: nowS + 50 },
    { scenario_id: 's-amber', type: 'flap', target: { device: 'r2' }, severity: 'low', t_start: nowS - 30, t_impact: nowS + 30, t_end: nowS + 90, lead_time: 60 },
  ],
};

function stubFetch(): jest.Mock {
  return jest.fn(async (url: string) => {
    const path = url.replace('http://x', '');
    const pick = (body: unknown): { ok: boolean; status?: number; json: () => Promise<unknown> } => ({
      ok: true,
      json: async () => body,
    });
    if (path.startsWith('/topology')) return pick(TOPOLOGY);
    if (path.startsWith('/metrics')) return pick(METRICS_RANGE);
    if (path.startsWith('/events')) return pick(EVENTS);
    if (path.startsWith('/flows')) return pick(FLOWS);
    if (path.startsWith('/labels')) return pick(LABELS);
    return { ok: false, status: 404, json: async () => ({ detail: 'not found' }) };
  }) as unknown as jest.Mock;
}

describe('HttpDataClient', () => {
  afterEach(() => {
    // @ts-expect-error test cleanup
    delete global.fetch;
  });

  it('getTopology maps DTOs, derives pop + live state, drops dangling links', async () => {
    global.fetch = stubFetch() as unknown as typeof fetch;
    const client = new HttpDataClient('http://x', 1000);
    const { nodes, links } = await client.getTopology({});
    expect(nodes).toHaveLength(3);
    const p1 = nodes.find((n) => n.id === 'p1')!;
    expect(p1.pop).toBe('pop1');
    const r1 = nodes.find((n) => n.id === 'r1') as TopologyNodeLive;
    const r2 = nodes.find((n) => n.id === 'r2') as TopologyNodeLive;
    expect(r1.state).toBe('red'); // inside impact window
    expect(r2.state).toBe('amber'); // inside pre-impact window
    expect(links).toHaveLength(1); // dangling link to 'ghost' dropped
    expect(links[0].kind).toBe('physical');
  });

  it('getTelemetry reshapes string values to numeric points, NaN -> null', async () => {
    global.fetch = stubFetch() as unknown as typeof fetch;
    const client = new HttpDataClient('http://x', 1000);
    const series = await client.getTelemetry({ deviceId: 'r1' });
    const cpu = series.find((s) => s.key === 'r1:node_cpu_pct')!;
    expect(cpu).toBeDefined();
    expect(cpu.source).toBe('measured');
    expect(cpu.points.map((p) => p.value)).toEqual([12.5, null, 20]);
    expect(cpu.points[0].tMs).toBe((nowS - 60) * 1000);
  });

  it('getTelemetry returns [] with no deviceId', async () => {
    global.fetch = stubFetch() as unknown as typeof fetch;
    const client = new HttpDataClient('http://x', 1000);
    expect(await client.getTelemetry({})).toEqual([]);
  });

  it('getEvents / getFlows map ts -> tsMs (flow ts parsed as UTC)', async () => {
    global.fetch = stubFetch() as unknown as typeof fetch;
    const client = new HttpDataClient('http://x', 1000);
    const events = await client.getEvents({});
    expect(events[0].tsMs).toBe(Date.parse('2026-08-03T10:00:00.000Z'));
    expect(events[0].line).toBe('peer down');
    const flows = await client.getFlows({});
    expect(flows[0].tsMs).toBe(Date.parse('2026-08-03T10:00:00Z'));
    expect(flows[0].ipSrc).toBe('10.0.0.1');
  });

  it('getIncidents / getPredictions derive from /labels', async () => {
    global.fetch = stubFetch() as unknown as typeof fetch;
    const client = new HttpDataClient('http://x', 1000);
    const incidents = await client.getIncidents({});
    expect(incidents.find((i) => i.id === 's-red')!.status).toBe('active');
    expect(incidents.find((i) => i.id === 's-red')!.source).toBe('ground_truth');
    const preds = await client.getPredictions({});
    const amber = preds.find((p) => p.id === 's-amber')!;
    expect(amber).toBeDefined();
    expect(amber.deviceId).toBe('r2');
    expect(amber.confidence).toBeGreaterThan(0);
    expect(amber.timeToImpactSeconds).toBeGreaterThan(0);
  });

  it('getCapabilities never throws when fetch rejects', async () => {
    global.fetch = jest.fn(async () => {
      throw new Error('network down');
    }) as unknown as typeof fetch;
    const client = new HttpDataClient('http://x', 1000);
    const caps = await client.getCapabilities();
    expect(caps.sources.measured).toBe(false);
    expect(caps.sources.ground_truth).toBe(true);
    expect(caps.datasetWindow.fromMs).toBeLessThan(caps.datasetWindow.toMs);
  });
});
