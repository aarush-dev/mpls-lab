// Seam-1 (UI-2 #51): CopilotClient parses the copilot's buffered SSE trace and maps it to the
// structured shape the UI renders. Feed canned `data:` bytes, assert the emitted events + the
// assembled response. The framing must be identical whether the body arrives in one chunk or
// split across reads (buffered today / streamed later). Prior art: HttpDataClient.test.ts.
import { CopilotClient } from './CopilotClient';
import { CopilotEvent } from './types';

// A full investigation: user -> think -> tool -> result -> gate(fail) -> tool -> result -> gate(pass) -> answer.
const EVENTS: CopilotEvent[] = [
  { type: 'user_msg', ts: 't0', content: 'why is r1 slow?' },
  { type: 'think', ts: 't1', content: 'check metrics first' },
  { type: 'tool_call', ts: 't2', name: 'query_metrics', arguments: { device: 'r1' }, id: 'c1' },
  { type: 'tool_result', ts: 't3', id: 'c1', name: 'query_metrics', content: '[metrics:0] cpu 95', n: 3 },
  { type: 'gate', ts: 't4', ok: false, missing: ['topology'], retry: 0 },
  { type: 'tool_call', ts: 't5', name: 'walk_topology_graph', arguments: { device: 'r1' }, id: 'c2' },
  { type: 'tool_result', ts: 't6', id: 'c2', name: 'walk_topology_graph', content: '[topology:0] pe1', n: 1 },
  { type: 'gate', ts: 't7', ok: true, missing: [], retry: 1 },
  { type: 'assistant_msg', ts: 't8', content: 'r1 cpu pegged [metrics:0], blast radius pe1 [topology:0]' },
];

function sseText(events: CopilotEvent[]): string {
  return events.map((e) => `data: ${JSON.stringify(e)}\n\n`).join('');
}

// A Response-like object whose body streams `text` in `chunks` pieces via getReader().
function sseResponse(text: string, chunks = 1): Response {
  const bytes = new TextEncoder().encode(text);
  const size = Math.ceil(bytes.length / chunks);
  const parts: Uint8Array[] = [];
  for (let i = 0; i < bytes.length; i += size) {
    parts.push(bytes.slice(i, i + size));
  }
  let idx = 0;
  const reader = {
    read: () =>
      idx < parts.length
        ? Promise.resolve({ done: false, value: parts[idx++] })
        : Promise.resolve({ done: true, value: undefined }),
  };
  return {
    ok: true,
    status: 200,
    body: { getReader: () => reader },
    text: () => Promise.resolve(text),
  } as unknown as Response;
}

function mockFetch(resp: Response) {
  global.fetch = jest.fn(async () => resp) as unknown as typeof fetch;
}

afterEach(() => {
  // @ts-expect-error reset between tests
  delete global.fetch;
});

describe('CopilotClient.streamChat', () => {
  it('emits events in order and assembles the response', async () => {
    mockFetch(sseResponse(sseText(EVENTS)));
    const seen: CopilotEvent[] = [];
    const client = new CopilotClient('http://copilot', 5000);

    const { response, trace } = await client.streamChat({ question: 'why is r1 slow?' }, (e) => seen.push(e));

    expect(seen.map((e) => e.type)).toEqual(EVENTS.map((e) => e.type));
    // trace excludes user_msg / assistant_msg
    expect(trace.map((s) => s.kind)).toEqual([
      'think',
      'tool_call',
      'tool_result',
      'gate',
      'tool_call',
      'tool_result',
      'gate',
    ]);
    // summary = last assistant_msg
    expect(response.summary).toBe('r1 cpu pegged [metrics:0], blast radius pe1 [topology:0]');
    // evidence built from tool_results
    expect(response.evidence).toHaveLength(2);
    expect(response.evidence[0].label).toBe('query_metrics');
    expect(response.evidence[0].detail).toContain('cpu 95');
    expect(response.evidence[0].detail).toContain('3 rows');
    expect(response.evidence[0].source).toBe('modelled');
    // gate verdict folds to the last outcome + max retry
    expect(response.gateVerdict).toEqual({ ok: true, missing: [], retry: 1 });
    // citations parsed from the prose, deduped
    expect(response.citations.map((c) => c.title).sort()).toEqual(['[metrics:0]', '[topology:0]']);
  });

  it('yields identical results whether buffered in one chunk or split across reads', async () => {
    mockFetch(sseResponse(sseText(EVENTS), 1));
    const a = await new CopilotClient('http://copilot', 5000).streamChat({ question: 'q' }, () => {});
    mockFetch(sseResponse(sseText(EVENTS), 7)); // split mid-frame
    const b = await new CopilotClient('http://copilot', 5000).streamChat({ question: 'q' }, () => {});
    expect(b.response).toEqual(a.response);
    expect(b.trace).toEqual(a.trace);
  });

  it('maps a non-ok HTTP response to a normalized ApiError', async () => {
    global.fetch = jest.fn(async () => ({
      ok: false,
      status: 503,
      json: async () => ({ detail: 'copilot down' }),
      text: async () => '{"detail":"copilot down"}',
    })) as unknown as typeof fetch;
    const client = new CopilotClient('http://copilot', 5000);
    await expect(client.streamChat({ question: 'q' }, () => {})).rejects.toMatchObject({
      status: 503,
      message: 'copilot down',
    });
  });
});
