import { bps, ms, pct, bytes, count, secondsToEta } from './format';

describe('bps', () => {
  it('formats 0 with no decimals at bps scale', () => {
    expect(bps(0)).toBe('0 bps');
  });

  it('formats into Kbps with 2 decimals once >= 1000', () => {
    expect(bps(1500)).toBe('1.50 Kbps');
  });

  it('handles negative values and multi-step scaling', () => {
    expect(bps(-2000000)).toBe('-2.00 Mbps');
  });
});

describe('ms', () => {
  it('formats sub-1000ms with 0 decimals', () => {
    expect(ms(500)).toBe('500 ms');
  });

  it('formats >=1000ms as seconds with 2 decimals', () => {
    expect(ms(1500)).toBe('1.50 s');
  });

  it('formats negative >=1000ms as seconds', () => {
    expect(ms(-2000)).toBe('-2.00 s');
  });
});

describe('pct', () => {
  it('treats |value|<=1 as a fraction, default 1 digit', () => {
    expect(pct(0.123)).toBe('12.3%');
  });

  it('treats |value|>1 as already-percent', () => {
    expect(pct(45)).toBe('45.0%');
  });

  it('respects a custom digits argument', () => {
    expect(pct(0.5, 0)).toBe('50%');
  });

  it('treats exactly 1 as a fraction (boundary)', () => {
    expect(pct(1)).toBe('100.0%');
  });
});

describe('bytes', () => {
  it('formats sub-1024 with 0 decimals', () => {
    expect(bytes(500)).toBe('500 B');
  });

  it('formats into KiB with 2 decimals', () => {
    expect(bytes(1536)).toBe('1.50 KiB');
  });

  it('handles negative values and multi-step IEC scaling', () => {
    expect(bytes(-2097152)).toBe('-2.00 MiB');
  });
});

describe('count', () => {
  it('rounds to nearest integer with no separators', () => {
    expect(count(3.7)).toBe('4');
  });

  it('rounds negative halves toward +Infinity like Math.round', () => {
    expect(count(-3.5)).toBe('-3');
  });
});

describe('secondsToEta', () => {
  it('formats 0 as "0s"', () => {
    expect(secondsToEta(0)).toBe('0s');
  });

  it('formats 59 as "59s" (no minutes shown)', () => {
    expect(secondsToEta(59)).toBe('59s');
  });

  it('formats 90 as "1m 30s"', () => {
    expect(secondsToEta(90)).toBe('1m 30s');
  });

  it('formats 3660 as "1h 1m 0s"', () => {
    expect(secondsToEta(3660)).toBe('1h 1m 0s');
  });

  it('formats 3725 as "1h 2m 5s"', () => {
    expect(secondsToEta(3725)).toBe('1h 2m 5s');
  });

  it('clamps negative input to 0', () => {
    expect(secondsToEta(-5)).toBe('0s');
  });
});
