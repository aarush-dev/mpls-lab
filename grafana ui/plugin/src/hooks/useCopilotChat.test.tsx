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
