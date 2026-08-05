import { parseSseFrames, mapEventsToTurn } from './copilotChat';
import type { ChatEvent } from './types';

// event_wire dicts as the backend streams them: {type, ts, ...payload}.
const ts = '2026-08-05T00:00:00+00:00';
const ev = (type: string, extra: Record<string, unknown> = {}): ChatEvent =>
  ({ type, ts, ...extra } as ChatEvent);

describe('parseSseFrames', () => {
  it('splits a complete frame and leaves no tail', () => {
    const { frames, rest } = parseSseFrames('data: {"a":1}\n\n');
    expect(frames).toEqual(['{"a":1}']);
    expect(rest).toBe('');
  });

  it('buffers a partial frame across chunks (tail carried in rest)', () => {
    const a = parseSseFrames('data: {"ty');
    expect(a.frames).toEqual([]);
    expect(a.rest).toBe('data: {"ty');
    // caller carries rest forward + appends the next chunk
    const b = parseSseFrames(a.rest + 'pe":"think"}\n\n');
    expect(b.frames).toEqual(['{"type":"think"}']);
    expect(b.rest).toBe('');
  });

  it('yields multiple frames from one chunk', () => {
    const { frames } = parseSseFrames('data: a\n\ndata: b\n\n');
    expect(frames).toEqual(['a', 'b']);
  });

  it('tolerates CRLF frame delimiters (proxy-rewritten)', () => {
    const { frames, rest } = parseSseFrames('data: {"a":1}\r\n\r\n');
    expect(frames).toEqual(['{"a":1}']);
    expect(rest).toBe('');
  });

  it('drops a blank keep-alive frame', () => {
    const { frames, rest } = parseSseFrames(': keep-alive\n\ndata: x\n\n');
    expect(frames).toEqual(['x']);
    expect(rest).toBe('');
  });
});

describe('mapEventsToTurn', () => {
  it('takes the final assistant_msg as the answer', () => {
    const turn = mapEventsToTurn([
      ev('assistant_msg', { content: 'first' }),
      ev('gate', { ok: true, missing: [], retry: 0 }),
      ev('assistant_msg', { content: 'final answer' }),
    ]);
    expect(turn.answer).toBe('final answer');
  });

  it('extracts + dedups [source:offset] citations and maps each to its tool_result', () => {
    const tr = ev('tool_result', { id: 't1', name: 'query_metrics', content: 'row [metrics:0] cpu 99%', n: 1 });
    const turn = mapEventsToTurn([
      tr,
      ev('assistant_msg', { content: 'cpu pegged [metrics:0] and again [metrics:0], see [events:2]' }),
    ]);
    expect(turn.citations).toEqual([
      { id: 'metrics:0', source: 'metrics', offset: 0 },
      { id: 'events:2', source: 'events', offset: 2 },
    ]);
    expect(turn.citeMap['metrics:0']).toBe(tr); // maps to the source event
    expect(turn.citeMap['events:2']).toBeUndefined(); // no tool_result carries it
  });

  it('surfaces the last gate outcome', () => {
    const turn = mapEventsToTurn([
      ev('gate', { ok: false, missing: ['x'], retry: 0 }),
      ev('gate', { ok: true, missing: [], retry: 1 }),
      ev('assistant_msg', { content: 'done' }),
    ]);
    expect(turn.gate).toMatchObject({ ok: true, retry: 1 });
  });

  it('ask-back: a clarifying assistant_msg has no citations and no gate', () => {
    const turn = mapEventsToTurn([
      ev('user_msg', { content: 'help' }),
      ev('assistant_msg', { content: 'Which device do you mean?' }),
    ]);
    expect(turn.answer).toBe('Which device do you mean?');
    expect(turn.citations).toEqual([]);
    expect(turn.gate).toBeUndefined();
  });
});
