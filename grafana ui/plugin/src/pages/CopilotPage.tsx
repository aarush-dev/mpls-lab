import React from 'react';
import { PluginPage } from '@grafana/runtime';
import { CopilotChat } from '../components/CopilotChat';

// T2/#68 + T3/#69 + T5/#72: the /copilot tab. Thin wrapper — the chat surface lives in `CopilotChat`
// and reads the SHARED hook, so it and the global side panel are one conversation.
export function CopilotPage() {
  return (
    <PluginPage>
      <CopilotChat />
    </PluginPage>
  );
}
