// Deterministic time helpers for the demo clock. No Date.now()/argless `new Date()` anywhere —
// every timestamp is derived from fixture metadata + a bucket index (plan decision #1).

/** Bucket metadata as published by the fixture generator (playback tape). */
export interface BucketMeta {
  /** Epoch ms of bucket index 0. */
  startMs: number;
  /** Bucket width in ms (e.g. 30s buckets -> 30000). */
  bucketMs: number;
  /** Total number of buckets in the composite playback tape. */
  bucketCount: number;
}

/** Epoch ms -> UTC "YYYY-MM-DD HH:MM:SSZ", locale-independent. */
export function formatUtc(tsMs: number): string {
  const d = new Date(tsMs);
  const pad = (n: number, width = 2) => String(n).padStart(width, '0');
  const yyyy = d.getUTCFullYear();
  const mm = pad(d.getUTCMonth() + 1);
  const dd = pad(d.getUTCDate());
  const hh = pad(d.getUTCHours());
  const mi = pad(d.getUTCMinutes());
  const ss = pad(d.getUTCSeconds());
  return `${yyyy}-${mm}-${dd} ${hh}:${mi}:${ss}Z`;
}

/** Bucket index -> epoch ms, given fixture bucket metadata. */
export function bucketToTsMs(meta: BucketMeta, index: number): number {
  return meta.startMs + index * meta.bucketMs;
}

export interface SlidingWindow {
  startIndex: number;
  endIndex: number;
}

/**
 * Trailing sliding window over the playback tape, ending at `cursor` (plan decision #1/#4:
 * "trailing ~50-bucket window" so a 100-150 bucket loop never visibly reveals the seam).
 *
 * Normal case: window = [cursor - windowBuckets + 1, cursor], clamped at 0.
 *
 * Wrap case: once looping is enabled and the cursor has wrapped back near the start
 * (cursor < windowBuckets - 1), a plain clamp-at-0 window would visibly shrink every loop
 * (e.g. cursor=2 -> only 3 buckets of data). Since the tape loops seamlessly (composite tape is
 * ordered calm->incidents->calm, see plan decision #4), we instead wrap the window's start into
 * the tail of the tape so it always contains exactly `windowBuckets` samples: the caller is
 * expected to read points modulo bucketCount when startIndex > endIndex.
 */
export function slidingWindow(cursor: number, windowBuckets: number, bucketCount: number): SlidingWindow {
  if (bucketCount <= 0) {
    return { startIndex: 0, endIndex: 0 };
  }
  const endIndex = Math.max(0, Math.min(cursor, bucketCount - 1));
  const span = Math.min(windowBuckets, bucketCount);

  if (endIndex >= span - 1) {
    // Enough history behind the cursor within the tape — simple trailing window.
    return { startIndex: endIndex - span + 1, endIndex };
  }

  // Not enough history yet within [0, endIndex]. If looping, wrap the start into the tail of the
  // tape so the window always has `span` buckets (startIndex > endIndex signals a wrapped window;
  // real indices are `startIndex, startIndex+1, ..., bucketCount-1, 0, 1, ..., endIndex`).
  const wrappedStart = bucketCount - (span - 1 - endIndex);
  return { startIndex: wrappedStart % bucketCount, endIndex };
}

/** Expand a (possibly wrapped) sliding window into concrete bucket indices, in playback order. */
export function windowIndices(window: SlidingWindow, bucketCount: number): number[] {
  if (bucketCount <= 0) {
    return [];
  }
  if (window.startIndex <= window.endIndex) {
    const out: number[] = [];
    for (let i = window.startIndex; i <= window.endIndex; i++) {
      out.push(i);
    }
    return out;
  }
  // Wrapped: startIndex..bucketCount-1, then 0..endIndex.
  const out: number[] = [];
  for (let i = window.startIndex; i < bucketCount; i++) {
    out.push(i);
  }
  for (let i = 0; i <= window.endIndex; i++) {
    out.push(i);
  }
  return out;
}

/** Convert whole seconds to epoch ms. */
export function secondsToMs(seconds: number): number {
  return seconds * 1000;
}

/** Convert epoch ms to whole seconds (floored). */
export function msToSeconds(ms: number): number {
  return Math.floor(ms / 1000);
}
