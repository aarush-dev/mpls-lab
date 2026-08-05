import { HttpDataClient } from './HttpDataClient';
import type { ChatEvent, TopologyNodeLive } from './types';

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
    if (path.startsWith('/topology')) {return pick(TOPOLOGY);}
    if (path.startsWith('/metrics')) {return pick(METRICS_RANGE);}
    if (path.startsWith('/events')) {return pick(EVENTS);}
    if (path.startsWith('/flows')) {return pick(FLOWS);}
    if (path.startsWith('/labels')) {return pick(LABELS);}
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

// jsdom lacks TextEncoder/TextDecoder, which the SSE reader needs.
(global as any).TextEncoder = (global as any).TextEncoder ?? require('util').TextEncoder;
(global as any).TextDecoder = (global as any).TextDecoder ?? require('util').TextDecoder;

// A fake fetch ReadableStream body: yields each string as one encoded chunk, then done.
function streamBody(chunks: string[]) {
  const enc = new (global as any).TextEncoder();
  let i = 0;
  return {
    getReader: () => ({
      read: async () => (i < chunks.length ? { done: false, value: enc.encode(chunks[i++]) } : { done: true }),
    }),
  };
}

describe('HttpDataClient.chat', () => {
  afterEach(() => {
    // @ts-expect-error test cleanup
    delete global.fetch;
  });

  const wire = (o: object) => `data: ${JSON.stringify(o)}\n\n`;

  it('POSTs the request and streams events in order, resolving the folded turn', async () => {
    // one frame split across two chunks proves the reader buffers partials
    const think = wire({ type: 'think', ts: 't', content: 'looking' });
    const chunks = [
      think.slice(0, 10),
      think.slice(10),
      wire({ type: 'tool_result', ts: 't', id: 'x', name: 'query_metrics', content: 'cpu 99 [metrics:0]', n: 1 }),
      wire({ type: 'gate', ts: 't', ok: true, missing: [], retry: 0 }),
      wire({ type: 'assistant_msg', ts: 't', content: 'r1 cpu pegged [metrics:0]' }),
    ];
    const fetchMock = jest.fn(async () => ({ ok: true, body: streamBody(chunks) }));
    global.fetch = fetchMock as unknown as typeof fetch;

    const client = new HttpDataClient('http://x', 1000, 'http://copilot', 5000);
    const seen: string[] = [];
    const turn = await client.chat(
      { question: 'what now?', sessionId: 's1', workspace: false, start: 100, end: 200 },
      (e: ChatEvent) => seen.push(e.type)
    );

    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe('http://copilot/chat');
    expect(init.method).toBe('POST');
    expect(JSON.parse(init.body as string)).toEqual({
      question: 'what now?',
      session_id: 's1',
      workspace: false,
      start: 100,
      end: 200,
    });
    expect(seen).toEqual(['think', 'tool_result', 'gate', 'assistant_msg']);
    expect(turn.answer).toBe('r1 cpu pegged [metrics:0]');
    expect(turn.citations).toEqual([{ id: 'metrics:0', source: 'metrics', offset: 0 }]);
    expect(turn.gate).toMatchObject({ ok: true });
  });

  it('Live mode omits start/end from the body', async () => {
    const fetchMock = jest.fn(async () => ({ ok: true, body: streamBody([]) }));
    global.fetch = fetchMock as unknown as typeof fetch;
    const client = new HttpDataClient('http://x', 1000, 'http://copilot', 5000);
    await client.chat({ question: 'q', sessionId: 's1', workspace: true }, () => undefined);
    expect(JSON.parse((fetchMock.mock.calls[0] as unknown as [string, RequestInit])[1].body as string)).toEqual({
      question: 'q',
      session_id: 's1',
      workspace: true,
    });
  });

  it('rejects when the copilot service is unreachable', async () => {
    global.fetch = jest.fn(async () => {
      throw new Error('ECONNREFUSED');
    }) as unknown as typeof fetch;
    const client = new HttpDataClient('http://x', 1000, 'http://copilot', 5000);
    await expect(client.chat({ question: 'q', sessionId: 's1', workspace: false }, () => undefined)).rejects.toBeDefined();
  });
});
