import React from 'react';
import { MemoryRouter } from 'react-router-dom';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom';

jest.mock('@grafana/runtime', () => ({
  PluginPage: ({ children }: any) => <div>{children}</div>,
}));

import { AppProvider } from '../state/AppContext';
import { DataClientProvider } from '../data/DataClientContext';

// @grafana/ui (pulled in by CopilotPage) needs TextEncoder/TextDecoder, which jsdom doesn't
// provide. Polyfill from node's `util`, then require the page (plain `require`, not `import`, so
// it isn't hoisted above the polyfill by the module transform).
(global as any).TextEncoder = require('util').TextEncoder;
(global as any).TextDecoder = require('util').TextDecoder;
const { CopilotPage } = require('./CopilotPage');

// api mode (default) drives the real CopilotClient, which POSTs /chat and reads an SSE trace.
// Stub fetch: an SSE answer for /chat, empty JSON for the dataapi endpoints (getIncidents etc).
const ANSWER = 'r1 cpu is pegged [metrics:0]';
const CHAT_SSE = [
  { type: 'user_msg', content: 'q' },
  { type: 'tool_call', name: 'query_metrics', arguments: { device: 'r1' }, id: 'c1' },
  { type: 'tool_result', name: 'query_metrics', id: 'c1', content: '[metrics:0] cpu 95', n: 3 },
  { type: 'gate', ok: true, missing: [], retry: 0 },
  { type: 'assistant_msg', content: ANSWER },
]
  .map((e) => `data: ${JSON.stringify(e)}\n\n`)
  .join('');

beforeEach(() => {
  global.fetch = jest.fn(async (url: any) => {
    if (String(url).includes('/chat')) {
      const bytes = new (require('util').TextEncoder)().encode(CHAT_SSE);
      let done = false;
      return {
        ok: true,
        status: 200,
        body: { getReader: () => ({ read: () => (done ? Promise.resolve({ done: true }) : ((done = true), Promise.resolve({ done: false, value: bytes }))) }) },
        text: () => Promise.resolve(CHAT_SSE),
      } as any;
    }
    return { ok: true, status: 200, json: async () => ({ rows: [], nodes: [], links: [], result: [] }), text: async () => '' } as any;
  }) as unknown as typeof fetch;
});

afterEach(() => {
  // @ts-expect-error reset
  delete global.fetch;
});

function renderPage() {
  return render(
    <MemoryRouter>
      <AppProvider>
        <DataClientProvider>
          <CopilotPage />
        </DataClientProvider>
      </AppProvider>
    </MemoryRouter>
  );
}

describe('CopilotPage', () => {
  it('renders the suggestion buttons and the input + send button', () => {
    renderPage();
    expect(screen.getByPlaceholderText('Ask about the network…')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Send' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'What is happening on the network right now?' })).toBeInTheDocument();
  });

  it('sends a suggestion and renders an assistant reply end-to-end', async () => {
    const { container } = renderPage();
    const suggestion = 'What is happening on the network right now?';
    const before = container.textContent ?? '';

    fireEvent.click(screen.getByRole('button', { name: suggestion }));

    // The suggestion buttons disappear once the thread has a first item.
    await waitFor(() => {
      expect(screen.queryByRole('button', { name: suggestion })).not.toBeInTheDocument();
    });

    // The user turn is echoed as a chat bubble.
    expect(await screen.findByText(suggestion)).toBeInTheDocument();

    // The live copilot's cited answer lands in the thread.
    expect(await screen.findByText(ANSWER)).toBeInTheDocument();

    // Wait for the mock client's async reply (~120ms) to land and grow the thread.
    await waitFor(
      () => {
        const after = container.textContent ?? '';
        expect(after.length).toBeGreaterThan(before.length + suggestion.length);
      },
      { timeout: 3000 }
    );

    expect(screen.queryByText(/thinking/i)).not.toBeInTheDocument();
  });
});
