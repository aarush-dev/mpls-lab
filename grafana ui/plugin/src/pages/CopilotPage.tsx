import React, { useState } from 'react';
import { css } from '@emotion/css';
import { GrafanaTheme2 } from '@grafana/data';
import { PluginPage } from '@grafana/runtime';
import { useStyles2, Button, Input, Spinner, Icon, InlineSwitch } from '@grafana/ui';
import { useCopilotChat } from '../hooks/useCopilotChat';
import { CopilotTrace } from '../components/CopilotTrace';

// T2/#68 + T3/#69: real Copilot answers in the browser. Thin render over `useCopilotChat` — user +
// assistant bubbles, an "investigating…" state while the turn streams, error + Retry on an
// unreachable backend, and a Claude-style collapsed trace (`CopilotTrace`) that renders the event
// cards and the cited answer with citation chips.
export function CopilotPage() {
  const styles = useStyles2(getStyles);
  const { items, sending, send, retry, stop, newChat } = useCopilotChat();
  const [draft, setDraft] = useState('');
  // T6/#71: default OFF -> read-only investigation. Flip on to let the agent run bash + present
  // artifacts (backend still needs a session_id; ADR-0011 decouples the tools from memory).
  const [workspace, setWorkspace] = useState(false);

  const submit = () => {
    const q = draft.trim();
    if (q && !sending) {
      send(q, workspace);
      setDraft('');
    }
  };

  return (
    <PluginPage>
      <div className={styles.toolbar}>
        <InlineSwitch
          label="Workspace"
          showLabel
          transparent
          value={workspace}
          onChange={() => setWorkspace((v) => !v)}
        />
        <Button variant="secondary" size="sm" icon="plus" onClick={newChat}>
          New chat
        </Button>
      </div>
      <div className={styles.thread}>
        {items.map((t) => (
          <div key={t.id}>
            <div className={styles.userRow}>
              <div className={styles.userBubble}>{t.question}</div>
            </div>
            <div className={styles.botRow}>
              <div className={styles.botBubble}>
                {t.state === 'sending' && t.events.length === 0 && (
                  <span className={styles.status}>
                    <Spinner size={12} /> investigating…
                  </span>
                )}
                {t.state !== 'error' && (t.events.length > 0 || t.turn) && (
                  <CopilotTrace events={t.events} turn={t.turn} />
                )}
                {t.state === 'error' && (
                  <span className={styles.error}>
                    <Icon name="exclamation-triangle" /> Couldn’t reach the copilot.
                    <Button variant="secondary" size="sm" onClick={() => retry(t.id)}>
                      Retry
                    </Button>
                  </span>
                )}
                {t.state === 'aborted' && (
                  <span className={styles.status}>
                    stopped.
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
        {sending ? (
          <Button variant="destructive" icon="square-shape" onClick={stop}>
            Stop
          </Button>
        ) : (
          <Button onClick={submit} disabled={!draft.trim()}>
            Send
          </Button>
        )}
      </div>
    </PluginPage>
  );
}

const getStyles = (theme: GrafanaTheme2) => ({
  toolbar: css({ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: theme.spacing(1) }),
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
  error: css({ display: 'inline-flex', alignItems: 'center', gap: theme.spacing(1), color: theme.colors.error.text }),
  composer: css({ display: 'flex', gap: theme.spacing(1) }),
});
