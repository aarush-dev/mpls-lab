import React, { createContext, Dispatch, PropsWithChildren, useContext, useEffect, useReducer } from 'react';
import { AppAction, AppState, appReducer, initialAppState } from './reducer';

interface AppContextValue {
  state: AppState;
  dispatch: Dispatch<AppAction>;
}

const AppContext = createContext<AppContextValue | undefined>(undefined);

/** Live refresh cadence: pull fresh data from the backend every 5s while in live mode.
 *  ponytail: was 1s (619f8a8d) — but NodeDetailPage fans out ~38 requests per tick (32 metrics +
 *  /topology + 3×/labels + events + flows) and fetchJson does NOT abort on effect cleanup, so a 1s
 *  tick queued 38 un-abortable fetches/sec into the browser's ~6 conn/origin slots faster than they
 *  drained → permanent slot starvation → node detail stuck on "Loading…". 5s drains fine. If sub-5s
 *  liveness is ever needed, abort superseded fetches in the effect cleanup first, then lower this. */
export const LIVE_REFRESH_MS = 5000;

// Global time context. In live mode a 1s interval dispatches TICK{nowMs}, sliding the window to
// [now-liveWindow, now] and bumping refreshTick so every page refetches. In history mode the
// interval is idle and the window is whatever the user picked. Wall-clock time is read HERE (not in
// the reducer) so the reducer stays pure/deterministic.
export function AppProvider({ children }: PropsWithChildren<{}>) {
  const [state, dispatch] = useReducer(appReducer, initialAppState);
  const { mode } = state;

  // Fill the initial range from the real clock on mount (initialState.range is 0/0).
  useEffect(() => {
    dispatch({ type: 'TICK', payload: { nowMs: Date.now() } });
  }, []);

  useEffect(() => {
    if (mode !== 'live') {
      return undefined;
    }
    const id = setInterval(() => dispatch({ type: 'TICK', payload: { nowMs: Date.now() } }), LIVE_REFRESH_MS);
    return () => clearInterval(id);
  }, [mode]);

  return <AppContext.Provider value={{ state, dispatch }}>{children}</AppContext.Provider>;
}

function useAppContext(): AppContextValue {
  const ctx = useContext(AppContext);
  if (!ctx) {
    throw new Error('useAppState/useAppDispatch must be used within an AppProvider');
  }
  return ctx;
}

export function useAppState(): AppState {
  return useAppContext().state;
}

export function useAppDispatch(): Dispatch<AppAction> {
  return useAppContext().dispatch;
}
