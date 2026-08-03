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
