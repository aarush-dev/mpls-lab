import React from 'react';
import { act, render, screen } from '@testing-library/react';
import { CopilotChatProvider, useSharedCopilotChat } from './CopilotChatContext';
import type { ChatRequest, CopilotTurn } from '../data/types';

// Same context stubs the driver test uses — no providers/backends needed.
const chat = jest.fn();
jest.mock('../data/DataClientContext', () => ({ useDataClient: () => ({ chat }) }));
jest.mock('../state/AppContext', () => ({ useAppState: () => ({ mode: 'live', range: { fromMs: 0, toMs: 0 } }) }));

beforeEach(() => {
  chat.mockReset();
  localStorage.clear();
});

// The acceptance: a question asked in one surface (the panel) appears in the other (the tab). Two
// consumers under ONE provider must read the same thread — proving the conversation isn't forked.
test('two consumers under one provider share the thread and session', async () => {
  chat.mockResolvedValue({ events: [], answer: 'ok', citations: [], citeMap: {} } as CopilotTurn);

  let sendFromPanel!: (q: string) => void;
  const seen: { req?: ChatRequest } = {};
  chat.mockImplementation((req: ChatRequest) => {
    seen.req = req;
    return Promise.resolve({ events: [], answer: 'ok', citations: [], citeMap: {} } as CopilotTurn);
  });

  function Panel() {
    const { send } = useSharedCopilotChat();
    sendFromPanel = send;
    return null;
  }
  function Tab() {
    const { items } = useSharedCopilotChat();
    return <div data-testid="tab">{items.map((t) => t.question).join('|')}</div>;
  }

  render(
    <CopilotChatProvider>
      <Panel />
      <Tab />
    </CopilotChatProvider>
  );

  expect(screen.getByTestId('tab').textContent).toBe('');
  await act(async () => sendFromPanel('why is R1 down?'));
  // Sent from the panel, visible on the tab.
  expect(screen.getByTestId('tab').textContent).toBe('why is R1 down?');
  // Same session id flows through the shared hook.
  expect(seen.req?.sessionId).toBeTruthy();
});
