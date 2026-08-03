import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import '@testing-library/jest-dom';
import type { ChatItem } from './CopilotChat';
import type { CopilotResponse } from '../data/types';

// @grafana/ui pulls in react-dom/server, which needs TextEncoder/TextDecoder — jsdom doesn't
// provide them. Polyfill from node's `util`, then require the component (plain `require`, not
// `import`, so it isn't hoisted above the polyfill by the module transform).
(global as any).TextEncoder = require('util').TextEncoder;
(global as any).TextDecoder = require('util').TextDecoder;
const { CopilotChat } = require('./CopilotChat');

const response: CopilotResponse = {
  summary: 'Tunnel latency is rising on pe1.',
  predictedIssue: 'Tunnel latency spike',
  confidence: 0.8,
  timeToImpactSeconds: 300,
  affectedScope: ['pe1'],
  evidence: [{ label: 'CPU', detail: 'high', source: 'measured' }],
  rootCauseHypotheses: ['x'],
  recommendedActions: [{ title: 'Do X', detail: 'y' }],
  citations: [{ title: 'Runbook: tunnel latency', href: 'ragcorpus/runbook-tunnel-latency-high.md' }],
  disclaimer: 'demo',
};

describe('CopilotChat', () => {
  it('renders a complete assistant response card', () => {
    const items: ChatItem[] = [
      {
        message: { id: 'a1', role: 'assistant', content: 'hello', createdAt: 't', state: 'complete' },
        response,
      },
    ];
    render(<CopilotChat items={items} onRetry={jest.fn()} />);

    // Message content is the turn's prose (what CopilotPage sets from response.summary in real use).
    expect(screen.getByText('hello')).toBeInTheDocument();
    expect(screen.getByText('CPU:')).toBeInTheDocument();
    expect(screen.getByText('Do X.')).toBeInTheDocument();
    expect(screen.getByText('Runbook: tunnel latency')).toBeInTheDocument();
  });

  it('renders the trace and a verified gate badge when present', () => {
    const withTrace: CopilotResponse = {
      ...response,
      gateVerdict: { ok: true, missing: [], retry: 1 },
      trace: [
        { kind: 'think', content: 'check metrics' },
        { kind: 'tool_call', name: 'query_metrics', arguments: { device: 'pe1' }, id: 'c1' },
        { kind: 'tool_result', name: 'query_metrics', id: 'c1', content: 'cpu 95', n: 3 },
        { kind: 'gate', gate: { ok: true, missing: [], retry: 1 } },
      ],
    };
    const items: ChatItem[] = [
      { message: { id: 'a1', role: 'assistant', content: 'hi', createdAt: 't', state: 'complete' }, response: withTrace },
    ];
    render(<CopilotChat items={items} onRetry={jest.fn()} />);

    expect(screen.getByText(/how i investigated/i)).toBeInTheDocument();
    expect(screen.getAllByText(/query_metrics/).length).toBeGreaterThan(0);
    expect(screen.getByText(/verified/i)).toBeInTheDocument();
  });

  it('shows the missing evidence when the gate did not pass', () => {
    const blocked: CopilotResponse = {
      ...response,
      gateVerdict: { ok: false, missing: ['topology', 'latency'], retry: 2 },
    };
    const items: ChatItem[] = [
      { message: { id: 'a1', role: 'assistant', content: 'hi', createdAt: 't', state: 'complete' }, response: blocked },
    ];
    render(<CopilotChat items={items} onRetry={jest.fn()} />);

    // the gate badge lists the missing evidence (retry ×2 folded in)
    expect(screen.getByText(/missing: topology, latency/i)).toBeInTheDocument();
    expect(screen.getByText(/retry ×2/i)).toBeInTheDocument();
  });

  it('renders no trace section when the response carries none', () => {
    const items: ChatItem[] = [
      { message: { id: 'a1', role: 'assistant', content: 'hi', createdAt: 't', state: 'complete' }, response },
    ];
    render(<CopilotChat items={items} onRetry={jest.fn()} />);

    expect(screen.queryByText(/how i investigated/i)).not.toBeInTheDocument();
  });

  it('shows a thinking indicator while sending', () => {
    const items: ChatItem[] = [
      { message: { id: 'u1', role: 'user', content: 'hi', createdAt: 't', state: 'sending' } },
    ];
    render(<CopilotChat items={items} onRetry={jest.fn()} />);

    expect(screen.getByText(/thinking/i)).toBeInTheDocument();
  });

  it('shows a retry button on error and calls onRetry with the message id', () => {
    const onRetry = jest.fn();
    const items: ChatItem[] = [
      { message: { id: 'u2', role: 'user', content: 'hi', createdAt: 't', state: 'error' } },
    ];
    render(<CopilotChat items={items} onRetry={onRetry} />);

    const retryBtn = screen.getByRole('button', { name: /retry/i });
    expect(retryBtn).toBeInTheDocument();

    fireEvent.click(retryBtn);
    expect(onRetry).toHaveBeenCalledTimes(1);
    expect(onRetry).toHaveBeenCalledWith('u2');
  });
});
