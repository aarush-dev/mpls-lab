import { appReducer, initialAppState, AppState, DEFAULT_LIVE_WINDOW_SEC } from './reducer';

const NOW = 1_800_000_000_000; // fixed epoch-ms for deterministic tests

describe('appReducer', () => {
  describe('TICK (live refresh)', () => {
    it('slides the range to end at nowMs and bumps refreshTick when live', () => {
      const s = appReducer(initialAppState, { type: 'TICK', payload: { nowMs: NOW } });
      expect(s.range.toMs).toBe(NOW);
      expect(s.range.fromMs).toBe(NOW - DEFAULT_LIVE_WINDOW_SEC * 1000);
      expect(s.refreshTick).toBe(1);
    });

    it('is a no-op in history mode', () => {
      const hist: AppState = { ...initialAppState, mode: 'history', range: { fromMs: 1, toMs: 2 } };
      const s = appReducer(hist, { type: 'TICK', payload: { nowMs: NOW } });
      expect(s).toBe(hist);
    });
  });

  describe('SET_MODE', () => {
    it('to live re-slides range from nowMs', () => {
      const hist: AppState = { ...initialAppState, mode: 'history', range: { fromMs: 1, toMs: 2 } };
      const s = appReducer(hist, { type: 'SET_MODE', payload: { mode: 'live', nowMs: NOW } });
      expect(s.mode).toBe('live');
      expect(s.range.toMs).toBe(NOW);
    });

    it('to history freezes the current range', () => {
      const live: AppState = { ...initialAppState, range: { fromMs: 10, toMs: 20 } };
      const s = appReducer(live, { type: 'SET_MODE', payload: { mode: 'history', nowMs: NOW } });
      expect(s.mode).toBe('history');
      expect(s.range).toEqual({ fromMs: 10, toMs: 20 });
    });
  });

  describe('SET_RANGE', () => {
    it('sets an absolute range and switches to history', () => {
      const s = appReducer(initialAppState, { type: 'SET_RANGE', payload: { fromMs: 100, toMs: 200 } });
      expect(s.mode).toBe('history');
      expect(s.range).toEqual({ fromMs: 100, toMs: 200 });
      expect(s.refreshTick).toBe(1);
    });
  });

  describe('SET_LIVE_WINDOW', () => {
    it('changes the window length and re-slides in live mode', () => {
      const s = appReducer(initialAppState, { type: 'SET_LIVE_WINDOW', payload: { sec: 300, nowMs: NOW } });
      expect(s.liveWindowSec).toBe(300);
      expect(s.range.fromMs).toBe(NOW - 300 * 1000);
    });

    it('floors the window at 30s', () => {
      const s = appReducer(initialAppState, { type: 'SET_LIVE_WINDOW', payload: { sec: 1, nowMs: NOW } });
      expect(s.liveWindowSec).toBe(30);
    });
  });

  describe('SET_FILTER', () => {
    it('sets a key when value is defined', () => {
      const result = appReducer(initialAppState, { type: 'SET_FILTER', payload: { key: 'pop', value: 'pop-1' } });
      expect(result.filters).toEqual({ pop: 'pop-1' });
    });

    it('deletes the key when value is undefined', () => {
      const state: AppState = { ...initialAppState, filters: { pop: 'pop-1', vrf: 'vrf-a' } };
      const result = appReducer(state, { type: 'SET_FILTER', payload: { key: 'pop', value: undefined } });
      expect(result.filters).toEqual({ vrf: 'vrf-a' });
    });
  });

  describe('CLEAR_FILTERS', () => {
    it('empties filters', () => {
      const state: AppState = { ...initialAppState, filters: { pop: 'pop-1', vrf: 'vrf-a' } };
      expect(appReducer(state, { type: 'CLEAR_FILTERS' }).filters).toEqual({});
    });
  });

  describe('injected faults', () => {
    it('INJECT_FAULT adds a pending fault with a 30/60/90 lead, replacing any prior fault on the same node', () => {
      let s = appReducer(initialAppState, { type: 'INJECT_FAULT', payload: { node: 'pe1', faultType: 'node_failure' } });
      expect(s.injectedFaults).toHaveLength(1);
      expect(s.injectedFaults[0]).toMatchObject({ node: 'pe1', faultType: 'node_failure', phase: 'pending' });
      expect([30, 60, 90]).toContain(s.injectedFaults[0].leadSec);
      s = appReducer(s, { type: 'INJECT_FAULT', payload: { node: 'pe1', faultType: 'congestion' } });
      expect(s.injectedFaults).toHaveLength(1);
      expect(s.injectedFaults[0]).toMatchObject({ node: 'pe1', faultType: 'congestion', phase: 'pending' });
    });

    it('ADVANCE_FAULT escalates the phase of one node', () => {
      let s = appReducer(initialAppState, { type: 'INJECT_FAULT', payload: { node: 'pe1', faultType: 'congestion' } });
      s = appReducer(s, { type: 'ADVANCE_FAULT', payload: { node: 'pe1', phase: 'predicted' } });
      expect(s.injectedFaults[0].phase).toBe('predicted');
      s = appReducer(s, { type: 'ADVANCE_FAULT', payload: { node: 'pe1', phase: 'down' } });
      expect(s.injectedFaults[0].phase).toBe('down');
    });

    it('CLEAR_FAULT removes one node, CLEAR_INJECTED empties all', () => {
      let s: AppState = {
        ...initialAppState,
        injectedFaults: [
          { node: 'pe1', faultType: 'node_failure', phase: 'down', leadSec: 60 },
          { node: 'ce_branch2', faultType: 'congestion', phase: 'predicted', leadSec: 30 },
        ],
      };
      s = appReducer(s, { type: 'CLEAR_FAULT', payload: { node: 'pe1' } });
      expect(s.injectedFaults).toEqual([{ node: 'ce_branch2', faultType: 'congestion', phase: 'predicted', leadSec: 30 }]);
      s = appReducer(s, { type: 'CLEAR_INJECTED' });
      expect(s.injectedFaults).toEqual([]);
    });
  });
});
