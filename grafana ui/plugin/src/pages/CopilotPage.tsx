import React from 'react';
import { css } from '@emotion/css';
import { PluginPage } from '@grafana/runtime';
import { GrafanaTheme2 } from '@grafana/data';
import { useStyles2 } from '@grafana/ui';
import { CopilotChat } from '../components/CopilotChat';

// T3/#74: full-page conversation view. The global drawer (CopilotPanel) is the compact quick-ask
// surface; this page is the roomier version — same SHARED hook, so it's one continuous conversation,
// just given a header and a wider max-width instead of duplicating the drawer verbatim.
export function CopilotPage() {
  const styles = useStyles2(getStyles);
  return (
    <PluginPage>
      <div className={styles.root}>
        <h1 className={styles.title}>Copilot</h1>
        <p className={styles.subtitle}>Ask about the network. Also available as a side panel from any page.</p>
        <CopilotChat />
      </div>
    </PluginPage>
  );
}

const getStyles = (theme: GrafanaTheme2) => ({
  root: css`
    max-width: 960px;
    margin: 0 auto;
  `,
  title: css`
    margin-bottom: ${theme.spacing(0.5)};
  `,
  subtitle: css`
    color: ${theme.colors.text.secondary};
    margin-bottom: ${theme.spacing(2)};
  `,
});
