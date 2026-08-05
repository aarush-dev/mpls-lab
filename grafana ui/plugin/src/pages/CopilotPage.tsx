import React, { useState } from 'react';
import { css } from '@emotion/css';
import { GrafanaTheme2 } from '@grafana/data';
import { PluginPage } from '@grafana/runtime';
import { useStyles2, Button, Input, Spinner, Icon } from '@grafana/ui';
import { useCopilotChat } from '../hooks/useCopilotChat';

// T2/#68: first real Copilot answer in the browser. Thin render over `useCopilotChat` — user +
// assistant bubbles, an "investigating…" state while the turn streams, and error + Retry on an
// unreachable backend. Cited answer is plain text (chips/trace cards are later #66 tickets).
export function CopilotPage() {
  const styles = useStyles2(getStyles);
  const { items, sending, send, retry } = useCopilotChat();
  const [draft, setDraft] = useState('');

  const submit = () => {
    const q = draft.trim();
    if (q && !sending) {
      send(q);
      setDraft('');
    }
  };

  return (
    <PluginPage>
      <div className={styles.thread}>
        {items.map((t) => (
          <div key={t.id}>
            <div className={styles.userRow}>
              <div className={styles.userBubble}>{t.question}</div>
            </div>
            <div className={styles.botRow}>
              <div className={styles.botBubble}>
                {t.state === 'sending' && (
                  <span className={styles.status}>
                    <Spinner size={12} /> investigating…
                  </span>
                )}
                {t.state === 'done' && <span className={styles.answer}>{t.turn?.answer}</span>}
                {t.state === 'error' && (
                  <span className={styles.error}>
                    <Icon name="exclamation-triangle" /> Couldn’t reach the copilot.
                    <Button variant="secondary" size="sm" onClick={() => retry(t.id)}>
                      Retry
                    </Button>
                  </span>
                )}
              </div>
            </div>
          </div>
        ))}
      </div>
      <div className={styles.composer}>
        <Input
          value={draft}
          placeholder="Ask about the network…"
          onChange={(e) => setDraft(e.currentTarget.value)}
          onKeyDown={(e) => e.key === 'Enter' && !e.nativeEvent.isComposing && submit()}
        />
        <Button onClick={submit} disabled={sending || !draft.trim()}>
          Send
        </Button>
      </div>
    </PluginPage>
  );
}

const getStyles = (theme: GrafanaTheme2) => ({
  thread: css({ display: 'flex', flexDirection: 'column', gap: theme.spacing(1), marginBottom: theme.spacing(2) }),
  userRow: css({ display: 'flex', justifyContent: 'flex-end' }),
  botRow: css({ display: 'flex', justifyContent: 'flex-start' }),
  userBubble: css({
    background: theme.colors.primary.main,
    color: theme.colors.primary.contrastText,
    padding: theme.spacing(1, 1.5),
    borderRadius: theme.shape.radius.default,
    maxWidth: '70%',
  }),
  botBubble: css({
    background: theme.colors.background.secondary,
    padding: theme.spacing(1, 1.5),
    borderRadius: theme.shape.radius.default,
    maxWidth: '70%',
  }),
  status: css({ display: 'inline-flex', alignItems: 'center', gap: theme.spacing(1), color: theme.colors.text.secondary }),
  answer: css({ whiteSpace: 'pre-wrap' }),
  error: css({ display: 'inline-flex', alignItems: 'center', gap: theme.spacing(1), color: theme.colors.error.text }),
  composer: css({ display: 'flex', gap: theme.spacing(1) }),
});
