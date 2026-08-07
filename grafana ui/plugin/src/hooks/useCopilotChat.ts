// Shared copilot-chat driver (T2/#68). Wraps the real streaming `DataClient.chat` seam (#67) in
// thread state: one `Turn` per exchange (question + streamed event trace + folded answer). The
// CopilotPage and (later #66) the global side panel both render off this. No mock path exists — a
// send always hits the live `:8100/chat`, so `state:'error'` is a real unreachable-backend signal.

import { useCallback, useEffect, useRef, useState } from 'react';
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
  state: 'sending' | 'done' | 'error' | 'aborted';
  start?: number;
  end?: number;
  workspace?: boolean; // T6/#71: snapshot the toggle at send so a Retry re-runs with the same tools
}

const SESSION_KEY = 'noc.copilot.session';
const THREAD_KEY = 'noc.copilot.thread';

function mintId(): string {
  return typeof crypto !== 'undefined' && crypto.randomUUID ? crypto.randomUUID() : `sess-${Date.now()}`;
}

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
  const id = mintId();
  try {
    localStorage.setItem(SESSION_KEY, id);
  } catch {
    // ignore — ephemeral session for this mount
  }
  return id;
}

// Drop the heavy inline payload from artifact events so a persisted thread can't blow the storage
// quota (T6/#71). Non-artifact events pass through untouched.
function stripArtifactBytes(events: ChatEvent[]): ChatEvent[] {
  return events.map((e) => (e.type === 'artifact' ? { ...e, content_b64: undefined, content: undefined } : e));
}

// Restore the thread for `sid` (#70). The thread is stored tagged with its session so a New chat's
// fresh id naturally starts empty and a stale thread never bleeds across sessions. A turn caught
// mid-flight by the reload has no live stream to resume — settle it as aborted, not stuck 'sending'.
function loadThread(sid: string): Turn[] {
  try {
    const raw = localStorage.getItem(THREAD_KEY);
    if (!raw) {
      return [];
    }
    const parsed = JSON.parse(raw) as { sid: string; items: Turn[] };
    if (parsed.sid !== sid || !Array.isArray(parsed.items)) {
      return [];
    }
    return parsed.items.map((t) => (t.state === 'sending' ? { ...t, state: 'aborted' } : t));
  } catch {
    return [];
  }
}

export interface UseCopilotChat {
  items: Turn[];
  sending: boolean;
  /** `scope` pins the History window (epoch s) for this turn; omit to derive from the global picker. */
  send: (question: string, workspace?: boolean, scope?: { start: number; end: number }) => void;
  retry: (id: string) => void;
  /** Abort every in-flight turn; each returns to idle (state 'aborted'), never 'error'. */
  stop: () => void;
  /** Mint a fresh session id and clear the thread — a clean multi-turn slate. */
  newChat: () => void;
}

export function useCopilotChat(): UseCopilotChat {
  const client = useDataClient();
  const { mode, range } = useAppState();
  const sessionId = useRef<string>();
  if (!sessionId.current) {
    sessionId.current = loadSessionId();
  }
  const [items, setItems] = useState<Turn[]>(() => loadThread(sessionId.current!));
  // Seed past any restored turn ids (turn-0..turn-n-1) so a post-reload send can't collide with one.
  const seq = useRef(items.length);
  // Live AbortControllers keyed by turn id — Stop (#70) aborts them; each settles on resolve/reject.
  const controllers = useRef<Record<string, AbortController>>({});
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
      const controller = new AbortController();
      controllers.current[id] = controller;
      patch(id, (t) => ({ ...t, state: 'sending', events: [], turn: undefined }));
      const req: ChatRequest = {
        question: t0.question,
        sessionId: sessionId.current!,
        workspace: t0.workspace ?? false,
        ...(t0.start != null ? { start: t0.start, end: t0.end } : {}),
      };
      client
        .chat(req, (ev) => alive() && patch(id, (t) => ({ ...t, events: [...t.events, ev] })), controller.signal)
        .then((turn) => alive() && patch(id, (t) => ({ ...t, turn, state: 'done' })))
        // A caller-aborted turn (Stop) returns to idle, not the unreachable-backend error state.
        .catch(() => alive() && patch(id, (t) => ({ ...t, state: controller.signal.aborted ? 'aborted' : 'error' })))
        .finally(() => {
          if (controllers.current[id] === controller) {
            delete controllers.current[id];
          }
        });
    },
    [client, patch]
  );

  const send = useCallback(
    (question: string, workspace = false, scope?: { start: number; end: number }) => {
      const id = `turn-${seq.current++}`;
      const win = scope ?? (mode === 'history' ? { start: Math.floor(range.fromMs / 1000), end: Math.floor(range.toMs / 1000) } : {});
      setBoth((prev) => [...prev, { id, question, events: [], state: 'sending', workspace, ...win }]);
      run(id);
    },
    [mode, range.fromMs, range.toMs, setBoth, run]
  );

  const retry = useCallback((id: string) => run(id), [run]);

  // Abort every in-flight turn; the run()'s abort-aware catch settles each to 'aborted' (idle).
  const stop = useCallback(() => {
    for (const c of Object.values(controllers.current)) {
      c.abort();
    }
  }, []);

  // Fresh slate: abort in-flight work, mint + persist a new session id, clear the thread.
  const newChat = useCallback(() => {
    stop();
    const id = mintId();
    sessionId.current = id;
    // Restart the id counter so the new session's turns are dense from turn-0 — loadThread's
    // length-based reseed on reload assumes that (else a restored turn-N collides with a fresh send).
    seq.current = 0;
    try {
      localStorage.setItem(SESSION_KEY, id);
    } catch {
      // ephemeral session — the thread still resets in memory
    }
    setBoth(() => []);
  }, [stop, setBoth]);

  // Persist the thread tagged with its session so it survives reload (loadThread restores it).
  // T6/#71: drop inline artifact bytes (content_b64/content) before persisting — one PNG turn would
  // otherwise blow the ~5MB quota and silently kill all further persistence. The trace/answer still
  // restore; the artifact just won't re-render after reload (bytes live in the session dir).
  // ponytail: still whole-thread each write otherwise — fine for a lab; page/prune if it bloats.
  useEffect(() => {
    // Skip the mid-stream churn: while a turn streams, items change per SSE event. Persist only once
    // the thread settles (no turn 'sending') — a crash mid-stream loses only a turn reload would abort.
    if (items.some((t) => t.state === 'sending')) {
      return;
    }
    try {
      const slim = items.map((t) => ({
        ...t,
        events: stripArtifactBytes(t.events),
        turn: t.turn ? { ...t.turn, events: stripArtifactBytes(t.turn.events) } : t.turn,
      }));
      localStorage.setItem(THREAD_KEY, JSON.stringify({ sid: sessionId.current, items: slim }));
    } catch {
      // storage full/unavailable — thread just won't survive reload
    }
  }, [items]);

  // Derived so it can't desync from the turns (an overlapping send + retry each own their own turn).
  const sending = items.some((t) => t.state === 'sending');

  return { items, sending, send, retry, stop, newChat };
}
