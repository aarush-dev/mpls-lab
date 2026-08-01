// Mock DataClient implementation, backed entirely by the fixture JSONs under ../fixtures (see
// fixtures/README.md for the exact contract this file reads against).
//
// Cursor-aware: `setCursor(n)` is called by App.tsx on every demo-clock tick (state.cursor from
// state/AppContext.tsx). All bucket-indexed reads (overview, topology node coloring, incidents
// status, active predictions, telemetry window) are recomputed from whatever `setCursor` last set.
// A future HttpDataClient talks to a real backend that has its own notion of "now" and will simply
// ignore/no-op `setCursor` (it's not part of the shared `DataClient` interface for that reason —
// callers that need it should feature-detect, e.g. `if ('setCursor' in client) ...`).
//
// Determinism: no `Math.random()`, no bare `Date.now()`/`new Date()`. The only "randomness" is the
// fixed `sleep()` latency below, and all "now" comes from the fixture's bucket clock.

import metaJson from '../fixtures/meta.json';
import topologyJson from '../fixtures/topology.json';
import nodeStatesJson from '../fixtures/nodeStates.json';
import telemetryJson from '../fixtures/telemetry.json';
import incidentsJson from '../fixtures/incidents.json';
import predictionsJson from '../fixtures/predictions.json';
import eventsJson from '../fixtures/events.json';
import flowsJson from '../fixtures/flows.json';
import conversationsJson from '../fixtures/conversations.json';

import type { DataClient } from './DataClient';
import type {
  Capabilities,
  Overview,
  TopologyGraph,
  TopologyNode,
  TopologyLink,
  TelemetryRequest,
  MetricSeries,
  MetricPoint,
  Filters,
  NetworkEvent,
  FlowRecord,
  Incident,
  Evidence,
  RecommendedAction,
  Prediction,
  Conversation,
  CopilotMessage,
  CopilotResponse,
  Citation,
  CreateConversationRequest,
  SendMessageRequest,
  SendMessageResponse,
  CopilotFeedbackRequest,
  DataSourceKind,
} from './types';
import { BucketMeta, bucketToTsMs, secondsToMs, slidingWindow, windowIndices, formatUtc } from '../utils/time';
import { synthSeries } from './telemetrySynth';

// ---------------------------------------------------------------------------------------------
// Raw fixture shapes. JSON module imports widen string-literal fields (e.g. `kind: 'physical'`)
// to plain `string`, so we type the imports loosely here and narrow with small `as` casts only at
// the point each field lands on a domain type that declares a union.
// ---------------------------------------------------------------------------------------------

interface RawMeta {
  bucketMs: number;
  bucketCount: number;
  windowBuckets: number;
  startTsMs: number;
  buckets: number[];
  deviceIds: string[];
  telemetryDeviceIds: string[];
}

interface RawTopologyNode {
  id: string;
  role: string;
  siteType?: string;
  pop?: string;
  parent?: string;
  vrfs?: string[];
}

interface RawTopologyLink {
  source: string;
  target: string;
  sourceIf?: string;
  targetIf?: string;
  kind?: string;
}

interface RawTopology {
  nodes: RawTopologyNode[];
  links: RawTopologyLink[];
}

type RawNodeStates = Record<string, Record<string, string>>;

interface RawMetricPoint {
  tMs: number;
  value: number | null;
}

interface RawMetricSeries {
  key: string;
  label: string;
  unit?: string;
  source: string;
  points: RawMetricPoint[];
}

type RawTelemetry = Record<string, RawMetricSeries[]>;

interface RawEvidence {
  label: string;
  detail: string;
  source: string;
}

interface RawRecommendedAction {
  title: string;
  detail: string;
}

interface RawIncident {
  id: string;
  status: string;
  faultType: string;
  severity: string;
  source: string;
  deviceIds: string[];
  startedAt: string;
  impactAt?: string | null;
  endedAt?: string | null;
  summary: string;
  evidence: RawEvidence[];
  affectedScope: string[];
  rootCauseHypotheses: string[];
  recommendedActions: RawRecommendedAction[];
  startBucket: number;
  impactBucket: number;
  endBucket: number;
}

interface RawPrediction {
  id: string;
  deviceId: string;
  faultType: string;
  confidence: number;
  timeToImpactSeconds: number;
  source: string;
  issuedAtMs: number;
}

interface RawEvent {
  tsMs: number;
  device?: string;
  app?: string;
  severity?: string;
  line: string;
  source: string;
}

interface RawFlow {
  tsMs: number;
  device?: string;
  ipSrc?: string;
  ipDst?: string;
  portSrc?: number;
  portDst?: number;
  proto?: string | number;
  bytes?: number;
  packets?: number;
  source: string;
}

interface RawCitation {
  title: string;
  href: string;
}

interface RawCopilotResponse {
  summary: string;
  predictedIssue?: string;
  confidence?: number;
  timeToImpactSeconds?: number;
  affectedScope: string[];
  evidence: RawEvidence[];
  rootCauseHypotheses: string[];
  recommendedActions: RawRecommendedAction[];
  citations: RawCitation[];
  disclaimer?: string;
}

interface RawCopilotMessage {
  id: string;
  role: string;
  content: string;
  createdAt: string;
  state?: string;
}

interface RawConversation {
  id: string;
  messages: RawCopilotMessage[];
  context?: { deviceIds?: string[]; incidentId?: string | null; timeRange?: { fromMs: number; toMs: number } };
  seedResponse: RawCopilotResponse;
  source: string;
}

const meta = metaJson as unknown as RawMeta;
const topology = topologyJson as unknown as RawTopology;
const nodeStates = nodeStatesJson as unknown as RawNodeStates;
const telemetry = telemetryJson as unknown as RawTelemetry;
const incidents = incidentsJson as unknown as RawIncident[];
const predictions = predictionsJson as unknown as RawPrediction[];
const events = eventsJson as unknown as RawEvent[];
const flows = flowsJson as unknown as RawFlow[];
const conversations = conversationsJson as unknown as RawConversation[];

/** Fixture bucket metadata mapped onto the shared `BucketMeta` shape from utils/time.ts. */
export const MOCK_BUCKET_META: BucketMeta = {
  startMs: meta.buckets[0] ?? meta.startTsMs,
  bucketMs: meta.bucketMs,
  bucketCount: meta.bucketCount,
};

/** Sliding window size (buckets) from the fixture, for wiring into `SET_BOUNDS`. */
export const MOCK_WINDOW_BUCKETS: number = meta.windowBuckets;

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/** Extends TopologyNode with a live-at-cursor health state, without changing the shared type. */
export interface TopologyNodeLive extends TopologyNode {
  state?: 'red' | 'amber' | 'green';
}

function evidenceOf(raw: RawEvidence[]): Evidence[] {
  return raw.map((e) => ({ label: e.label, detail: e.detail, source: e.source as DataSourceKind }));
}

function actionsOf(raw: RawRecommendedAction[]): RecommendedAction[] {
  return raw.map((a) => ({ title: a.title, detail: a.detail }));
}

function citationsOf(raw: RawCitation[]): Citation[] {
  return raw.map((c) => ({ title: c.title, href: c.href }));
}

function copilotResponseOf(raw: RawCopilotResponse): CopilotResponse {
  return {
    summary: raw.summary,
    predictedIssue: raw.predictedIssue,
    confidence: raw.confidence,
    timeToImpactSeconds: raw.timeToImpactSeconds,
    affectedScope: raw.affectedScope,
    evidence: evidenceOf(raw.evidence),
    rootCauseHypotheses: raw.rootCauseHypotheses,
    recommendedActions: actionsOf(raw.recommendedActions),
    citations: citationsOf(raw.citations),
    disclaimer: raw.disclaimer,
  };
}

function messageOf(raw: RawCopilotMessage): CopilotMessage {
  return {
    id: raw.id,
    role: raw.role as CopilotMessage['role'],
    content: raw.content,
    createdAt: raw.createdAt,
    state: raw.state as CopilotMessage['state'],
  };
}

function conversationOf(raw: RawConversation): Conversation {
  return {
    id: raw.id,
    messages: raw.messages.map(messageOf),
    context: raw.context
      ? {
          deviceIds: raw.context.deviceIds,
          incidentId: raw.context.incidentId ?? undefined,
          timeRange: raw.context.timeRange,
        }
      : undefined,
  };
}

/** A prediction is considered "active" at `curTs` for the span [issuedAtMs, issuedAtMs + TTI). */
function isPredictionActive(p: RawPrediction, curTs: number): boolean {
  return p.issuedAtMs <= curTs && curTs < p.issuedAtMs + secondsToMs(p.timeToImpactSeconds);
}

/** Derive an Incident's runtime status from the play-head cursor vs. its raw bucket fields. */
function incidentStatusAt(raw: RawIncident, cursor: number): Incident['status'] | null {
  if (cursor < raw.startBucket) {
    return null; // not yet begun — caller skips these
  }
  if (cursor < raw.impactBucket) {
    return 'open';
  }
  if (cursor < raw.endBucket) {
    return 'active';
  }
  return 'resolved';
}

function incidentOf(raw: RawIncident, status: Incident['status']): Incident {
  return {
    id: raw.id,
    status,
    faultType: raw.faultType,
    severity: raw.severity as Incident['severity'],
    source: raw.source as Incident['source'],
    deviceIds: raw.deviceIds,
    startedAt: raw.startedAt,
    impactAt: raw.impactAt,
    endedAt: raw.endedAt,
    summary: raw.summary,
    evidence: evidenceOf(raw.evidence),
    affectedScope: raw.affectedScope,
    rootCauseHypotheses: raw.rootCauseHypotheses,
    recommendedActions: actionsOf(raw.recommendedActions),
  };
}

function predictionOf(raw: RawPrediction): Prediction {
  return {
    id: raw.id,
    deviceId: raw.deviceId,
    faultType: raw.faultType,
    confidence: raw.confidence,
    timeToImpactSeconds: raw.timeToImpactSeconds,
    source: raw.source as Prediction['source'],
    issuedAtMs: raw.issuedAtMs,
  };
}

// Error-counter metric kinds that the fixture leaves flat-0 on covered devices — treat all-zero
// as "dead" so they get replaced with lively synthetic blips.
const ERROR_KINDS = new Set(['if_in_errors', 'if_in_discards', 'if_out_errors', 'if_out_discards']);

/** Last colon-segment of a series key, e.g. `pe1:CORP:xcvr_temp_c` -> `xcvr_temp_c`. */
function metricKind(key: string): string {
  const parts = key.split(':');
  return parts[parts.length - 1];
}

/** A real series is "dead" if every point is null, or it's an all-zero error counter. */
function isDeadRaw(s: RawMetricSeries): boolean {
  const vals = s.points.map((p) => p.value);
  if (vals.every((v) => v === null)) {
    return true;
  }
  // ponytail: metricKind collides if a device ever has two interfaces sharing a kind; the fixture
  // has one interface entity per device, so kind is unique enough here.
  return ERROR_KINDS.has(metricKind(s.key)) && vals.every((v) => v === 0 || v === null);
}

function rawToSeries(s: RawMetricSeries): MetricSeries {
  return {
    key: s.key,
    label: s.label,
    unit: s.unit,
    source: s.source as DataSourceKind,
    points: s.points.map((p) => ({ tMs: p.tMs, value: p.value })),
  };
}

function matchesDeviceFilter(deviceIds: string[], filters: Filters): boolean {
  if (!filters.device) {
    return true;
  }
  return deviceIds.includes(filters.device);
}

export class MockDataClient implements DataClient {
  private cursor = 0;
  // Ever-increasing display clock (never wraps). Drives only the telemetry time axis (Task A);
  // events/predictions/incidents gating stays on cursor-based curTsMs() below.
  private absTick = 0;
  private convCounter = 0;

  /** Called by App.tsx on every demo-clock tick. HttpDataClient will not implement this. */
  setCursor(n: number): void {
    this.cursor = Math.max(0, Math.min(n, Math.max(0, meta.bucketCount - 1)));
  }

  /** Monotonic display clock from App.tsx (state.absTick). HttpDataClient will not implement this. */
  setAbsTick(n: number): void {
    this.absTick = Math.max(0, n);
  }

  getCursor(): number {
    return this.cursor;
  }

  private curTsMs(): number {
    return bucketToTsMs(MOCK_BUCKET_META, this.cursor);
  }

  private nodeStateAt(deviceId: string): 'red' | 'amber' | 'green' {
    const bucket = nodeStates[String(this.cursor)];
    const state = bucket?.[deviceId];
    return state === 'red' || state === 'amber' ? state : 'green';
  }

  async getCapabilities(): Promise<Capabilities> {
    await sleep(120);
    const sources: Record<DataSourceKind, boolean> = {
      mock: true,
      measured: true,
      simulated: true,
      modelled: true,
      ground_truth: true,
      prediction: true,
    };
    const first = meta.buckets[0] ?? meta.startTsMs;
    const last = meta.buckets[meta.buckets.length - 1] ?? first;
    return {
      sources,
      datasetWindow: { fromMs: first, toMs: last },
    };
  }

  async getOverview(_filters: Filters): Promise<Overview> {
    await sleep(120);
    const cursor = this.cursor;
    const curTs = this.curTsMs();

    const reportingDevices = meta.deviceIds.length;
    const expectedDevices = meta.deviceIds.length;
    const degradedBucket = nodeStates[String(cursor)] ?? {};
    const degradedDevices = Object.keys(degradedBucket).length;

    const tunnelLinks = topology.links.filter((l) => l.kind === 'tunnel');
    const totalTunnels = tunnelLinks.length;
    const degradedTunnels = tunnelLinks.filter((l) => {
      const srcState = this.nodeStateAt(l.source);
      const dstState = this.nodeStateAt(l.target);
      return srcState !== 'green' || dstState !== 'green';
    }).length;

    const activeIncidents = incidents.filter(
      (inc) => cursor >= inc.startBucket && cursor <= inc.endBucket
    ).length;

    const activePredictions = predictions.filter((p) => isPredictionActive(p, curTs));
    let highestRisk: Overview['highestRisk'];
    if (activePredictions.length > 0) {
      const top = activePredictions.reduce((best, p) => (p.confidence > best.confidence ? p : best));
      highestRisk = { deviceId: top.deviceId, score: top.confidence };
    }
    const nearestTimeToImpactSeconds =
      activePredictions.length > 0
        ? Math.min(...activePredictions.map((p) => p.timeToImpactSeconds))
        : null;

    return {
      reportingDevices,
      expectedDevices,
      degradedDevices,
      totalTunnels,
      degradedTunnels,
      activeIncidents,
      highestRisk,
      nearestTimeToImpactSeconds,
    };
  }

  async getTopology(filters: Filters): Promise<TopologyGraph> {
    await sleep(120);
    let nodes: TopologyNodeLive[] = topology.nodes.map((n) => ({
      id: n.id,
      role: n.role,
      siteType: n.siteType,
      pop: n.pop,
      parent: n.parent,
      vrfs: n.vrfs,
      state: this.nodeStateAt(n.id),
    }));
    if (filters.pop) {
      const keep = new Set(nodes.filter((n) => n.pop === filters.pop).map((n) => n.id));
      nodes = nodes.filter((n) => keep.has(n.id));
    }
    const nodeIds = new Set(nodes.map((n) => n.id));
    const links: TopologyLink[] = topology.links
      .filter((l) => nodeIds.has(l.source) && nodeIds.has(l.target))
      .map((l) => ({
        source: l.source,
        target: l.target,
        sourceIf: l.sourceIf,
        targetIf: l.targetIf,
        kind: l.kind as TopologyLink['kind'],
      }));
    return { nodes, links };
  }

  /**
   * Full telemetry series for a device: real fixture series where present, with dead series
   * (all-null, or all-zero error counters) swapped for lively synthetic values and any role-
   * expected metric kinds that are missing filled in. Wholly-uncovered devices are synthesized.
   */
  private telemetryFor(deviceId: string): MetricSeries[] {
    const role = topology.nodes.find((n) => n.id === deviceId)?.role ?? 'host';
    const real = telemetry[deviceId] ?? [];
    const synth = synthSeries(deviceId, role, meta.bucketCount);
    if (real.length === 0) {
      return synth;
    }
    const synthByKey = new Map(synth.map((s) => [s.key, s]));
    const synthByKind = new Map(synth.map((s) => [metricKind(s.key), s]));
    const realKinds = new Set(real.map((s) => metricKind(s.key)));
    const out: MetricSeries[] = real.map((s) => {
      if (isDeadRaw(s)) {
        const repl = synthByKey.get(s.key) ?? synthByKind.get(metricKind(s.key));
        if (repl) {
          // Keep the real series identity (key/label/unit/source), take synth's lively values.
          return { key: s.key, label: s.label, unit: s.unit, source: s.source as DataSourceKind, points: repl.points };
        }
      }
      return rawToSeries(s);
    });
    // Add role-expected series whose kind is absent from real (matched by kind so PE's VRF-named
    // interface isn't duplicated under an eth0 synth key).
    for (const s of synth) {
      if (!realKinds.has(metricKind(s.key))) {
        out.push(s);
      }
    }
    return out;
  }

  async getTelemetry(request: TelemetryRequest): Promise<MetricSeries[]> {
    await sleep(120);
    if (!request.deviceId) {
      return [];
    }
    const series = this.telemetryFor(request.deviceId);
    const window = slidingWindow(this.cursor, meta.windowBuckets, meta.bucketCount);
    const indices = windowIndices(window, meta.bucketCount);
    const n = indices.length;

    return series
      .filter((s) => !request.keys || request.keys.includes(s.key))
      .map((s) => {
        // Rewrite tMs onto the monotonic display timeline (Task A): position p (old->new) ends at
        // absTick, so the axis never rewinds on loop. Values still come from the cursor window.
        const points: MetricPoint[] = indices.map((index, p) => ({
          tMs: bucketToTsMs(MOCK_BUCKET_META, this.absTick - (n - 1 - p)),
          value: s.points[index]?.value ?? null,
        }));
        return {
          key: s.key,
          label: s.label,
          unit: s.unit,
          source: s.source,
          points,
        };
      });
  }

  async getEvents(filters: Filters): Promise<NetworkEvent[]> {
    await sleep(120);
    const curTs = this.curTsMs();
    return events
      .filter((e) => e.tsMs <= curTs)
      .filter((e) => !filters.device || e.device === filters.device)
      .sort((a, b) => b.tsMs - a.tsMs)
      .map((e) => ({ tsMs: e.tsMs, device: e.device, app: e.app, severity: e.severity, line: e.line }));
  }

  async getFlows(filters: Filters): Promise<FlowRecord[]> {
    await sleep(120);
    const curTs = this.curTsMs();
    return flows
      .filter((f) => f.tsMs <= curTs)
      .filter((f) => !filters.device || f.device === filters.device)
      .sort((a, b) => b.tsMs - a.tsMs)
      .map((f) => ({
        tsMs: f.tsMs,
        device: f.device,
        ipSrc: f.ipSrc,
        ipDst: f.ipDst,
        portSrc: f.portSrc,
        portDst: f.portDst,
        proto: f.proto,
        bytes: f.bytes,
        packets: f.packets,
      }));
  }

  async getIncidents(filters: Filters): Promise<Incident[]> {
    await sleep(120);
    const cursor = this.cursor;
    const out: Incident[] = [];
    for (const raw of incidents) {
      const status = incidentStatusAt(raw, cursor);
      if (status === null) {
        continue;
      }
      if (!matchesDeviceFilter(raw.deviceIds, filters)) {
        continue;
      }
      out.push(incidentOf(raw, status));
    }
    return out;
  }

  async getPredictions(filters: Filters): Promise<Prediction[]> {
    await sleep(120);
    const curTs = this.curTsMs();
    return predictions
      .filter((p) => isPredictionActive(p, curTs))
      .filter((p) => !filters.device || p.deviceId === filters.device)
      .map(predictionOf);
  }

  async getConversation(id: string): Promise<Conversation> {
    await sleep(120);
    const found = conversations.find((c) => c.id === id);
    if (!found) {
      throw { status: 404, code: 'not_found', message: `Conversation ${id} not found`, retryable: false };
    }
    return conversationOf(found);
  }

  async createConversation(request: CreateConversationRequest): Promise<Conversation> {
    await sleep(120);
    this.convCounter += 1;
    const id = `conv-mock-${this.convCounter}`;
    const messages: CopilotMessage[] = [];
    if (request.firstMessage) {
      messages.push({
        id: `${id}-u1`,
        role: 'user',
        content: request.firstMessage,
        createdAt: formatUtc(this.curTsMs()),
        state: 'complete',
      });
    }
    return { id, messages, context: request.context };
  }

  async sendMessage(request: SendMessageRequest): Promise<SendMessageResponse> {
    await sleep(120);
    const faultType = this.faultTypeForContext(request.context);
    const seed = conversations.find((c) => c.id === `conv-seed-${faultType}`);

    const sentMessage: CopilotMessage = { ...request.message, state: 'complete' };
    const response = seed ? copilotResponseOf(seed.seedResponse) : undefined;

    return { message: sentMessage, response };
  }

  async submitFeedback(_request: CopilotFeedbackRequest): Promise<void> {
    await sleep(120);
  }

  /** Best-effort fault-type lookup for a conversation's device/incident context at the cursor. */
  private faultTypeForContext(context: SendMessageRequest['context']): string | undefined {
    if (context?.incidentId) {
      const inc = incidents.find((i) => i.id === context.incidentId);
      if (inc) {
        return inc.faultType;
      }
    }
    const deviceId = context?.deviceIds?.[0];
    if (deviceId) {
      const cursor = this.cursor;
      const match = incidents.find(
        (i) => i.deviceIds.includes(deviceId) && cursor >= i.startBucket && cursor <= i.endBucket
      );
      if (match) {
        return match.faultType;
      }
    }
    return undefined;
  }
}
