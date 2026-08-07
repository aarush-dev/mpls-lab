import React from 'react';
import { render, screen, waitFor, within } from '@testing-library/react';
import '@testing-library/jest-dom';

jest.mock('@grafana/runtime', () => ({
  PluginPage: ({ children }: any) => <div>{children}</div>,
}));

import type { ForensicCase } from '../data/types';
import { copilotCasePath } from '../constants';

// Fake client: only the copilot-cases path matters here; other fault methods return empty.
const fakeCases: ForensicCase[] = [
  { id: 'c1', ts: '2026-08-06T10:00:00Z', device: 'r1', fault_type: 'link_flap', severity: 'high' },
  { id: 'c2', ts: '2026-08-06T11:00:00Z', device: 'r2', fault_type: 'cpu_hog', severity: 'medium' },
];
let cases = fakeCases;
const client = {
  getTopology: () => Promise.resolve({ nodes: [], edges: [] }),
  getScenarios: () => Promise.resolve([]),
  getActiveFaults: () => Promise.resolve([]),
  getCases: () => Promise.resolve(cases),
};
jest.mock('../data/DataClientContext', () => ({ useDataClient: () => client }));

// @grafana/ui needs TextEncoder/TextDecoder, which jsdom lacks. Polyfill then require the page.
(global as any).TextEncoder = require('util').TextEncoder;
(global as any).TextDecoder = require('util').TextDecoder;
const { FaultInjectionPage } = require('./FaultInjectionPage');

describe('FaultInjectionPage — copilot cases panel', () => {
  it('lists cases newest-first with device/type/severity/time and a copilot link', async () => {
    cases = fakeCases;
    render(<FaultInjectionPage />);

    await waitFor(() => expect(screen.getByText('Copilot cases (2)')).toBeInTheDocument());

    const items = screen.getAllByRole('listitem');
    // Newest-first: c2 (11:00) before c1 (10:00).
    expect(within(items[0]).getByText('r2')).toBeInTheDocument();
    expect(within(items[0]).getByText(/cpu_hog · medium/)).toBeInTheDocument();
    expect(within(items[1]).getByText('r1')).toBeInTheDocument();

    // Deep-link carries the case so /copilot can auto-ask about it (newest-first: c2/r2).
    const links = screen.getAllByRole('link', { name: /Open copilot/ });
    expect(links[0]).toHaveAttribute('href', copilotCasePath(fakeCases[1]));
  });

  it('shows the empty state when no cases exist', async () => {
    cases = [];
    render(<FaultInjectionPage />);
    await waitFor(() => expect(screen.getByText('Copilot cases (0)')).toBeInTheDocument());
    expect(screen.getByText(/None yet\./)).toBeInTheDocument();
  });
});
