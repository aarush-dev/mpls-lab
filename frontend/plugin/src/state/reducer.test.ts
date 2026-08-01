import { appReducer, initialAppState, AppState } from './reducer';

describe('appReducer', () => {
  describe('TICK', () => {
    it('advances cursor by 1', () => {
      const state: AppState = { ...initialAppState, bucketCount: 5, cursor: 2 };
      expect(appReducer(state, { type: 'TICK' }).cursor).toBe(3);
    });

    it('wraps to 0 at bucketCount-1 when loop=true', () => {
      const state: AppState = { ...initialAppState, bucketCount: 5, cursor: 4, loop: true };
      expect(appReducer(state, { type: 'TICK' }).cursor).toBe(0);
    });

    it('clamps at last bucket when loop=false', () => {
      const state: AppState = { ...initialAppState, bucketCount: 5, cursor: 4, loop: false };
      expect(appReducer(state, { type: 'TICK' }).cursor).toBe(4);
    });

    it('is a no-op when bucketCount<=0', () => {
      const state: AppState = { ...initialAppState, bucketCount: 0, cursor: 0 };
      const result = appReducer(state, { type: 'TICK' });
      expect(result).toBe(state);
      expect(result.cursor).toBe(0);
    });
  });

  describe('PLAY/PAUSE', () => {
    it('PLAY sets playing true', () => {
      const state: AppState = { ...initialAppState, playing: false };
      expect(appReducer(state, { type: 'PLAY' }).playing).toBe(true);
    });

    it('PAUSE sets playing false', () => {
      const state: AppState = { ...initialAppState, playing: true };
      expect(appReducer(state, { type: 'PAUSE' }).playing).toBe(false);
    });
  });

  describe('SEEK', () => {
    it('clamps to [0, bucketCount-1] on the high end', () => {
      const state: AppState = { ...initialAppState, bucketCount: 10 };
      expect(appReducer(state, { type: 'SEEK', payload: { cursor: 999 } }).cursor).toBe(9);
    });

    it('clamps to [0, bucketCount-1] on the low end', () => {
      const state: AppState = { ...initialAppState, bucketCount: 10 };
      expect(appReducer(state, { type: 'SEEK', payload: { cursor: -5 } }).cursor).toBe(0);
    });

    it('accepts an in-range cursor unchanged', () => {
      const state: AppState = { ...initialAppState, bucketCount: 10 };
      expect(appReducer(state, { type: 'SEEK', payload: { cursor: 4 } }).cursor).toBe(4);
    });

    it('when bucketCount<=0, clamps only to >=0', () => {
      const state: AppState = { ...initialAppState, bucketCount: 0 };
      expect(appReducer(state, { type: 'SEEK', payload: { cursor: -3 } }).cursor).toBe(0);
      expect(appReducer(state, { type: 'SEEK', payload: { cursor: 7 } }).cursor).toBe(7);
    });
  });

  describe('SET_SPEED', () => {
    it('sets speed', () => {
      const state: AppState = { ...initialAppState, speed: 1 };
      expect(appReducer(state, { type: 'SET_SPEED', payload: { speed: 4 } }).speed).toBe(4);
    });
  });

  describe('SET_BOUNDS', () => {
    it('sets bucketCount and windowBuckets', () => {
      const state: AppState = { ...initialAppState };
      const result = appReducer(state, {
        type: 'SET_BOUNDS',
        payload: { bucketCount: 20, windowBuckets: 10 },
      });
      expect(result.bucketCount).toBe(20);
      expect(result.windowBuckets).toBe(10);
    });

    it('clamps an out-of-range cursor down to bucketCount-1', () => {
      const state: AppState = { ...initialAppState, cursor: 50, bucketCount: 100 };
      const result = appReducer(state, {
        type: 'SET_BOUNDS',
        payload: { bucketCount: 10, windowBuckets: 5 },
      });
      expect(result.cursor).toBe(9);
    });

    it('leaves an in-range cursor unchanged', () => {
      const state: AppState = { ...initialAppState, cursor: 3, bucketCount: 5 };
      const result = appReducer(state, {
        type: 'SET_BOUNDS',
        payload: { bucketCount: 10, windowBuckets: 5 },
      });
      expect(result.cursor).toBe(3);
    });

    it('resets cursor to 0 when bucketCount is 0', () => {
      const state: AppState = { ...initialAppState, cursor: 3, bucketCount: 5 };
      const result = appReducer(state, {
        type: 'SET_BOUNDS',
        payload: { bucketCount: -1, windowBuckets: 5 },
      });
      expect(result.bucketCount).toBe(0);
      expect(result.cursor).toBe(0);
    });

    it('floors windowBuckets at 1', () => {
      const state: AppState = { ...initialAppState };
      const result = appReducer(state, {
        type: 'SET_BOUNDS',
        payload: { bucketCount: 10, windowBuckets: 0 },
      });
      expect(result.windowBuckets).toBe(1);
    });
  });

  describe('SET_FILTER', () => {
    it('sets a key when value is defined', () => {
      const state: AppState = { ...initialAppState, filters: {} };
      const result = appReducer(state, { type: 'SET_FILTER', payload: { key: 'pop', value: 'pop-1' } });
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

  describe('absTick (monotonic display clock)', () => {
    it('starts at 0', () => {
      expect(initialAppState.absTick).toBe(0);
    });

    it('keeps increasing even when the data cursor wraps on loop', () => {
      const state: AppState = { ...initialAppState, bucketCount: 5, cursor: 4, absTick: 4, loop: true };
      const next = appReducer(state, { type: 'TICK' });
      expect(next.cursor).toBe(0); // data wraps
      expect(next.absTick).toBe(5); // display time does not
    });

    it('SEEK phase-aligns absTick to the scrubbed bucket within the current loop', () => {
      const state: AppState = { ...initialAppState, bucketCount: 5, cursor: 0, absTick: 12 };
      const next = appReducer(state, { type: 'SEEK', payload: { cursor: 3 } });
      expect(next.cursor).toBe(3);
      expect(next.absTick).toBe(13); // floor(12/5)*5 + 3
    });
  });
});
