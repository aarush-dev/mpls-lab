import React, { useEffect, useRef } from 'react';
import { PluginPage } from '@grafana/runtime';
import { CopilotChat } from '../components/CopilotChat';
import { useSharedCopilotChat } from '../hooks/CopilotChatContext';

// T2/#68 + T3/#69 + T5/#72: the /copilot tab. Thin wrapper — the chat surface lives in `CopilotChat`
// and reads the SHARED hook, so it and the global side panel are one conversation.
// Deep-link: FaultInjectionPage's "Open copilot" carries a forensic case in the query string
// (copilotCasePath). On arrival, start a fresh chat and auto-ask about it, scoped to the hour before.
export function CopilotPage() {
  const { newChat, send } = useSharedCopilotChat();
  const fired = useRef(false);

  useEffect(() => {
    if (fired.current) {
      return;
    }
    const p = new URLSearchParams(window.location.search);
    const device = p.get('device');
    const ts = p.get('ts');
    if (!device || !ts) {
      return;
    }
    fired.current = true;
    const t = Date.parse(ts);
    const scope = Number.isFinite(t) ? { start: Math.floor(t / 1000) - 3600, end: Math.floor(t / 1000) } : undefined;
    const fault = p.get('fault') || 'incident';
    const sev = p.get('sev');
    const window_ = scope ? ` Focus on the hour before it (${new Date(scope.start * 1000).toISOString()} to ${ts}).` : '';
    const q = `Investigate the ${fault} incident on ${device} around ${ts}${sev ? ` (severity ${sev})` : ''}.${window_} What happened and why?`;
    newChat();
    send(q, false, scope);
    // Drop the query so a re-render/reload can't resend the same case.
    window.history.replaceState({}, '', window.location.pathname);
  }, [newChat, send]);

  return (
    <PluginPage>
      <CopilotChat />
    </PluginPage>
  );
}
