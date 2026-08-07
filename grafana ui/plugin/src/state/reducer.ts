// Global time-context state for a LIVE backend (replaces the old fixture-replay tape).
// `mode` = live (auto-refresh, window follows now) | history (static, user-picked range).
// `range` is the active window every page passes as its telemetry timeRange. `refreshTick` is
// bumped on every live refresh / manual refresh so data effects can key on it. The reducer stays
// pure — wall-clock `nowMs` is passed IN via the action (AppContext supplies Date.now()), never read
// here, so the reducer remains deterministic/testable.

import type { Filters } from '../data/types';

export type ClockMode = 'live' | 'history';

/** Active data window in epoch-ms. Passed to every DataClient call as timeRange. */
export interface TimeRangeMs {
  fromMs: number;
  toMs: number;
}

export interface AppState {
  mode: ClockMode;
  range: TimeRangeMs;
  /** Live window length in seconds (how far back "live" looks). */
  liveWindowSec: number;
  /** Bumped on each live refresh / manual refresh. Data effects depend on it to refetch. */
  refreshTick: number;
  // Global operator filters (pop/siteType/device/vrf/hub). Passed to every DataClient call.
  filters: Filters;
}

export const DEFAULT_LIVE_WINDOW_SEC = 900; // 15 min

// range starts empty; AppContext dispatches a TICK on mount to fill it from the real clock.
// HttpDataClient treats an empty/zero range as "last liveWindow" so first paint is safe.
export const initialAppState: AppState = {
  mode: 'live',
  range: { fromMs: 0, toMs: 0 },
  liveWindowSec: DEFAULT_LIVE_WINDOW_SEC,
  refreshTick: 0,
  filters: {},
};

export type AppAction =
  | { type: 'TICK'; payload: { nowMs: number } }
  | { type: 'REFRESH'; payload: { nowMs: number } }
  | { type: 'SET_MODE'; payload: { mode: ClockMode; nowMs: number } }
  | { type: 'SET_RANGE'; payload: { fromMs: number; toMs: number } }
  | { type: 'SET_LIVE_WINDOW'; payload: { sec: number; nowMs: number } }
  | { type: 'SET_FILTER'; payload: { key: StringFilterKey; value: string | undefined } }
  | { type: 'CLEAR_FILTERS' };

// String-valued filter keys only (excludes timeRange). SET_FILTER never touches timeRange.
export type StringFilterKey = 'pop' | 'siteType' | 'device' | 'vrf' | 'hub';

/** Slide the live window to end at nowMs. */
function liveRange(nowMs: number, liveWindowSec: number): TimeRangeMs {
  return { fromMs: nowMs - liveWindowSec * 1000, toMs: nowMs };
}

export function appReducer(state: AppState, action: AppAction): AppState {
  switch (action.type) {
    case 'TICK': {
      // Live refresh only; ignored while paused in history.
      if (state.mode !== 'live') {
        return state;
      }
      return {
        ...state,
        range: liveRange(action.payload.nowMs, state.liveWindowSec),
        refreshTick: state.refreshTick + 1,
      };
    }
    case 'REFRESH':
      return {
        ...state,
        range: state.mode === 'live' ? liveRange(action.payload.nowMs, state.liveWindowSec) : state.range,
        refreshTick: state.refreshTick + 1,
      };
    case 'SET_MODE': {
      if (action.payload.mode === 'live') {
        return {
          ...state,
          mode: 'live',
          range: liveRange(action.payload.nowMs, state.liveWindowSec),
          refreshTick: state.refreshTick + 1,
        };
      }
      // Freeze on the current window when entering history.
      return { ...state, mode: 'history', refreshTick: state.refreshTick + 1 };
    }
    case 'SET_RANGE':
      // A user-picked absolute range implies history (static) mode.
      return {
        ...state,
        mode: 'history',
        range: { fromMs: action.payload.fromMs, toMs: action.payload.toMs },
        refreshTick: state.refreshTick + 1,
      };
    case 'SET_LIVE_WINDOW': {
      const sec = Math.max(30, action.payload.sec);
      return {
        ...state,
        liveWindowSec: sec,
        range: state.mode === 'live' ? liveRange(action.payload.nowMs, sec) : state.range,
        refreshTick: state.refreshTick + 1,
      };
    }
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
    default:
      return state;
  }
}
