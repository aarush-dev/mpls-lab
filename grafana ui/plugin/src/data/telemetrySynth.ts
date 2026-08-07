// Deterministic synthetic telemetry generator. Fills devices/series the fixtures leave empty
// (all 148 selectable nodes) and lively values for dead series (flat-0 error counters, all-null
// PE transceivers). Pure function of (deviceId, role, bucketCount): same args -> same output.
//
// Determinism contract (see MockDataClient header): NO Date.now(), NO argless `new Date()`,
// NO Math.random(). All "randomness" is a fixed integer hash of (seed, index).

import metaJson from '../fixtures/meta.json';
import type { MetricSeries, MetricPoint, DataSourceKind } from './types';
import { BucketMeta, bucketToTsMs } from '../utils/time';

// Mock-only stress thresholds — NOT the same scale as the real backend's topologyStyles.STRESS_*
// (kept separate, see tunnelStressAt below). Set above TUNNEL_METRICS' own synthetic ranges
// (latency 20-60ms, jitter 1-8ms, loss 0-2%) so ambient sine noise never trips it: this filler is
// a pure function of (deviceId, bucket), it does not react to injectedFaults, so any threshold
// inside its normal range would eventually false-positive on idle nodes with no fault injected.
const MOCK_STRESS_LATENCY_MS = 70;
const MOCK_STRESS_JITTER_MS = 10;
const MOCK_STRESS_LOSS_PCT = 3;

// Local BucketMeta (mirrors MockDataClient.MOCK_BUCKET_META) — imported here instead of from
// MockDataClient to avoid a circular import. tMs is overwritten by getTelemetry's monotonic
// rewrite anyway, so only startMs/bucketMs shape matters.
const rawMeta = metaJson as unknown as { bucketMs: number; startTsMs: number; buckets: number[] };
const META: BucketMeta = {
  startMs: rawMeta.buckets[0] ?? rawMeta.startTsMs,
  bucketMs: rawMeta.bucketMs,
  bucketCount: rawMeta.buckets.length,
};

type Mode = 'smooth' | 'blip' | 'count';

interface Tmpl {
  kind: string;
  label: (entity: string) => string;
  unit?: string;
  source: DataSourceKind;
  min: number;
  max: number;
  freq: number;
  mode: Mode;
}

// Device-level (no interface segment).
const DEVICE_METRICS: Tmpl[] = [
  { kind: 'cpu_pct', label: () => 'CPU', unit: '%', source: 'measured', min: 10, max: 40, freq: 0.21, mode: 'smooth' },
  { kind: 'mem_pct', label: () => 'Memory', unit: '%', source: 'measured', min: 30, max: 70, freq: 0.13, mode: 'smooth' },
];

const OCTET_METRICS: Tmpl[] = [
  { kind: 'if_in_octets', label: (e) => `Interface RX (${e})`, unit: 'bytes', source: 'measured', min: 1e5, max: 5e5, freq: 0.27, mode: 'smooth' },
  { kind: 'if_out_octets', label: (e) => `Interface TX (${e})`, unit: 'bytes', source: 'measured', min: 1e5, max: 4e5, freq: 0.19, mode: 'smooth' },
];

// Full interface set for routers/spokes (octets + error counters + queue + transceiver).
const IFACE_METRICS_FULL: Tmpl[] = [
  ...OCTET_METRICS,
  { kind: 'if_in_errors', label: (e) => `Interface RX errors (${e})`, unit: 'count', source: 'measured', min: 0, max: 5, freq: 0, mode: 'blip' },
  { kind: 'if_in_discards', label: (e) => `Interface RX discards (${e})`, unit: 'count', source: 'measured', min: 0, max: 5, freq: 0, mode: 'blip' },
  { kind: 'if_out_errors', label: (e) => `Interface TX errors (${e})`, unit: 'count', source: 'measured', min: 0, max: 5, freq: 0, mode: 'blip' },
  { kind: 'if_out_discards', label: (e) => `Interface TX discards (${e})`, unit: 'count', source: 'measured', min: 0, max: 5, freq: 0, mode: 'blip' },
  { kind: 'q_backlog_bytes', label: (e) => `Queue backlog (${e})`, unit: 'bytes', source: 'measured', min: 0, max: 3000, freq: 0.33, mode: 'smooth' },
  { kind: 'q_drops', label: (e) => `Queue drops (${e})`, unit: 'count', source: 'measured', min: 0, max: 30, freq: 0, mode: 'count' },
  { kind: 'xcvr_temp_c', label: (e) => `Transceiver temperature (${e})`, unit: 'C', source: 'modelled', min: 35, max: 45, freq: 0.17, mode: 'smooth' },
  { kind: 'xcvr_rx_power_dbm', label: (e) => `Transceiver RX power (${e})`, unit: 'dBm', source: 'modelled', min: -5, max: -1, freq: 0.23, mode: 'smooth' },
  { kind: 'xcvr_tx_bias_ma', label: (e) => `Transceiver TX bias (${e})`, unit: 'mA', source: 'modelled', min: 25, max: 35, freq: 0.15, mode: 'smooth' },
];

const TUNNEL_METRICS: Tmpl[] = [
  { kind: 'tunnel_latency_ms', label: (e) => `Tunnel latency (${e})`, unit: 'ms', source: 'simulated', min: 20, max: 60, freq: 0.29, mode: 'smooth' },
  { kind: 'tunnel_jitter_ms', label: (e) => `Tunnel jitter (${e})`, unit: 'ms', source: 'simulated', min: 1, max: 8, freq: 0.37, mode: 'smooth' },
  { kind: 'tunnel_loss_pct', label: (e) => `Tunnel loss (${e})`, unit: '%', source: 'simulated', min: 0, max: 2, freq: 0.41, mode: 'smooth' },
];

// Roles whose real fixtures carry a tunnel entity (spoke sites). ce_hub/p/pe have no tunnel.
const SPOKE_ROLES = new Set(['ce_branch', 'ce_dc']);

function hash(a: number, b: number): number {
  let h = (Math.imul(a ^ 0x9e3779b9, 2654435761) ^ Math.imul(b + 1, 40503)) >>> 0;
  h ^= h >>> 15;
  h = Math.imul(h, 2246822519) >>> 0;
  h ^= h >>> 13;
  return h >>> 0;
}

/** Deterministic pseudo-uniform in [0,1) from an integer seed pair. */
function unit01(a: number, b: number): number {
  return (hash(a, b) % 100000) / 100000;
}

/** Char-code sum salt so different metric kinds aren't in phase-lockstep. */
function saltOf(s: string): number {
  return [...s].reduce((a, c) => a + c.charCodeAt(0), 0);
}

function round(v: number, unit?: string): number {
  return unit === 'bytes' ? Math.round(v) : Math.round(v * 1000) / 1000;
}

function valueFor(t: Tmpl, seed: number, i: number): number {
  if (t.mode === 'blip') {
    // Mostly 0 with occasional small integer spikes (1..max) — lively, not flat-0.
    if (unit01(seed, i) < 0.88) {
      return 0;
    }
    return 1 + Math.floor(unit01(seed + 97, i) * t.max);
  }
  if (t.mode === 'count') {
    return Math.round(unit01(seed, i) * t.max) + t.min;
  }
  const phase = ((seed % 100) / 100) * Math.PI * 2;
  const wave = 0.5 + 0.5 * Math.sin(i * t.freq + phase);
  const jitter = (unit01(seed, i) - 0.5) * 0.08; // ±8% deterministic jitter
  const v = t.min + (wave + jitter) * (t.max - t.min);
  return round(Math.min(t.max, Math.max(t.min, v)), t.unit);
}

function buildSeries(deviceId: string, entity: string, key: string, t: Tmpl, bucketCount: number): MetricSeries {
  const seed = saltOf(deviceId) + saltOf(t.kind);
  const points: MetricPoint[] = [];
  for (let i = 0; i < bucketCount; i++) {
    points.push({ tMs: bucketToTsMs(META, i), value: valueFor(t, seed, i) });
  }
  return { key, label: t.label(entity), unit: t.unit, source: t.source, points };
}

/**
 * Full-length synthetic series for a device. Role-aware:
 *  - host: cpu/mem + interface octets only
 *  - router (p/pe/ce_hub/unknown): cpu/mem + full interface set (no tunnel)
 *  - spoke (ce_branch/ce_dc): full interface set + one tunnel entity
 * points.length === bucketCount for every series.
 */
/**
 * True if a spoke device's synthetic tunnel telemetry is over the stress threshold at `bucket`.
 * Threshold scales with `degree` (node connection count) — more-connected nodes have more
 * redundancy, so it takes a bigger excursion per link to call them stressed.
 */
export function tunnelStressAt(deviceId: string, role: string, bucket: number, degree = 1): boolean {
  if (!SPOKE_ROLES.has(role)) {
    return false;
  }
  const latency = TUNNEL_METRICS.find((t) => t.kind === 'tunnel_latency_ms')!;
  const jitter = TUNNEL_METRICS.find((t) => t.kind === 'tunnel_jitter_ms')!;
  const loss = TUNNEL_METRICS.find((t) => t.kind === 'tunnel_loss_pct')!;
  const v = (t: Tmpl) => valueFor(t, saltOf(deviceId) + saltOf(t.kind), bucket);
  const mult = 1 + Math.max(0, degree) / 5;
  return (
    v(latency) > MOCK_STRESS_LATENCY_MS * mult ||
    v(jitter) > MOCK_STRESS_JITTER_MS * mult ||
    v(loss) > MOCK_STRESS_LOSS_PCT * mult
  );
}

export function synthSeries(deviceId: string, role: string, bucketCount: number): MetricSeries[] {
  const out: MetricSeries[] = [];
  for (const t of DEVICE_METRICS) {
    out.push(buildSeries(deviceId, deviceId, `${deviceId}:${t.kind}`, t, bucketCount));
  }
  const iface = 'eth0';
  const ifaceMetrics = role === 'host' ? OCTET_METRICS : IFACE_METRICS_FULL;
  for (const t of ifaceMetrics) {
    out.push(buildSeries(deviceId, iface, `${deviceId}:${iface}:${t.kind}`, t, bucketCount));
  }
  if (SPOKE_ROLES.has(role)) {
    // ponytail: hub peer unknown from deviceId alone; synth entity name only surfaces on
    // uncovered spokes (covered ones keep their real "<id>-<hub>" tunnel key). Good enough.
    const entity = `${deviceId}-hub`;
    for (const t of TUNNEL_METRICS) {
      out.push(buildSeries(deviceId, entity, `${deviceId}:${entity}:${t.kind}`, t, bucketCount));
    }
  }
  return out;
}
