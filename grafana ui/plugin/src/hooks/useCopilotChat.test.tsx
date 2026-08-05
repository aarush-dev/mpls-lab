import { act, renderHook, waitFor } from '@testing-library/react';
import { useCopilotChat } from './useCopilotChat';
import type { ChatEvent, ChatRequest, CopilotTurn } from '../data/types';

// Stub the two contexts the hook reads so it can run headless (no providers/backends).
const chat = jest.fn();
jest.mock('../data/DataClientContext', () => ({ useDataClient: () => ({ chat }) }));
let appState: { mode: 'live' | 'history'; range: { fromMs: number; toMs: number } };
jest.mock('../state/AppContext', () => ({ useAppState: () => appState }));

function turnOf(answer: string): CopilotTurn {
  return { events: [], answer, citations: [], citeMap: {} };
}

beforeEach(() => {
  chat.mockReset();
  appState = { mode: 'live', range: { fromMs: 0, toMs: 0 } };
  localStorage.clear();
});

test('items grow as events stream and sending toggles', async () => {
  // A chat that emits two events then resolves, gated so we can observe mid-stream growth.
  let emit!: (e: ChatEvent) => void;
  let finish!: (t: CopilotTurn) => void;
  chat.mockImplementation((_req: ChatRequest, onEvent: (e: ChatEvent) => void) => {
    emit = onEvent;
    return new Promise<CopilotTurn>((res) => (finish = res));
  });

  const { result } = renderHook(() => useCopilotChat());

  act(() => result.current.send('why is R1 down?'));
  expect(result.current.sending).toBe(true);
  expect(result.current.items).toHaveLength(1);
  expect(result.current.items[0].events).toHaveLength(0);

  act(() => emit({ type: 'think', ts: '1', content: 'looking' }));
  expect(result.current.items[0].events).toHaveLength(1);
  act(() => emit({ type: 'assistant_msg', ts: '2', content: 'BGP flap [events:0]' }));
  expect(result.current.items[0].events).toHaveLength(2);

  await act(async () => finish(turnOf('BGP flap [events:0]')));
  await waitFor(() => expect(result.current.sending).toBe(false));
  expect(result.current.items[0].state).toBe('done');
  expect(result.current.items[0].turn?.answer).toBe('BGP flap [events:0]');
});

test('reject marks the item errored; retry re-runs', async () => {
  chat.mockRejectedValueOnce({ message: 'unreachable' });
  const { result } = renderHook(() => useCopilotChat());

  await act(async () => result.current.send('status?'));
  await waitFor(() => expect(result.current.items[0].state).toBe('error'));
  expect(result.current.sending).toBe(false);

  chat.mockResolvedValueOnce(turnOf('all green'));
  await act(async () => result.current.retry(result.current.items[0].id));
  await waitFor(() => expect(result.current.items[0].state).toBe('done'));
  expect(result.current.items).toHaveLength(1); // retry re-runs in place, no new bubble
  expect(chat).toHaveBeenCalledTimes(2);
});

test('retry reuses the ORIGINAL turn window, not the current picker', async () => {
  chat.mockRejectedValueOnce({ message: 'down' });
  appState = { mode: 'history', range: { fromMs: 10_000, toMs: 20_000 } };
  const { result } = renderHook(() => useCopilotChat());

  await act(async () => result.current.send('q'));
  await waitFor(() => expect(result.current.items[0].state).toBe('error'));

  // Operator switches to Live before retrying — the retry must still scope to window A.
  appState = { mode: 'live', range: { fromMs: 99_000, toMs: 99_000 } };
  chat.mockResolvedValueOnce(turnOf('ok'));
  await act(async () => result.current.retry(result.current.items[0].id));
  await waitFor(() => expect(result.current.items[0].state).toBe('done'));
  expect(chat.mock.calls[1][0]).toMatchObject({ start: 10, end: 20 });
});

test('Stop aborts the in-flight turn -> state aborted, back to idle', async () => {
  // A chat that rejects only when its signal aborts (mirrors fetch abort).
  chat.mockImplementation((_req: ChatRequest, _onEvent: (e: ChatEvent) => void, signal: AbortSignal) =>
    new Promise<CopilotTurn>((_res, rej) => signal.addEventListener('abort', () => rej(new Error('aborted'))))
  );
  const { result } = renderHook(() => useCopilotChat());

  act(() => result.current.send('why is R1 down?'));
  expect(result.current.sending).toBe(true);

  await act(async () => result.current.stop());
  await waitFor(() => expect(result.current.items[0].state).toBe('aborted'));
  expect(result.current.sending).toBe(false);
});

test('New chat clears the thread and mints a fresh session id', async () => {
  chat.mockResolvedValue(turnOf('ok'));
  const { result } = renderHook(() => useCopilotChat());

  await act(async () => result.current.send('first'));
  await waitFor(() => expect(result.current.items).toHaveLength(1));
  const before = localStorage.getItem('noc.copilot.session');

  act(() => result.current.newChat());
  expect(result.current.items).toHaveLength(0);
  expect(localStorage.getItem('noc.copilot.session')).not.toBe(before);
});

test('thread persists across remount (localStorage), tagged to its session', async () => {
  chat.mockResolvedValue(turnOf('all green'));
  const first = renderHook(() => useCopilotChat());
  await act(async () => first.result.current.send('status?'));
  await waitFor(() => expect(first.result.current.items[0].state).toBe('done'));

  // Fresh mount (reload) reads the same session id + persisted thread.
  const reloaded = renderHook(() => useCopilotChat());
  expect(reloaded.result.current.items).toHaveLength(1);
  expect(reloaded.result.current.items[0].question).toBe('status?');
  expect(reloaded.result.current.items[0].turn?.answer).toBe('all green');
});

test('workspace toggle flows to the request and a retry reuses the same flag', async () => {
  chat.mockRejectedValueOnce({ message: 'down' });
  const { result } = renderHook(() => useCopilotChat());

  await act(async () => result.current.send('build a chart', true));
  await waitFor(() => expect(result.current.items[0].state).toBe('error'));
  expect(chat.mock.calls[0][0]).toMatchObject({ workspace: true });

  // Retry must re-send the ORIGINAL flag, not the composer default.
  chat.mockResolvedValueOnce(turnOf('done'));
  await act(async () => result.current.retry(result.current.items[0].id));
  expect(chat.mock.calls[1][0]).toMatchObject({ workspace: true });
});

test('workspace defaults off when send omits it', async () => {
  chat.mockResolvedValue(turnOf('ok'));
  const { result } = renderHook(() => useCopilotChat());
  await act(async () => result.current.send('read-only q'));
  expect(chat.mock.calls[0][0]).toMatchObject({ workspace: false });
});

test('after newChat + reload, a fresh send does not collide with a restored turn id', async () => {
  chat.mockResolvedValue(turnOf('ok'));
  const first = renderHook(() => useCopilotChat());
  // Session A: two turns, then New chat, then two turns in session B (ids must restart at turn-0).
  await act(async () => first.result.current.send('a1'));
  await act(async () => first.result.current.send('a2'));
  act(() => first.result.current.newChat());
  await act(async () => first.result.current.send('b1'));
  await act(async () => first.result.current.send('b2'));
  expect(first.result.current.items.map((t) => t.id)).toEqual(['turn-0', 'turn-1']);

  // Reload: restore B's thread, then send again — the new turn must get a unique id, not clobber one.
  const reloaded = renderHook(() => useCopilotChat());
  expect(reloaded.result.current.items.map((t) => t.id)).toEqual(['turn-0', 'turn-1']);
  await act(async () => reloaded.result.current.send('b3'));
  const ids = reloaded.result.current.items.map((t) => t.id);
  expect(new Set(ids).size).toBe(ids.length); // no duplicate ids
  expect(reloaded.result.current.items).toHaveLength(3);
});

test('History mode sends start/end; Live omits (asserted at the DataClient seam)', async () => {
  chat.mockResolvedValue(turnOf('ok'));

  appState = { mode: 'history', range: { fromMs: 10_000, toMs: 20_000 } };
  const hist = renderHook(() => useCopilotChat());
  await act(async () => hist.result.current.send('q'));
  expect(chat.mock.calls[0][0]).toMatchObject({ start: 10, end: 20 });

  appState = { mode: 'live', range: { fromMs: 10_000, toMs: 20_000 } };
  const live = renderHook(() => useCopilotChat());
  await act(async () => live.result.current.send('q'));
  const body = chat.mock.calls[1][0] as ChatRequest;
  expect(body.start).toBeUndefined();
  expect(body.end).toBeUndefined();
});
