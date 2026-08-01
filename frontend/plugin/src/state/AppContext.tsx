import React, { createContext, Dispatch, PropsWithChildren, useContext, useEffect, useReducer } from 'react';
import { AppAction, AppState, appReducer, initialAppState } from './reducer';
import { MOCK_BUCKET_META } from '../data/MockDataClient';

interface AppContextValue {
  state: AppState;
  dispatch: Dispatch<AppAction>;
}

const AppContext = createContext<AppContextValue | undefined>(undefined);

// Single global demo clock. Every page reads `state.cursor` and is a pure function of it, so all
// pages always show the same "live" moment (locked decision #3/#5 in the plan). Autoplay defaults
// on + loops; pause/scrub/speed are user-driven overrides. The interval only ever dispatches TICK
// — it never reads wall-clock time, keeping playback deterministic.
export function AppProvider({ children }: PropsWithChildren<{}>) {
  const [state, dispatch] = useReducer(appReducer, initialAppState);
  const { playing, speed, bucketCount } = state;

  useEffect(() => {
    if (!playing || bucketCount <= 0) {
      return undefined;
    }
    // Real-time playback: one bucket of data takes one bucket's wall-clock time (30s/bucket).
    // speed stays a multiplier (2x -> 15s/bucket).
    const intervalMs = MOCK_BUCKET_META.bucketMs / Math.max(speed, 0.01);
    const id = setInterval(() => dispatch({ type: 'TICK' }), intervalMs);
    return () => clearInterval(id);
  }, [playing, speed, bucketCount]);

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
