import React from 'react';
import { PluginPage } from '@grafana/runtime';

// T1/#67 removed the mock copilot path (structured `CopilotResponse`) and left this a placeholder.
// T2/#68 rebuilds it on the real streaming `DataClient.chat` seam (useCopilotChat hook, cited
// answer + investigating/error states). Until then the tab renders nothing that can fake an answer.
export function CopilotPage() {
  return (
    <PluginPage>
      <h1>Copilot</h1>
      <p>Copilot chat is being wired to the live investigation service. (#68)</p>
    </PluginPage>
  );
}
