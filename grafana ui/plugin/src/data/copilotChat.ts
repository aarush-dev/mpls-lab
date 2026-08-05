// Pure copilot-chat transport helpers: SSE frame splitting + trace-event → turn folding.
// Both are side-effect-free so they're unit-testable without a live backend (T1/#67). The backend
// streams ADR-0009 `event_wire` dicts as Server-Sent Events; these turn that byte stream into a
// `CopilotTurn`. No fetch/DOM here — HttpDataClient.chat owns the ReadableStream reader.

import type { ChatEvent, AssistantMsgEvent, GateEvent, ToolResultEvent, CopilotTurn, TurnCitation } from './types';

/**
 * Incremental SSE frame splitter. Feed the FULL accumulated buffer each call; returns every
 * COMPLETE frame's concatenated `data:` payload plus the unconsumed tail (a partial frame) to
 * carry forward into the next read. Frames are `\n\n`-delimited (SSE). A blank keep-alive (a frame
 * with no `data:` line) yields no payload and is dropped. Pure: caller owns the buffer.
 */
export function parseSseFrames(buffer: string): { frames: string[]; rest: string } {
  const parts = buffer.replace(/\r\n/g, '\n').split('\n\n'); // tolerate CRLF from a proxy — else no frame ever completes

  const rest = parts.pop() ?? ''; // trailing piece has no terminating blank line yet — incomplete
  const frames: string[] = [];
  for (const block of parts) {
    const data = block
      .split('\n')
      .filter((l) => l.startsWith('data:'))
      .map((l) => l.slice(5).replace(/^ /, '')) // one optional leading space per the SSE grammar
      .join('\n');
    if (data) {
      frames.push(data); // no data line (keep-alive / blank) → nothing to push
    }
  }
  return { frames, rest };
}

// Evidence id shape the tools emit and the answer cites: [source:offset] e.g. [metrics:0], [events:2].
const CITE_RE = /\[([a-z_]+):(\d+)\]/g;

/**
 * Fold a trace-event list into a `CopilotTurn`: the LAST assistant_msg is the answer (final wins;
 * a clarifying ask-back is just an assistant_msg with no citations), `[source:offset]` cites are
 * extracted from the answer and deduped in first-appearance order, each mapped to the tool_result
 * event whose observation carries that id, and the LAST gate outcome is surfaced.
 */
export function mapEventsToTurn(events: ChatEvent[]): CopilotTurn {
  const answer =
    [...events].reverse().find((e): e is AssistantMsgEvent => e.type === 'assistant_msg')?.content ?? '';
  const gate = [...events].reverse().find((e): e is GateEvent => e.type === 'gate');
  const toolResults = events.filter((e): e is ToolResultEvent => e.type === 'tool_result');

  const citations: TurnCitation[] = [];
  const citeMap: Record<string, ToolResultEvent> = {};
  const seen = new Set<string>();
  for (const m of answer.matchAll(CITE_RE)) {
    const id = `${m[1]}:${m[2]}`;
    if (seen.has(id)) {
      continue;
    }
    seen.add(id);
    citations.push({ id, source: m[1], offset: parseInt(m[2], 10) });
    const src = toolResults.find((t) => t.content.includes(`[${id}]`));
    if (src) {
      citeMap[id] = src;
    }
  }
  return { events, answer, citations, citeMap, gate };
}
