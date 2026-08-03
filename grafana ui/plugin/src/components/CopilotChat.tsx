import React from 'react';
import { css } from '@emotion/css';
import { GrafanaTheme2 } from '@grafana/data';
import { useStyles2, Button, Icon, Spinner } from '@grafana/ui';

import { CopilotMessage, CopilotResponse } from '../data/types';
import { nodeDetailPath } from '../constants';

export interface ChatItem {
  message: CopilotMessage;
  response?: CopilotResponse;
}

interface Props {
  items: ChatItem[];
  onRetry: (id: string) => void;
}

// Presentational chat thread. Renders each turn plus, for assistant turns, a structured response
// card that keeps evidence / hypotheses / actions / citations visually separate from the prose.
export function CopilotChat({ items, onRetry }: Props) {
  const styles = useStyles2(getStyles);
  return (
    <div className={styles.thread}>
      {items.map((item) => (
        <div key={item.message.id} className={item.message.role === 'user' ? styles.userRow : styles.botRow}>
          <div className={item.message.role === 'user' ? styles.userBubble : styles.botBubble}>
            <div>{item.message.content}</div>

            {item.message.state === 'sending' && (
              <div className={styles.status}>
                <Spinner size={12} /> thinking…
              </div>
            )}
            {item.message.state === 'error' && (
              <div className={styles.errorRow}>
                <Icon name="exclamation-triangle" /> Couldn’t reach the copilot.
                <Button variant="secondary" size="sm" onClick={() => onRetry(item.message.id)}>
                  Retry
                </Button>
              </div>
            )}

            {item.response && <ResponseCard response={item.response} />}
          </div>
        </div>
      ))}
    </div>
  );
}

function ResponseCard({ response }: { response: CopilotResponse }) {
  const styles = useStyles2(getStyles);
  const r = response;
  return (
    <div className={styles.card}>
      {(r.predictedIssue || r.confidence != null) && (
        <div className={styles.predict}>
          {r.predictedIssue && <strong>{r.predictedIssue}</strong>}
          {r.confidence != null && <span className={styles.conf}>{(r.confidence * 100).toFixed(0)}% confidence</span>}
          {r.timeToImpactSeconds != null && <span className={styles.conf}>~{Math.round(r.timeToImpactSeconds / 60)}m to impact</span>}
        </div>
      )}

      {r.affectedScope.length > 0 && (
        <div className={styles.scope}>
          {r.affectedScope.map((s) => (
            <a key={s} className={styles.chip} href={nodeDetailPath(s)}>
              {s}
            </a>
          ))}
        </div>
      )}

      {r.evidence.length > 0 && (
        <Section title="Evidence">
          <ul className={styles.list}>
            {r.evidence.map((e, i) => (
              <li key={i}>
                <strong>{e.label}:</strong> {e.detail}
              </li>
            ))}
          </ul>
        </Section>
      )}

      {r.rootCauseHypotheses.length > 0 && (
        <Section title="Likely cause">
          <ul className={styles.list}>
            {r.rootCauseHypotheses.map((h, i) => (
              <li key={i}>{h}</li>
            ))}
          </ul>
        </Section>
      )}

      {r.recommendedActions.length > 0 && (
        <Section title="Recommended actions">
          <ol className={styles.list}>
            {r.recommendedActions.map((a, i) => (
              <li key={i}>
                <strong>{a.title}.</strong> {a.detail}
              </li>
            ))}
          </ol>
        </Section>
      )}

      {r.citations.length > 0 && (
        <Section title="Sources">
          <ul className={styles.citations}>
            {r.citations.map((c, i) => (
              <li key={i}>
                <Icon name="document-info" size="sm" /> {c.title}
              </li>
            ))}
          </ul>
        </Section>
      )}

      {r.disclaimer && <div className={styles.disclaimer}>{r.disclaimer}</div>}
    </div>
  );
}

function Section({ title, children }: React.PropsWithChildren<{ title: string }>) {
  const styles = useStyles2(getStyles);
  return (
    <div className={styles.section}>
      <div className={styles.sectionTitle}>{title}</div>
      {children}
    </div>
  );
}

const getStyles = (theme: GrafanaTheme2) => ({
  thread: css`
    display: flex;
    flex-direction: column;
    gap: ${theme.spacing(1.5)};
    padding: ${theme.spacing(1)} 0;
  `,
  userRow: css`
    display: flex;
    justify-content: flex-end;
  `,
  botRow: css`
    display: flex;
    justify-content: flex-start;
  `,
  userBubble: css`
    max-width: 78%;
    padding: ${theme.spacing(1)} ${theme.spacing(1.5)};
    background: ${theme.colors.primary.main};
    color: ${theme.colors.primary.contrastText};
    border-radius: ${theme.shape.radius.default};
  `,
  botBubble: css`
    max-width: 78%;
    padding: ${theme.spacing(1)} ${theme.spacing(1.5)};
    background: ${theme.colors.background.secondary};
    border: 1px solid ${theme.colors.border.weak};
    border-radius: ${theme.shape.radius.default};
  `,
  status: css`
    display: flex;
    align-items: center;
    gap: ${theme.spacing(0.5)};
    margin-top: ${theme.spacing(0.5)};
    color: ${theme.colors.text.secondary};
    font-size: ${theme.typography.bodySmall.fontSize};
  `,
  errorRow: css`
    display: flex;
    align-items: center;
    gap: ${theme.spacing(1)};
    margin-top: ${theme.spacing(0.5)};
    color: ${theme.colors.error.text};
    font-size: ${theme.typography.bodySmall.fontSize};
  `,
  card: css`
    margin-top: ${theme.spacing(1)};
    padding-top: ${theme.spacing(1)};
    border-top: 1px solid ${theme.colors.border.weak};
    display: flex;
    flex-direction: column;
    gap: ${theme.spacing(1)};
  `,
  predict: css`
    display: flex;
    flex-wrap: wrap;
    align-items: baseline;
    gap: ${theme.spacing(1)};
  `,
  conf: css`
    color: ${theme.colors.text.secondary};
    font-size: ${theme.typography.bodySmall.fontSize};
  `,
  scope: css`
    display: flex;
    flex-wrap: wrap;
    gap: ${theme.spacing(0.5)};
  `,
  chip: css`
    font-family: ${theme.typography.fontFamilyMonospace};
    font-size: ${theme.typography.bodySmall.fontSize};
    padding: 1px ${theme.spacing(0.75)};
    background: ${theme.colors.background.primary};
    border: 1px solid ${theme.colors.border.weak};
    border-radius: ${theme.shape.radius.default};
  `,
  section: css`
    display: flex;
    flex-direction: column;
    gap: ${theme.spacing(0.25)};
  `,
  sectionTitle: css`
    font-size: ${theme.typography.bodySmall.fontSize};
    font-weight: ${theme.typography.fontWeightMedium};
    color: ${theme.colors.text.secondary};
    text-transform: uppercase;
    letter-spacing: 0.04em;
  `,
  list: css`
    margin: 0;
    padding-left: ${theme.spacing(2.5)};
    display: flex;
    flex-direction: column;
    gap: ${theme.spacing(0.25)};
  `,
  citations: css`
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: ${theme.spacing(0.25)};
    color: ${theme.colors.text.secondary};
  `,
  disclaimer: css`
    color: ${theme.colors.text.secondary};
    font-size: ${theme.typography.bodySmall.fontSize};
    font-style: italic;
  `,
});
