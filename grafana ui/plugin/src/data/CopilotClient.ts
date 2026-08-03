// UI-2 (#51): the one place coupled to the copilot's HTTP surface. Reads POST /chat's SSE trace
// (buffered today, streamed later — same framing) and maps the ADR-0009 event enum to the
// structured shape the UI renders. Composed by HttpDataClient; the mock stays untouched.
import { normalizeError } from './errors';
import {
  ChatStreamRequest,
  Citation,
  CopilotEvent,
  CopilotResponse,
  Evidence,
  GateVerdict,
  TraceStep,
} from './types';

const CITATION_RE = /\[([a-z_]+):(\d+)\]/g;
const EVIDENCE_MAX = 240;

export interface ChatResult {
  response: CopilotResponse;
  trace: TraceStep[];
}

export class CopilotClient {
  constructor(private baseUrl: string, private timeoutMs: number) {}

  /**
   * Drive one chat turn. Calls `onEvent` for each trace event as it arrives, and resolves with the
   * assembled result. Works whether the body arrives in one buffered chunk or streamed frame by
   * frame — the `\n\n` framing is identical either way.
   */
  async streamChat(req: ChatStreamRequest, onEvent: (e: CopilotEvent) => void): Promise<ChatResult> {
    const controller = new AbortController();
    let timer = setTimeout(() => controller.abort(), this.timeoutMs);
    const body = JSON.stringify({
      question: req.question,
      start: req.start,
      end: req.end,
      skills: req.skills,
      session_id: req.sessionId,
      case_id: req.caseId,
    });

    const events: CopilotEvent[] = [];
    try {
      const res = await fetch(`${this.baseUrl}/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body,
        signal: controller.signal,
      });
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        throw normalizeError({ status: res.status, ...detail });
      }

      const reader = res.body?.getReader?.();
      const dec = new TextDecoder();
      let buf = '';
      const drainFrames = (flush = false) => {
        let sep;
        while ((sep = buf.indexOf('\n\n')) !== -1) {
          const frame = buf.slice(0, sep);
          buf = buf.slice(sep + 2);
          const evt = parseFrame(frame);
          if (evt) {
            events.push(evt);
            onEvent(evt);
          }
        }
        if (flush) {
          const evt = parseFrame(buf);
          if (evt) {
            events.push(evt);
            onEvent(evt);
          }
          buf = '';
        }
      };

      if (reader) {
        for (;;) {
          const { done, value } = await reader.read();
          clearTimeout(timer);
          if (done) {
            break;
          }
          timer = setTimeout(() => controller.abort(), this.timeoutMs); // idle timeout, re-armed per read
          buf += dec.decode(value, { stream: true });
          drainFrames();
        }
        drainFrames(true);
      } else {
        // no streaming body (jsdom / a proxy that buffers): parse the whole text at once.
        buf = await res.text();
        drainFrames(true);
      }
    } catch (e) {
      throw normalizeError(e);
    } finally {
      clearTimeout(timer);
    }

    const response = assemble(events);
    return { response, trace: response.trace ?? [] };
  }
}

function parseFrame(frame: string): CopilotEvent | null {
  const line = frame.trim();
  if (!line.startsWith('data:')) {
    return null;
  }
  const payload = line.slice(5).trim();
  if (!payload) {
    return null;
  }
  try {
    return JSON.parse(payload) as CopilotEvent;
  } catch {
    return null;
  }
}

/** Map one canonical event to a trace step (null for non-trace events). Shared with CopilotPage's
 * live incremental append so the two paths can't diverge. */
export function stepOf(e: CopilotEvent): TraceStep | null {
  switch (e.type) {
    case 'think':
      return { kind: 'think', ts: e.ts, content: e.content };
    case 'tool_call':
      return { kind: 'tool_call', ts: e.ts, name: e.name, arguments: e.arguments, id: e.id };
    case 'tool_result':
      return { kind: 'tool_result', ts: e.ts, name: e.name, id: e.id, content: e.content, n: e.n };
    case 'gate':
      return { kind: 'gate', ts: e.ts, gate: { ok: e.ok, missing: e.missing ?? [], retry: e.retry ?? 0 } };
    default:
      return null;
  }
}

function toTrace(events: CopilotEvent[]): TraceStep[] {
  return events.map(stepOf).filter((s): s is TraceStep => s !== null);
}

/** Devices the agent actually queried — pulled from tool_call `device` args, deduped, in order. */
function devicesOf(events: CopilotEvent[]): string[] {
  const seen = new Set<string>();
  for (const e of events) {
    if (e.type === 'tool_call') {
      const dev = (e.arguments as { device?: unknown } | undefined)?.device;
      if (typeof dev === 'string' && dev) {
        seen.add(dev);
      }
    }
  }
  return [...seen];
}

function assemble(events: CopilotEvent[]): CopilotResponse {
  const answers = events.filter((e): e is Extract<CopilotEvent, { type: 'assistant_msg' }> => e.type === 'assistant_msg');
  const summary = answers.length ? answers[answers.length - 1].content : '';

  const evidence: Evidence[] = events
    .filter((e): e is Extract<CopilotEvent, { type: 'tool_result' }> => e.type === 'tool_result')
    .map((e) => ({
      label: e.name ?? 'tool',
      detail: truncate(e.content) + (e.n != null ? ` (${e.n} rows)` : ''),
      source: 'modelled',
    }));

  const gates = events.filter((e): e is Extract<CopilotEvent, { type: 'gate' }> => e.type === 'gate');
  const gateVerdict: GateVerdict | undefined = gates.length
    ? {
        ok: gates[gates.length - 1].ok,
        missing: gates[gates.length - 1].missing ?? [],
        retry: Math.max(...gates.map((g) => g.retry ?? 0)),
      }
    : undefined;

  return {
    summary,
    affectedScope: devicesOf(events),
    evidence,
    rootCauseHypotheses: [],
    recommendedActions: [],
    citations: citationsOf(summary),
    trace: toTrace(events),
    gateVerdict,
  };
}

function citationsOf(prose: string): Citation[] {
  const seen = new Set<string>();
  const out: Citation[] = [];
  for (const m of prose.matchAll(CITATION_RE)) {
    const title = `[${m[1]}:${m[2]}]`;
    if (!seen.has(title)) {
      seen.add(title);
      out.push({ title, href: '' });
    }
  }
  return out;
}

function truncate(s: string): string {
  return s.length > EVIDENCE_MAX ? s.slice(0, EVIDENCE_MAX) + '…' : s;
}
