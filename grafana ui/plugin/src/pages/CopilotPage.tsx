import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { css } from '@emotion/css';
import { useLocation } from 'react-router-dom';
import { PluginPage } from '@grafana/runtime';
import { GrafanaTheme2 } from '@grafana/data';
import { useStyles2, Button, Input } from '@grafana/ui';

import { CopilotChat, ChatItem } from '../components/CopilotChat';
import { useAppState } from '../state/AppContext';
import { useDataClient } from '../data/DataClientContext';
import { Incident, CopilotMessage, CopilotEvent, TraceStep } from '../data/types';
import { ChatResult, stepOf } from '../data/CopilotClient';
import { formatUtc } from '../utils/time';

const SUGGESTIONS = [
  'What is happening on the network right now?',
  'Which devices are affected?',
  'What should I do about it?',
];

const SEVERITY_RANK: Record<string, number> = { high: 3, medium: 2, low: 1, unknown: 0 };
const STATUS_RANK: Record<string, number> = { active: 2, open: 1, resolved: 0, unknown: 0 };

// A live copilot client exposes streamChat; the mock client does not. Feature-detect, exactly like
// the fault-injection / setCursor extras.
interface CopilotStreamer {
  streamChat?: (req: any, onEvent: (e: CopilotEvent) => void) => Promise<ChatResult>;
}

// Picks the most relevant live incident so a free-form answer tracks the current moment.
function topIncident(incidents: Incident[]): Incident | null {
  const live = incidents.filter((i) => i.status === 'active' || i.status === 'open');
  if (live.length === 0) {
    return null;
  }
  return [...live].sort(
    (a, b) =>
      (STATUS_RANK[b.status] ?? 0) - (STATUS_RANK[a.status] ?? 0) ||
      (SEVERITY_RANK[b.severity] ?? 0) - (SEVERITY_RANK[a.severity] ?? 0)
  )[0];
}

// A persisted session so a free-form conversation resumes across reloads (ADR-0009 resumable).
function persistentSessionId(): string {
  const k = 'noc-copilot-session';
  let v = localStorage.getItem(k);
  if (!v) {
    v = (globalThis.crypto?.randomUUID?.() as string) ?? `sess-${Date.now()}`;
    localStorage.setItem(k, v);
  }
  return v;
}

// A one-shot "Explain with Copilot" target parsed from the URL (UI-3 #52). Fresh session, scoped
// window, a context sentence prepended to the auto-asked question.
interface Explain {
  device?: string;
  incident?: string;
  faultType?: string;
  start?: number;
  end?: number;
  question: string;
}

function freshId(prefix: string): string {
  const rnd = (globalThis.crypto?.randomUUID?.() as string) ?? `${Date.now()}-${Math.floor(Math.random() * 1e9)}`;
  return `${prefix}-${rnd}`;
}

function parseExplain(search: string): Explain | null {
  const p = new URLSearchParams(search);
  const device = p.get('device') || undefined;
  const incident = p.get('incident') || undefined;
  if (!device && !incident) {
    return null;
  }
  const faultType = p.get('faultType') || undefined;
  const from = Number(p.get('from'));
  const to = Number(p.get('to'));
  const scope = device ? `device ${device}` : `incident ${incident}`;
  const fault = faultType ? ` (${faultType})` : '';
  return {
    device,
    incident,
    faultType,
    start: from > 0 ? Math.floor(from / 1000) : undefined,
    end: to > 0 ? Math.floor(to / 1000) : undefined,
    question: `Explain what is happening on ${scope}${fault} and why.`,
  };
}

export function CopilotPage() {
  const styles = useStyles2(getStyles);
  const { refreshTick, range } = useAppState();
  const dataClient = useDataClient();
  const location = useLocation();

  const [items, setItems] = useState<ChatItem[]>([]);
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const [context, setContext] = useState<Incident | null>(null);
  const idRef = useRef(0);
  const nextId = () => `msg-${(idRef.current += 1)}`;

  const streamer = dataClient as unknown as CopilotStreamer;
  const explain = useMemo(() => parseExplain(location.search), [location.search]);
  // Fresh session per Explain navigation (isolated context, story 9); persisted id for free-form.
  const sessionId = useMemo(() => (explain ? freshId('explain') : persistentSessionId()), [explain]);

  // Track the live incident for free-form context. Does not touch the conversation thread.
  useEffect(() => {
    let cancelled = false;
    dataClient.getIncidents({}).then((incidents) => {
      if (!cancelled) {
        setContext(topIncident(incidents));
      }
    });
    return () => {
      cancelled = true;
    };
  }, [dataClient, refreshTick]);

  const nowIso = () => formatUtc(Date.now());

  // The window the copilot scopes its tools to: the current UI range (epoch s), or the explain
  // target's frozen window. Zero/empty range -> undefined so the copilot defaults to rolling live.
  const windowOf = useCallback(
    (ex?: Explain | null) => {
      if (ex && (ex.start || ex.end)) {
        return { start: ex.start, end: ex.end };
      }
      return {
        start: range.fromMs > 0 ? Math.floor(range.fromMs / 1000) : undefined,
        end: range.toMs > 0 ? Math.floor(range.toMs / 1000) : undefined,
      };
    },
    [range]
  );

  // One user turn. `existing` set on retry (reuse its id, no duplicate). `ex` set for an Explain
  // turn (fresh session, scoped window). Streams the live trace when the client supports it;
  // falls back to the mock's buffered sendMessage otherwise (mock/offline still works).
  const runSend = useCallback(
    (content: string, existing?: CopilotMessage, ex?: Explain | null) => {
      const win = windowOf(ex);
      const userMsg: CopilotMessage = existing
        ? { ...existing, state: 'sending' }
        : { id: nextId(), role: 'user', content, createdAt: nowIso(), state: 'sending' };
      const botId = nextId();

      setItems((prev) => {
        const without = prev.filter((it) => it.message.id !== userMsg.id);
        return [...without, { message: userMsg }];
      });
      setSending(true);

      const finishUser = (state: CopilotMessage['state']) =>
        setItems((prev) => prev.map((it) => (it.message.id === userMsg.id ? { message: { ...userMsg, state } } : it)));

      if (streamer.streamChat) {
        const trace: TraceStep[] = [];
        // an empty assistant turn that fills in as the trace streams (all-at-once while buffered)
        setItems((prev) => [
          ...prev,
          {
            message: { id: botId, role: 'assistant', content: '', createdAt: nowIso(), state: 'sending' },
            response: { summary: '', affectedScope: [], evidence: [], rootCauseHypotheses: [], recommendedActions: [], citations: [], trace },
          },
        ]);
        const onEvent = (e: CopilotEvent) => {
          const step = stepOf(e); // same mapper the client uses, so the live + final trace can't diverge
          if (step) {
            trace.push(step);
          }
          setItems((prev) =>
            prev.map((it) =>
              it.message.id === botId ? { ...it, response: { ...it.response!, trace: [...trace] } } : it
            )
          );
        };
        streamer
          .streamChat({ question: content, ...win, sessionId, caseId: undefined }, onEvent)
          .then(({ response }) => {
            finishUser('complete');
            setItems((prev) =>
              prev.map((it) =>
                it.message.id === botId
                  ? { message: { id: botId, role: 'assistant', content: response.summary || 'No answer produced.', createdAt: nowIso(), state: 'complete' }, response }
                  : it
              )
            );
          })
          .catch(() => {
            finishUser('error');
            setItems((prev) => prev.filter((it) => it.message.id !== botId));
          })
          .finally(() => setSending(false));
        return;
      }

      // mock fallback: buffered, no live trace.
      dataClient
        .sendMessage({ conversationId: sessionId, message: userMsg, context: context ? { incidentId: context.id, deviceIds: context.deviceIds } : undefined })
        .then((res) => {
          finishUser('complete');
          setItems((prev) => [
            ...prev,
            {
              message: { id: botId, role: 'assistant', content: res.response?.summary ?? 'Nothing critical on the fabric right now.', createdAt: nowIso(), state: 'complete' },
              response: res.response,
            },
          ]);
        })
        .catch(() => finishUser('error'))
        .finally(() => setSending(false));
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [context, dataClient, streamer, sessionId, windowOf]
  );

  // Auto-ask once when arriving via "Explain with Copilot".
  const explainFired = useRef(false);
  useEffect(() => {
    if (explain && !explainFired.current) {
      explainFired.current = true;
      runSend(explain.question, undefined, explain);
    }
  }, [explain, runSend]);

  const submit = () => {
    const text = input.trim();
    if (!text || sending) {
      return;
    }
    setInput('');
    runSend(text);
  };

  const retry = (id: string) => {
    if (sending) {
      return;
    }
    const item = items.find((it) => it.message.id === id);
    if (item) {
      runSend(item.message.content, item.message, explain); // keep the scoped window/session on retry
    }
  };

  const contextLabel = useMemo(
    () =>
      context
        ? `Live incident · ${context.faultType} · ${context.deviceIds.join(', ')}`
        : 'Network nominal — no active incident',
    [context]
  );

  return (
    <PluginPage>
      <h1>Copilot</h1>
      <div className={context ? styles.ctxAlert : styles.ctxOk}>{contextLabel}</div>

      {items.length === 0 ? (
        <div className={styles.suggest}>
          {SUGGESTIONS.map((s) => (
            <Button key={s} variant="secondary" size="sm" disabled={sending} onClick={() => runSend(s)}>
              {s}
            </Button>
          ))}
        </div>
      ) : (
        <CopilotChat items={items} onRetry={retry} />
      )}

      <div className={styles.inputRow}>
        <Input
          placeholder="Ask about the network…"
          value={input}
          disabled={sending}
          onChange={(e) => setInput(e.currentTarget.value)}
          onKeyDown={(e) => e.key === 'Enter' && submit()}
        />
        <Button onClick={submit} disabled={sending || !input.trim()}>
          Send
        </Button>
      </div>
    </PluginPage>
  );
}

const getStyles = (theme: GrafanaTheme2) => ({
  ctxAlert: css`
    margin: ${theme.spacing(1)} 0 ${theme.spacing(2)};
    padding: ${theme.spacing(1)} ${theme.spacing(1.5)};
    border-left: 3px solid ${theme.colors.warning.border};
    background: ${theme.colors.background.secondary};
    color: ${theme.colors.warning.text};
    border-radius: ${theme.shape.radius.default};
  `,
  ctxOk: css`
    margin: ${theme.spacing(1)} 0 ${theme.spacing(2)};
    padding: ${theme.spacing(1)} ${theme.spacing(1.5)};
    border-left: 3px solid ${theme.colors.success.border};
    background: ${theme.colors.background.secondary};
    color: ${theme.colors.text.secondary};
    border-radius: ${theme.shape.radius.default};
  `,
  suggest: css`
    display: flex;
    flex-wrap: wrap;
    gap: ${theme.spacing(1)};
    padding: ${theme.spacing(2)} 0;
  `,
  inputRow: css`
    display: flex;
    gap: ${theme.spacing(1)};
    margin-top: ${theme.spacing(2)};
    position: sticky;
    bottom: 0;
    background: ${theme.colors.background.primary};
    padding: ${theme.spacing(1)} 0;
  `,
});
