// Deterministic, locale-independent unit formatters. No toLocaleString/Intl — output must be
// identical across environments (mirrors the "no Date.now()" determinism rule for numbers).

function fixed(n: number, digits: number): string {
  // Guard NaN/Infinity so charts never render "NaN <unit>".
  if (!Number.isFinite(n)) {
    return '0';
  }
  return n.toFixed(digits);
}

/** Bits per second -> "12.3 Mbps" style, base-1000. */
export function bps(value: number): string {
  const units = ['bps', 'Kbps', 'Mbps', 'Gbps', 'Tbps'];
  let v = Math.abs(value);
  let i = 0;
  while (v >= 1000 && i < units.length - 1) {
    v /= 1000;
    i++;
  }
  const sign = value < 0 ? '-' : '';
  return `${sign}${fixed(v, i === 0 ? 0 : 2)} ${units[i]}`;
}

/** Milliseconds -> "42 ms" / "1.20 s" for readability above 1000ms. */
export function ms(value: number): string {
  if (Math.abs(value) >= 1000) {
    return `${fixed(value / 1000, 2)} s`;
  }
  return `${fixed(value, 0)} ms`;
}

/** Fraction (0..1) or already-percent value -> "12.3%". Treats |value| <= 1 as a fraction. */
export function pct(value: number, digits = 1): string {
  const v = Math.abs(value) <= 1 ? value * 100 : value;
  return `${fixed(v, digits)}%`;
}

/** Bytes -> IEC units (KiB/MiB/...), base-1024. */
export function bytes(value: number): string {
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB'];
  let v = Math.abs(value);
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  const sign = value < 0 ? '-' : '';
  return `${sign}${fixed(v, i === 0 ? 0 : 2)} ${units[i]}`;
}

/** Plain integer count with no separators (deterministic; avoids locale-dependent grouping). */
export function count(value: number): string {
  return `${Math.round(value)}`;
}

/** Whole seconds -> compact ETA string, e.g. 200 -> "3m 20s", 45 -> "45s", 3725 -> "1h 2m 5s". */
export function secondsToEta(totalSeconds: number): string {
  const s = Math.max(0, Math.round(totalSeconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;

  const parts: string[] = [];
  if (h > 0) {
    parts.push(`${h}h`);
  }
  if (h > 0 || m > 0) {
    parts.push(`${m}m`);
  }
  parts.push(`${sec}s`);
  return parts.join(' ');
}
