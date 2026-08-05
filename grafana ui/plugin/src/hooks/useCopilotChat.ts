// Shared copilot-chat driver (T2/#68). Wraps the real streaming `DataClient.chat` seam (#67) in
// thread state: one `Turn` per exchange (question + streamed event trace + folded answer). The
// CopilotPage and (later #66) the global side panel both render off this. No mock path exists — a
// send always hits the live `:8100/chat`, so `state:'error'` is a real unreachable-backend signal.

import { useCallback, useRef, useState } from 'react';
import { useDataClient } from '../data/DataClientContext';
import { useAppState } from '../state/AppContext';
import type { ChatEvent, ChatRequest, CopilotTurn } from '../data/types';

/** One chat exchange: the user's question, the assistant's streamed events (grows as they arrive),
 * the folded turn once resolved, and a lifecycle state. `start`/`end` snapshot the History window
 * (epoch s) at send time so a Retry re-runs against the ORIGINAL window, not the current picker
 * (both undefined = Live, backend rolls its own now-window). */
export interface Turn {
  id: string;
  question: string;
  events: ChatEvent[];
  turn?: CopilotTurn;
  state: 'sending' | 'done' | 'error';
  start?: number;
  end?: number;
}

const SESSION_KEY = 'noc.copilot.session';

// One session id per browser, persisted so multi-turn memory (#66) survives reload. Read lazily so
// tests that stub localStorage still get a stable id.
function loadSessionId(): string {
  try {
    const existing = localStorage.getItem(SESSION_KEY);
    if (existing) {
      return existing;
    }
  } catch {
    // storage unavailable — fall through to an ephemeral id
  }
  const id = typeof crypto !== 'undefined' && crypto.randomUUID ? crypto.randomUUID() : `sess-${Date.now()}`;
  try {
    localStorage.setItem(SESSION_KEY, id);
  } catch {
    // ignore — ephemeral session for this mount
  }
  return id;
}

export interface UseCopilotChat {
  items: Turn[];
  sending: boolean;
  send: (question: string) => void;
  retry: (id: string) => void;
}

export function useCopilotChat(): UseCopilotChat {
  const client = useDataClient();
  const { mode, range } = useAppState();
  const [items, setItems] = useState<Turn[]>([]);
  const sessionId = useRef<string>();
  if (!sessionId.current) {
    sessionId.current = loadSessionId();
  }
  const seq = useRef(0);
  // Synchronous mirror of `items` so `run` can read a turn's snapshot right after `send` sets it,
  // without waiting for the state flush.
  const itemsRef = useRef<Turn[]>(items);
  // Per-turn run token: a retry bumps it, so a slow/late callback from a superseded run (its stream
  // still draining, or its promise resolving after the retry) is dropped instead of clobbering the
  // fresh attempt's state.
  const gen = useRef<Record<string, number>>({});
  const genSeq = useRef(0);

  const setBoth = useCallback((update: (prev: Turn[]) => Turn[]) => {
    const next = update(itemsRef.current);
    itemsRef.current = next;
    setItems(next);
  }, []);

  const patch = useCallback(
    (id: string, fn: (t: Turn) => Turn) => setBoth((prev) => prev.map((t) => (t.id === id ? fn(t) : t))),
    [setBoth]
  );

  // Runs (or re-runs) the turn `id` against the live backend using the snapshot already on the turn.
  // The banner-visible incident is NEVER injected — the agent finds the fault through its own tools.
  const run = useCallback(
    (id: string) => {
      const t0 = itemsRef.current.find((t) => t.id === id);
      if (!t0) {
        return;
      }
      const my = (gen.current[id] = ++genSeq.current);
      const alive = () => gen.current[id] === my;
      patch(id, (t) => ({ ...t, state: 'sending', events: [], turn: undefined }));
      const req: ChatRequest = {
        question: t0.question,
        sessionId: sessionId.current!,
        workspace: false,
        ...(t0.start != null ? { start: t0.start, end: t0.end } : {}),
      };
      client
        .chat(req, (ev) => alive() && patch(id, (t) => ({ ...t, events: [...t.events, ev] })))
        .then((turn) => alive() && patch(id, (t) => ({ ...t, turn, state: 'done' })))
        .catch(() => alive() && patch(id, (t) => ({ ...t, state: 'error' })));
    },
    [client, patch]
  );

  const send = useCallback(
    (question: string) => {
      const id = `turn-${seq.current++}`;
      const scope = mode === 'history' ? { start: Math.floor(range.fromMs / 1000), end: Math.floor(range.toMs / 1000) } : {};
      setBoth((prev) => [...prev, { id, question, events: [], state: 'sending', ...scope }]);
      run(id);
    },
    [mode, range.fromMs, range.toMs, setBoth, run]
  );

  const retry = useCallback((id: string) => run(id), [run]);

  // Derived so it can't desync from the turns (an overlapping send + retry each own their own turn).
  const sending = items.some((t) => t.state === 'sending');

  return { items, sending, send, retry };
}
