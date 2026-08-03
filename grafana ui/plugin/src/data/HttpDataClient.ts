// Live DataClient backed by the FastAPI backend (default http://127.0.0.1:8000). Talks to the same
// endpoints the sim exposes: /topology, /metrics (VictoriaMetrics/PromQL), /events (Loki), /flows,
// /labels (ground-truth fault timeline) and /faults/* (injection control). Metric queries are
// driven entirely by metricCatalog.ts — the single source of truth for name/promql/group/source.
//
// The Copilot conversation methods have no backend yet, so they forward to a private MockDataClient.
// `setCursor`/`getActiveAlerts` are deliberately NOT implemented: a live backend has its own "now"
// (Date.now()), and callers feature-detect those mock-only hooks.

import type { DataClient } from './DataClient';
import type {
  Capabilities,
  Overview,
  TopologyGraph,
  TopologyLink,
  TelemetryRequest,
  MetricSeries,
  MetricPoint,
  Filters,
  NetworkEvent,
  FlowRecord,
  Incident,
  Prediction,
  Conversation,
  CreateConversationRequest,
  SendMessageRequest,
  SendMessageResponse,
  CopilotFeedbackRequest,
  FaultScenario,
  InjectFaultRequest,
  ChatStreamRequest,
  CopilotEvent,
} from './types';
import { normalizeError } from './errors';
import { METRIC_CATALOG } from './metricCatalog';
import { MockDataClient, TopologyNodeLive } from './MockDataClient';
import { CopilotClient, ChatResult } from './CopilotClient';

// --- Raw backend DTOs -------------------------------------------------------------------------

interface RawTopologyNode {
  id: string;
  role: string;
  site_type?: string;
  vrfs?: string[];
}
interface RawTopologyLink {
  source: string;
  target: string;
  source_if?: string;
  target_if?: string;
}
interface RawTopologyResp {
  nodes: RawTopologyNode[];
  links: RawTopologyLink[];
}

interface PromMetric {
  __name__?: string;
  device?: string;
  interface?: string;
  tunnel?: string;
  vrf?: string;
  [k: string]: string | undefined;
}
interface PromSeries {
  metric: PromMetric;
  values?: Array<[number, string]>; // range mode
  value?: [number, string]; // instant mode
}
interface PromResp {
  result: PromSeries[];
}

interface RawEventRow {
  ts: string;
  device?: string;
  app?: string;
  severity?: string;
  line: string;
}
interface RawFlowRow {
  ts: string;
  device?: string;
  ip_src?: string;
  ip_dst?: string;
  port_src?: number;
  port_dst?: number;
  proto?: string | number;
  bytes?: number;
  packets?: number;
}
interface RawLabelRow {
  scenario_id: string;
  type: string;
  target?: { device?: string; interface?: string };
  severity?: 'low' | 'medium' | 'high' | null;
  t_start: number | string;
  t_impact: number | string;
  t_end: number | string;
  lead_time?: number;
  impact_method?: string;
  device?: string;
}

// --- Helpers ----------------------------------------------------------------------------------

/** Coerce a label timestamp (epoch seconds, epoch ms, or ISO string) to epoch seconds. */
function toEpochS(v: number | string | undefined): number {
  if (v == null) {
    return NaN;
  }
  if (typeof v === 'number') {
    return v > 1e12 ? v / 1000 : v; // ms vs s heuristic
  }
  const parsed = Date.parse(v);
  return Number.isNaN(parsed) ? NaN : parsed / 1000;
}

/** String metric value -> number, with NaN mapped to null (per the DTO contract). */
function num(v: string): number | null {
  const f = parseFloat(v);
  return Number.isNaN(f) ? null : f;
}

function isoOf(epochS: number): string | null {
  return Number.isNaN(epochS) ? null : new Date(epochS * 1000).toISOString();
}

/** Device id -> POP. p1-4 -> pop1, p5-8 -> pop2 (4 P/pop); pe1-2 -> pop1 (2 PE/pop). Else undefined. */
function popOf(id: string): string | undefined {
  const m = /^(pe|p)(\d+)$/.exec(id);
  if (!m) {
    return undefined;
  }
  const n = parseInt(m[2], 10);
  const per = m[1] === 'p' ? 4 : 2;
  return `pop${Math.ceil(n / per)}`;
}

export class HttpDataClient implements DataClient {
  private mock = new MockDataClient();
  private copilot: CopilotClient;

  constructor(
    private baseUrl: string,
    private timeoutMs: number,
    copilotBaseUrl = 'http://127.0.0.1:8100',
    copilotTimeoutMs = 120000
  ) {
    this.copilot = new CopilotClient(copilotBaseUrl, copilotTimeoutMs);
  }

  // --- transport ------------------------------------------------------------------------------

  private async fetchJson<T>(path: string): Promise<T> {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.timeoutMs);
    try {
      const res = await fetch(`${this.baseUrl}${path}`, { signal: controller.signal });
      const body = await res.json().catch(() => undefined);
      if (!res.ok) {
        // FastAPI errors come back as { detail }; hand the raw body to normalizeError.
        throw { status: res.status, ...(body ?? {}) };
      }
      return body as T;
    } catch (e) {
      throw normalizeError(e);
    } finally {
      clearTimeout(timer);
    }
  }

  private async postJson<T>(path: string, payload: unknown): Promise<T> {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.timeoutMs);
    try {
      const res = await fetch(`${this.baseUrl}${path}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
        signal: controller.signal,
      });
      const body = await res.json().catch(() => undefined);
      if (!res.ok) {
        throw { status: res.status, ...(body ?? {}) };
      }
      return body as T;
    } catch (e) {
      throw normalizeError(e);
    } finally {
      clearTimeout(timer);
    }
  }

  private qs(params: Record<string, string | number | undefined>): string {
    const sp = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) {
      if (v !== undefined && v !== '') {
        sp.set(k, String(v));
      }
    }
    const s = sp.toString();
    return s ? `?${s}` : '';
  }

  /** Instant PromQL scalar (e.g. a count(...) query). Returns 0 on any failure or empty result. */
  private async instantScalar(query: string): Promise<number> {
    try {
      const resp = await this.fetchJson<PromResp>(`/metrics${this.qs({ query })}`);
      const v = resp.result?.[0]?.value?.[1];
      const n = v != null ? parseFloat(v) : NaN;
      return Number.isNaN(n) ? 0 : n;
    } catch {
      return 0;
    }
  }

  private async fetchLabels(): Promise<RawLabelRow[]> {
    const resp = await this.fetchJson<{ rows: RawLabelRow[] }>('/labels');
    return resp.rows ?? [];
  }

  private static deviceOf(l: RawLabelRow): string | undefined {
    return l.target?.device ?? l.device;
  }

  // --- topology -------------------------------------------------------------------------------

  async getTopology(filters: Filters): Promise<TopologyGraph> {
    const [topo, labels] = await Promise.all([
      this.fetchJson<RawTopologyResp>('/topology'),
      this.fetchLabels().catch(() => [] as RawLabelRow[]),
    ]);
    const nowS = Date.now() / 1000;

    // device -> live health from the ground-truth timeline.
    const state = new Map<string, 'red' | 'amber'>();
    for (const l of labels) {
      const dev = HttpDataClient.deviceOf(l);
      if (!dev) {
        continue;
      }
      const s = toEpochS(l.t_start);
      const i = toEpochS(l.t_impact);
      const e = toEpochS(l.t_end);
      if (nowS >= i && nowS <= e) {
        state.set(dev, 'red');
      } else if (nowS >= s && nowS < i && state.get(dev) !== 'red') {
        state.set(dev, 'amber');
      }
    }

    // Live /topology gives no pop/parent — derive both from the physical adjacency:
    // p/pe anchor on popOf(id); ce -> its uplink PE (parent) + that PE's pop; host -> its
    // single CE (parent) + that CE's pop. Redundant PEs share a pop, so lowest-id is deterministic.
    const adj = new Map<string, string[]>();
    for (const l of topo.links) {
      (adj.get(l.source) ?? adj.set(l.source, []).get(l.source)!).push(l.target);
      (adj.get(l.target) ?? adj.set(l.target, []).get(l.target)!).push(l.source);
    }
    const roleById = new Map(topo.nodes.map((n) => [n.id, n.role]));
    const popById = new Map<string, string | undefined>();
    const parentById = new Map<string, string | undefined>();
    for (const n of topo.nodes) {
      if (n.role === 'p' || n.role === 'pe') {
        popById.set(n.id, popOf(n.id));
      }
    }
    for (const n of topo.nodes) {
      if (n.role.startsWith('ce')) {
        const pe = Array.from(new Set((adj.get(n.id) ?? []).filter((x) => roleById.get(x) === 'pe'))).sort()[0];
        parentById.set(n.id, pe);
        popById.set(n.id, pe ? popById.get(pe) : popOf(n.id));
      }
    }
    for (const n of topo.nodes) {
      if (n.role === 'host') {
        const ce = (adj.get(n.id) ?? []).find((x) => (roleById.get(x) ?? '').startsWith('ce'));
        parentById.set(n.id, ce);
        popById.set(n.id, ce ? popById.get(ce) : popOf(n.id));
      }
    }

    let nodes: TopologyNodeLive[] = topo.nodes.map((n) => ({
      id: n.id,
      role: n.role,
      siteType: n.site_type,
      pop: popById.get(n.id) ?? popOf(n.id),
      parent: parentById.get(n.id),
      vrfs: n.vrfs,
      state: state.get(n.id) ?? 'green',
    }));
    if (filters.pop) {
      nodes = nodes.filter((n) => n.pop === filters.pop);
    }
    const ids = new Set(nodes.map((n) => n.id));
    const links: TopologyLink[] = topo.links
      .filter((l) => ids.has(l.source) && ids.has(l.target))
      .map((l) => ({
        source: l.source,
        target: l.target,
        sourceIf: l.source_if,
        targetIf: l.target_if,
        kind: 'physical',
      }));
    return { nodes, links };
  }

  // --- telemetry ------------------------------------------------------------------------------

  async getTelemetry(request: TelemetryRequest): Promise<MetricSeries[]> {
    const deviceId = request.deviceId;
    if (!deviceId) {
      return [];
    }
    const toMs = request.timeRange?.toMs ?? Date.now();
    const fromMs = request.timeRange?.fromMs ?? toMs - 15 * 60 * 1000;
    const start = Math.floor(fromMs / 1000);
    const end = Math.floor(toMs / 1000);
    const step = 30;

    const perEntry = await Promise.all(
      METRIC_CATALOG.map(async (desc) => {
        const query = desc.promql.replace(/\$dev/g, deviceId);
        let resp: PromResp;
        try {
          resp = await this.fetchJson<PromResp>(`/metrics${this.qs({ query, start, end, step })}`);
        } catch {
          return [] as MetricSeries[]; // one dead metric must not sink the page
        }
        return (resp.result ?? []).map((s): MetricSeries => {
          const device = s.metric.device ?? deviceId;
          const entity = desc.entityLabel ? s.metric[desc.entityLabel] : undefined;
          const key = `${device}${entity ? `:${entity}` : ''}:${desc.name}`;
          const points: MetricPoint[] = (s.values ?? []).map(([t, v]) => ({ tMs: t * 1000, value: num(v) }));
          return {
            key,
            label: entity ? `${desc.label} (${entity})` : desc.label,
            unit: desc.unit,
            source: desc.source,
            points,
          };
        });
      })
    );
    return perEntry.flat();
  }

  // --- events / flows -------------------------------------------------------------------------

  async getEvents(filters: Filters): Promise<NetworkEvent[]> {
    const start = filters.timeRange ? Math.floor(filters.timeRange.fromMs / 1000) : undefined;
    const end = filters.timeRange ? Math.floor(filters.timeRange.toMs / 1000) : undefined;
    const resp = await this.fetchJson<{ rows: RawEventRow[] }>(
      `/events${this.qs({ device: filters.device, start, end, limit: 1000 })}`
    );
    return (resp.rows ?? [])
      .map((r) => ({ tsMs: Date.parse(r.ts), device: r.device, app: r.app, severity: r.severity, line: r.line }))
      .sort((a, b) => b.tsMs - a.tsMs);
  }

  async getFlows(filters: Filters): Promise<FlowRecord[]> {
    const start = filters.timeRange ? Math.floor(filters.timeRange.fromMs / 1000) : undefined;
    const end = filters.timeRange ? Math.floor(filters.timeRange.toMs / 1000) : undefined;
    const resp = await this.fetchJson<{ rows: RawFlowRow[] }>(
      `/flows${this.qs({ device: filters.device, start, end, limit: 500 })}`
    );
    return (resp.rows ?? [])
      .map((r) => ({
        // Flow ts is space-separated with no timezone — append 'Z' to parse as UTC.
        tsMs: Date.parse(`${r.ts.replace(' ', 'T')}Z`),
        device: r.device,
        ipSrc: r.ip_src,
        ipDst: r.ip_dst,
        portSrc: r.port_src,
        portDst: r.port_dst,
        proto: r.proto,
        bytes: r.bytes,
        packets: r.packets,
      }))
      .sort((a, b) => b.tsMs - a.tsMs);
  }

  // --- incidents / predictions (derived from /labels) -----------------------------------------

  async getIncidents(filters: Filters): Promise<Incident[]> {
    const labels = await this.fetchLabels();
    const nowS = Date.now() / 1000;
    const out: Incident[] = [];
    for (const l of labels) {
      const dev = HttpDataClient.deviceOf(l);
      if (!dev) {
        continue;
      }
      if (filters.device && dev !== filters.device) {
        continue;
      }
      const s = toEpochS(l.t_start);
      const i = toEpochS(l.t_impact);
      const e = toEpochS(l.t_end);
      if (nowS < s) {
        continue; // not begun
      }
      const status: Incident['status'] = nowS < i ? 'open' : nowS < e ? 'active' : 'resolved';
      out.push({
        id: l.scenario_id,
        status,
        faultType: l.type,
        severity: l.severity ?? 'unknown',
        source: 'ground_truth',
        deviceIds: [dev],
        startedAt: isoOf(s) ?? '',
        impactAt: isoOf(i),
        endedAt: isoOf(e),
        summary: `${l.type} on ${dev}`,
        evidence: [
          { label: 'Fault type', detail: l.type, source: 'ground_truth' },
          ...(l.impact_method ? [{ label: 'Impact method', detail: l.impact_method, source: 'ground_truth' as const }] : []),
        ],
        affectedScope: [dev],
        rootCauseHypotheses: [`${l.type} affecting ${dev}`],
        recommendedActions: [{ title: 'Investigate device', detail: `Inspect ${dev} for ${l.type}.` }],
      });
    }
    return out;
  }

  async getPredictions(filters: Filters): Promise<Prediction[]> {
    const labels = await this.fetchLabels();
    const nowS = Date.now() / 1000;
    const out: Prediction[] = [];
    for (const l of labels) {
      const dev = HttpDataClient.deviceOf(l);
      if (!dev) {
        continue;
      }
      if (filters.device && dev !== filters.device) {
        continue;
      }
      const s = toEpochS(l.t_start);
      const i = toEpochS(l.t_impact);
      if (!(nowS >= s && nowS < i)) {
        continue; // only pre-impact windows are "predictions"
      }
      const lead = l.lead_time && l.lead_time > 0 ? l.lead_time : i - s;
      const confidence = lead > 0 ? Math.max(0, Math.min(1, (nowS - s) / lead)) : 0;
      out.push({
        id: l.scenario_id,
        deviceId: dev,
        faultType: l.type,
        confidence,
        timeToImpactSeconds: Math.max(0, i - nowS),
        source: 'mock',
        issuedAtMs: s * 1000,
      });
    }
    return out;
  }

  // --- overview -------------------------------------------------------------------------------

  async getOverview(filters: Filters): Promise<Overview> {
    const [topo, labels, predictions] = await Promise.all([
      this.fetchJson<RawTopologyResp>('/topology').catch(() => ({ nodes: [], links: [] } as RawTopologyResp)),
      this.fetchLabels().catch(() => [] as RawLabelRow[]),
      this.getPredictions(filters).catch(() => [] as Prediction[]),
    ]);
    const nowS = Date.now() / 1000;

    const [reportingDevices, totalTunnels, degradedTunnels] = await Promise.all([
      this.instantScalar('count(count by(device)({device=~".+"}))'),
      this.instantScalar('count(sdwan_tunnel_latency_ms)'),
      this.instantScalar('count(sdwan_tunnel_loss_pct > 1)'),
    ]);

    const activeDevices = new Set<string>();
    for (const l of labels) {
      const dev = HttpDataClient.deviceOf(l);
      if (dev && nowS >= toEpochS(l.t_start) && nowS <= toEpochS(l.t_end)) {
        activeDevices.add(dev);
      }
    }

    let highestRisk: Overview['highestRisk'];
    if (predictions.length > 0) {
      const top = predictions.reduce((b, p) => (p.confidence > b.confidence ? p : b));
      highestRisk = { deviceId: top.deviceId, score: top.confidence };
    }
    const nearestTimeToImpactSeconds =
      predictions.length > 0 ? Math.min(...predictions.map((p) => p.timeToImpactSeconds)) : null;

    return {
      expectedDevices: topo.nodes.length,
      reportingDevices,
      degradedDevices: activeDevices.size,
      totalTunnels,
      degradedTunnels,
      activeIncidents: activeDevices.size,
      highestRisk,
      nearestTimeToImpactSeconds,
    };
  }

  // --- capabilities (must never throw — drives the Lab ON/OFF badge) --------------------------

  async getCapabilities(): Promise<Capabilities> {
    const probe = async (path: string) => {
      try {
        await this.fetchJson(path);
        return true;
      } catch {
        return false;
      }
    };
    // VM drives measured/simulated/modelled; events + flows are probed too so a partial outage is
    // still reflected as "core (VM) up" rather than silently green.
    const [vmOk] = await Promise.all([
      probe(`/metrics${this.qs({ query: 'vector(1)' })}`),
      probe(`/events${this.qs({ limit: 1 })}`),
      probe(`/flows${this.qs({ limit: 1 })}`),
    ]);
    const now = Date.now();
    return {
      sources: {
        measured: vmOk,
        simulated: vmOk,
        modelled: vmOk,
        ground_truth: true,
        mock: false,
        prediction: true,
      },
      datasetWindow: { fromMs: now - 30 * 24 * 60 * 60 * 1000, toMs: now },
    };
  }

  // --- copilot (UI-2 #51: live via CopilotClient; conversation persistence stays mock) --------

  getConversation(id: string): Promise<Conversation> {
    return this.mock.getConversation(id);
  }
  createConversation(request: CreateConversationRequest): Promise<Conversation> {
    return this.mock.createConversation(request);
  }
  // DataClient interface: unchanged (mock). The Copilot page drives the live copilot via streamChat.
  sendMessage(request: SendMessageRequest): Promise<SendMessageResponse> {
    return this.mock.sendMessage(request);
  }
  submitFeedback(request: CopilotFeedbackRequest): Promise<void> {
    return this.mock.submitFeedback(request);
  }

  // --- copilot streaming (beyond the DataClient interface; feature-detected by the page) --------

  streamChat(req: ChatStreamRequest, onEvent: (e: CopilotEvent) => void): Promise<ChatResult> {
    return this.copilot.streamChat(req, onEvent);
  }

  // --- fault injection (extra, beyond the DataClient interface) --------------------------------

  getScenarios(): Promise<FaultScenario[]> {
    return this.fetchJson<{ scenarios?: FaultScenario[] } | FaultScenario[]>('/faults/scenarios').then((r) =>
      Array.isArray(r) ? r : r.scenarios ?? []
    );
  }
  injectFault(req: InjectFaultRequest): Promise<{ scenario_id: string }> {
    return this.postJson<{ scenario_id: string }>('/faults/inject', req);
  }
  getActiveFaults(): Promise<unknown> {
    return this.fetchJson('/faults/active');
  }
  revertFault(scenarioId: string): Promise<unknown> {
    return this.postJson(`/faults/revert/${encodeURIComponent(scenarioId)}`, {});
  }
}
