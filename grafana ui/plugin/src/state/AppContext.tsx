import React, { createContext, Dispatch, PropsWithChildren, useContext, useEffect, useReducer } from 'react';
import { AppAction, AppState, InjectedFault, appReducer, initialAppState } from './reducer';

// ponytail: injectedFaults are visual-only overlay state; persist just that slice to
// localStorage so an injected fault survives page refresh/navigation (the real backend
// fault keeps running its duration regardless — only this visual was being lost).
const FAULTS_KEY = 'noc.injectedFaults';

function loadFaults(): InjectedFault[] {
  try {
    const raw = localStorage.getItem(FAULTS_KEY);
    return raw ? (JSON.parse(raw) as InjectedFault[]) : [];
  } catch {
    return [];
  }
}

interface AppContextValue {
  state: AppState;
  dispatch: Dispatch<AppAction>;
}

const AppContext = createContext<AppContextValue | undefined>(undefined);

/** Live refresh cadence: pull fresh data from the backend every 1s while in live mode.
 *  Note: visible granularity is still bounded by the VM scrape interval + the query step —
 *  a 1s refresh re-fetches faster than new samples land, so the graph updates the moment a
 *  fresh scrape appears rather than every second. */
export const LIVE_REFRESH_MS = 1000;

// Global time context. In live mode a 1s interval dispatches TICK{nowMs}, sliding the window to
// [now-liveWindow, now] and bumping refreshTick so every page refetches. In history mode the
// interval is idle and the window is whatever the user picked. Wall-clock time is read HERE (not in
// the reducer) so the reducer stays pure/deterministic.
export function AppProvider({ children }: PropsWithChildren<{}>) {
  const [state, dispatch] = useReducer(appReducer, initialAppState, (s) => ({
    ...s,
    injectedFaults: loadFaults(),
  }));
  const { mode, injectedFaults } = state;

  // Fill the initial range from the real clock on mount (initialState.range is 0/0).
  useEffect(() => {
    dispatch({ type: 'TICK', payload: { nowMs: Date.now() } });
  }, []);

  // Persist the injected-fault overlay so it survives refresh/navigation.
  useEffect(() => {
    try {
      localStorage.setItem(FAULTS_KEY, JSON.stringify(injectedFaults));
    } catch {
      // storage unavailable (private mode / quota) — visual just won't persist.
    }
  }, [injectedFaults]);

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
