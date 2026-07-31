import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { css } from '@emotion/css';
import { PluginPage } from '@grafana/runtime';
import { GrafanaTheme2 } from '@grafana/data';
import { useStyles2, Button, Input } from '@grafana/ui';

import { CopilotChat, ChatItem } from '../components/CopilotChat';
import { useAppState } from '../state/AppContext';
import { useDataClient } from '../data/DataClientContext';
import { Incident, CopilotMessage } from '../data/types';
import { MOCK_BUCKET_META } from '../data/MockDataClient';
import { bucketToTsMs, formatUtc } from '../utils/time';

const CONV_ID = 'conv-live';
const SUGGESTIONS = [
  'What is happening on the network right now?',
  'Which devices are affected?',
  'What should I do about it?',
];

const SEVERITY_RANK: Record<string, number> = { high: 3, medium: 2, low: 1, unknown: 0 };
const STATUS_RANK: Record<string, number> = { active: 2, open: 1, resolved: 0, unknown: 0 };

// Picks the most relevant live incident so the copilot's answer tracks the same moment as the
// shared demo clock (M4 gate: replies in-sync with the current incident).
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

export function CopilotPage() {
  const styles = useStyles2(getStyles);
  const { cursor } = useAppState();
  const dataClient = useDataClient();

  const [items, setItems] = useState<ChatItem[]>([]);
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const [context, setContext] = useState<Incident | null>(null);
  const idRef = useRef(0);
  const nextId = () => `msg-${(idRef.current += 1)}`;

  // Track the live incident for context. Does not touch the conversation thread.
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
  }, [dataClient, cursor]);

  const nowIso = () => formatUtc(bucketToTsMs(MOCK_BUCKET_META, cursor));

  // Runs the send state machine for one user turn. `existing` is set when retrying a failed turn
  // (reuse its id so the thread doesn't duplicate — client-generated ids, no dup on retry).
  const runSend = useCallback(
    (content: string, existing?: CopilotMessage) => {
      const ctx = context ? { incidentId: context.id, deviceIds: context.deviceIds } : undefined;
      const userMsg: CopilotMessage = existing
        ? { ...existing, state: 'sending' }
        : { id: nextId(), role: 'user', content, createdAt: nowIso(), state: 'sending' };

      setItems((prev) => {
        const without = prev.filter((it) => it.message.id !== userMsg.id);
        return [...without, { message: userMsg }];
      });
      setSending(true);

      dataClient
        .sendMessage({ conversationId: CONV_ID, message: userMsg, context: ctx })
        .then((res) => {
          setItems((prev) => {
            const next: ChatItem[] = prev.map((it) =>
              it.message.id === userMsg.id ? { message: { ...userMsg, state: 'complete' as const } } : it
            );
            if (res.response) {
              next.push({
                message: {
                  id: nextId(),
                  role: 'assistant',
                  content: res.response.summary,
                  createdAt: nowIso(),
                  state: 'complete',
                },
                response: res.response,
              });
            } else {
              next.push({
                message: {
                  id: nextId(),
                  role: 'assistant',
                  content:
                    'Nothing critical on the fabric at this moment. Ask again when an incident is active, or pick a device to inspect.',
                  createdAt: nowIso(),
                  state: 'complete',
                },
              });
            }
            return next;
          });
        })
        .catch(() => {
          setItems((prev) =>
            prev.map((it) => (it.message.id === userMsg.id ? { message: { ...userMsg, state: 'error' as const } } : it))
          );
        })
        .finally(() => setSending(false));
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [context, dataClient, cursor]
  );

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
      runSend(item.message.content, item.message);
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
