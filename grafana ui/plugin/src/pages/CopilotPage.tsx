import React, { useEffect, useRef } from 'react';
import { PluginPage } from '@grafana/runtime';
import { CopilotChat } from '../components/CopilotChat';
import { useSharedCopilotChat } from '../hooks/CopilotChatContext';
import { COPILOT_CASE_KEY } from '../constants';

// T2/#68 + T3/#69 + T5/#72: the /copilot tab. Thin wrapper — the chat surface lives in `CopilotChat`
// and reads the SHARED hook, so it and the global side panel are one conversation.
// Deep-link: FaultInjectionPage's "Open copilot" stashes a forensic case (sessionStorage, with a
// query-string fallback). On arrival, start a fresh chat and auto-ask about it, scoped to the hour
// before the case timestamp.
type CaseCtx = { device: string | null; ts: string | null; fault_type?: string | null; severity?: string | null };

function readCase(): CaseCtx | null {
  try {
    const raw = sessionStorage.getItem(COPILOT_CASE_KEY);
    if (raw) {
      sessionStorage.removeItem(COPILOT_CASE_KEY);
      return JSON.parse(raw) as CaseCtx;
    }
  } catch {
    // storage unavailable — fall through to the query string
  }
  const p = new URLSearchParams(window.location.search);
  if (p.get('device') && p.get('ts')) {
    window.history.replaceState({}, '', window.location.pathname);
    return { device: p.get('device'), ts: p.get('ts'), fault_type: p.get('fault'), severity: p.get('sev') };
  }
  return null;
}

export function CopilotPage() {
  const { newChat, send } = useSharedCopilotChat();
  const fired = useRef(false);

  useEffect(() => {
    if (fired.current) {
      return;
    }
    const c = readCase();
    if (!c || !c.device) {
      return;
    }
    fired.current = true;
    const t = c.ts ? Date.parse(c.ts) : NaN;
    const scope = Number.isFinite(t) ? { start: Math.floor(t / 1000) - 3600, end: Math.floor(t / 1000) } : undefined;
    // No fault type (PA predictions) → ask generically about "the incident"; don't double the word.
    const subject = c.fault_type ? `${c.fault_type} incident` : 'incident';
    const when = c.ts ? ` around ${c.ts}` : '';
    const win = scope ? ` Focus on the hour before it (${new Date(scope.start * 1000).toISOString()} to ${c.ts}).` : '';
    const q = `Investigate the ${subject} on ${c.device}${when}${c.severity ? ` (severity ${c.severity})` : ''}.${win} What happened and why?`;
    newChat();
    send(q, false, scope);
  }, [newChat, send]);

  return (
    <PluginPage>
      <CopilotChat />
    </PluginPage>
  );
}
