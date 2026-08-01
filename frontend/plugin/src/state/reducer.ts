// Global demo-clock state. cursor = current bucket index into the composite playback tape.
// bucketCount/windowBuckets are populated once (SET_BOUNDS) when fixture metadata loads.
// No wall-clock time is read here — TICK advances the cursor by exactly one bucket per call,
// driven by a setInterval in AppContext.tsx (deterministic, testable).

import type { Filters } from '../data/types';

/** A manually injected fault (demo control): forces a node red + fires an alert. */
export interface InjectedFault {
  node: string;
  faultType: string;
}

export interface AppState {
  cursor: number;
  // Ever-increasing display clock (never wraps). cursor drives DATA reads (wraps on loop); absTick
  // drives the DISPLAY time axis so the chart clock never rewinds when the tape loops (151->0).
  absTick: number;
  playing: boolean;
  speed: number;
  loop: boolean;
  bucketCount: number;
  windowBuckets: number;
  // Global operator filters (pop/siteType/device/vrf/hub). Passed to every DataClient call.
  filters: Filters;
  // Manually injected faults (Fault Injection page). Overlaid on the replayed node states.
  injectedFaults: InjectedFault[];
}

export const initialAppState: AppState = {
  cursor: 0,
  absTick: 0,
  playing: true,
  speed: 1,
  loop: true,
  bucketCount: 0,
  windowBuckets: 50,
  filters: {},
  injectedFaults: [],
};

export type AppAction =
  | { type: 'TICK' }
  | { type: 'PLAY' }
  | { type: 'PAUSE' }
  | { type: 'SEEK'; payload: { cursor: number } }
  | { type: 'SET_SPEED'; payload: { speed: number } }
  | { type: 'SET_BOUNDS'; payload: { bucketCount: number; windowBuckets: number } }
  | { type: 'SET_FILTER'; payload: { key: StringFilterKey; value: string | undefined } }
  | { type: 'CLEAR_FILTERS' }
  | { type: 'INJECT_FAULT'; payload: InjectedFault }
  | { type: 'CLEAR_FAULT'; payload: { node: string } }
  | { type: 'CLEAR_INJECTED' };

// String-valued filter keys only (excludes timeRange). SET_FILTER never touches timeRange.
export type StringFilterKey = 'pop' | 'siteType' | 'device' | 'vrf' | 'hub';

export function appReducer(state: AppState, action: AppAction): AppState {
  switch (action.type) {
    case 'TICK': {
      if (state.bucketCount <= 0) {
        return state;
      }
      const next = state.loop
        ? (state.cursor + 1) % state.bucketCount
        : Math.min(state.cursor + 1, state.bucketCount - 1);
      return { ...state, cursor: next, absTick: state.absTick + 1 };
    }
    case 'PLAY':
      return { ...state, playing: true };
    case 'PAUSE':
      return { ...state, playing: false };
    case 'SEEK': {
      if (state.bucketCount <= 0) {
        return { ...state, cursor: Math.max(0, action.payload.cursor) };
      }
      const clamped = Math.max(0, Math.min(action.payload.cursor, state.bucketCount - 1));
      // Phase-align absTick to the scrubbed cursor within the current loop so the display clock
      // tracks the slider without rewinding across earlier loops.
      const absTick = Math.floor(state.absTick / state.bucketCount) * state.bucketCount + clamped;
      return { ...state, cursor: clamped, absTick };
    }
    case 'SET_SPEED':
      return { ...state, speed: action.payload.speed };
    case 'SET_FILTER': {
      const filters = { ...state.filters };
      if (action.payload.value) {
        filters[action.payload.key] = action.payload.value;
      } else {
        delete filters[action.payload.key];
      }
      return { ...state, filters };
    }
    case 'CLEAR_FILTERS':
      return { ...state, filters: {} };
    case 'INJECT_FAULT': {
      // Re-injecting the same node replaces its fault type (idempotent by node).
      const rest = state.injectedFaults.filter((f) => f.node !== action.payload.node);
      return { ...state, injectedFaults: [...rest, action.payload] };
    }
    case 'CLEAR_FAULT':
      return { ...state, injectedFaults: state.injectedFaults.filter((f) => f.node !== action.payload.node) };
    case 'CLEAR_INJECTED':
      return { ...state, injectedFaults: [] };
    case 'SET_BOUNDS': {
      const bucketCount = Math.max(0, action.payload.bucketCount);
      const windowBuckets = Math.max(1, action.payload.windowBuckets);
      const cursor = bucketCount > 0 ? Math.min(state.cursor, bucketCount - 1) : 0;
      return { ...state, bucketCount, windowBuckets, cursor };
    }
    default:
      return state;
  }
}
