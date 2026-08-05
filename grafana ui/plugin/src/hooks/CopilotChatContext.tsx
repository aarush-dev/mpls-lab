// T5/#72: one copilot conversation, shared by the /copilot tab and the global side panel. The
// driver hook (useCopilotChat) owns per-instance thread state; calling it separately in each surface
// would fork the conversation. Mount it ONCE here and hand the same value to both consumers so a
// question asked in either place appears in the other.
import React, { createContext, PropsWithChildren, useContext } from 'react';
import { useCopilotChat as useCopilotChatDriver, UseCopilotChat } from './useCopilotChat';

const CopilotChatContext = createContext<UseCopilotChat | undefined>(undefined);

export function CopilotChatProvider({ children }: PropsWithChildren<{}>) {
  const chat = useCopilotChatDriver();
  return <CopilotChatContext.Provider value={chat}>{children}</CopilotChatContext.Provider>;
}

export function useSharedCopilotChat(): UseCopilotChat {
  const ctx = useContext(CopilotChatContext);
  if (!ctx) {
    throw new Error('useSharedCopilotChat must be used within a CopilotChatProvider');
  }
  return ctx;
}
