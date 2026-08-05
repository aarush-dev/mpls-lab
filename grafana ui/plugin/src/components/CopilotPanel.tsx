import React from 'react';
import { Drawer } from '@grafana/ui';
import { CopilotChat } from './CopilotChat';

// T5/#72: global side panel. Mounted once above all routes; a top-bar button toggles `open`. Renders
// the same shared chat as the /copilot tab, so the conversation is continuous between them.
export function CopilotPanel({ open, onClose }: { open: boolean; onClose: () => void }) {
  if (!open) {
    return null;
  }
  return (
    <Drawer title="Copilot" subtitle="Ask about the network from any page" size="md" onClose={onClose}>
      <CopilotChat />
    </Drawer>
  );
}
