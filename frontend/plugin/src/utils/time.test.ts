import {
  formatUtc,
  bucketToTsMs,
  slidingWindow,
  windowIndices,
  secondsToMs,
  msToSeconds,
  BucketMeta,
} from './time';

describe('formatUtc', () => {
  it('formats epoch 0', () => {
    expect(formatUtc(0)).toBe('1970-01-01 00:00:00Z');
  });

  it('pads single-digit month/day/hour/minute/second', () => {
    const ts = Date.UTC(2024, 0, 5, 3, 4, 5);
    expect(formatUtc(ts)).toBe('2024-01-05 03:04:05Z');
  });

  it('formats a double-digit date fully', () => {
    const ts = Date.UTC(2025, 11, 31, 23, 59, 58);
    expect(formatUtc(ts)).toBe('2025-12-31 23:59:58Z');
  });
});

describe('bucketToTsMs', () => {
  it('equals startMs + i * bucketMs', () => {
    const meta: BucketMeta = { startMs: 1000, bucketMs: 30000, bucketCount: 100 };
    expect(bucketToTsMs(meta, 0)).toBe(1000);
    expect(bucketToTsMs(meta, 5)).toBe(1000 + 5 * 30000);
  });
});

describe('slidingWindow', () => {
  it('returns zeroed window when bucketCount<=0', () => {
    expect(slidingWindow(5, 10, 0)).toEqual({ startIndex: 0, endIndex: 0 });
  });

  it('simple trailing window when enough history exists', () => {
    expect(slidingWindow(7, 5, 10)).toEqual({ startIndex: 3, endIndex: 7 });
  });

  it('clamps at 0 window when cursor is 0 (span==1 boundary)', () => {
    expect(slidingWindow(0, 5, 10)).toEqual({ startIndex: 6, endIndex: 0 });
  });

  it('wraps into the tail of the tape when not enough history and window smaller than remaining', () => {
    // cursor=2, window=5, bucketCount=10: not enough history in [0,2], so wraps.
    expect(slidingWindow(2, 5, 10)).toEqual({ startIndex: 8, endIndex: 2 });
  });

  it('wraps and spans the whole tape when window larger than bucketCount', () => {
    // span = min(10,5) = 5 = bucketCount
    expect(slidingWindow(1, 10, 5)).toEqual({ startIndex: 2, endIndex: 1 });
  });
});

describe('windowIndices', () => {
  it('returns [] when bucketCount<=0', () => {
    expect(windowIndices({ startIndex: 0, endIndex: 0 }, 0)).toEqual([]);
  });

  it('expands a non-wrapped window in order', () => {
    expect(windowIndices({ startIndex: 3, endIndex: 7 }, 10)).toEqual([3, 4, 5, 6, 7]);
  });

  it('expands a wrapped window: tail then head, correct length', () => {
    const result = windowIndices({ startIndex: 8, endIndex: 2 }, 10);
    expect(result).toEqual([8, 9, 0, 1, 2]);
    expect(result).toHaveLength(5);
  });

  it('expands a fully-wrapped window spanning the whole tape', () => {
    const result = windowIndices({ startIndex: 2, endIndex: 1 }, 5);
    expect(result).toEqual([2, 3, 4, 0, 1]);
    expect(result).toHaveLength(5);
  });
});

describe('secondsToMs / msToSeconds', () => {
  it('round-trips whole seconds', () => {
    expect(secondsToMs(5)).toBe(5000);
    expect(msToSeconds(5000)).toBe(5);
  });

  it('msToSeconds floors partial seconds', () => {
    expect(msToSeconds(5999)).toBe(5);
    expect(msToSeconds(0)).toBe(0);
  });
});
