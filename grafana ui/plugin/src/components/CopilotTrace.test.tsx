import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import '@testing-library/jest-dom';

import { CopilotTrace, citedRow } from './CopilotTrace';
import type { ChatEvent, CopilotTurn, ToolResultEvent } from '../data/types';

const result: ToolResultEvent = {
  type: 'tool_result',
  ts: '3',
  id: 'call-1',
  name: 'metrics',
  n: 2,
  content: 'cpu 12% [metrics:0]\nbgp flap [metrics:1]',
};
const events: ChatEvent[] = [
  { type: 'think', ts: '1', content: 'check R1' },
  { type: 'tool_call', ts: '2', id: 'call-1', name: 'metrics', arguments: { dev: 'R1' } },
  result,
  { type: 'gate', ts: '4', ok: true, missing: [], retry: 0 },
  { type: 'assistant_msg', ts: '5', content: 'BGP flap on R1 [metrics:1].' },
];
const turn: CopilotTurn = {
  events,
  answer: 'BGP flap on R1 [metrics:1].',
  citations: [{ id: 'metrics:1', source: 'metrics', offset: 1 }],
  citeMap: { 'metrics:1': result },
};

beforeAll(() => {
  Element.prototype.scrollIntoView = jest.fn();
  // jsdom has no object-URL impl — stub it so ArtifactView can build/revoke blob URLs.
  (URL as unknown as { createObjectURL: unknown }).createObjectURL = jest.fn(() => 'blob:art');
  (URL as unknown as { revokeObjectURL: unknown }).revokeObjectURL = jest.fn();
});

const b64 = btoa('IMG');

test('a raster artifact renders inline as an image', () => {
  const art: ChatEvent = { type: 'artifact', ts: '6', name: '0000-cpu.png', kind: 'chart', mime: 'image/png', title: 'r1 cpu', content_b64: b64 };
  render(<CopilotTrace events={[art]} />);
  const img = screen.getByRole('img', { name: 'r1 cpu' });
  expect(img).toHaveAttribute('src', 'blob:art');
  expect(URL.createObjectURL).toHaveBeenCalledWith(expect.any(Blob));
});

test('an svg artifact is a download chip, NEVER inlined (stored-XSS guard)', () => {
  const art: ChatEvent = { type: 'artifact', ts: '6', name: '0000-plot.svg', kind: 'chart', mime: 'image/svg+xml', content_b64: b64 };
  render(<CopilotTrace events={[art]} />);
  expect(screen.queryByRole('img')).not.toBeInTheDocument();
  const chip = screen.getByText(/0000-plot\.svg/).closest('a');
  expect(chip).toHaveAttribute('download', '0000-plot.svg');
});

test('a text/code artifact downloads (not the dead on-backend chip)', () => {
  const art: ChatEvent = { type: 'artifact', ts: '6', name: '0000-fix.py', kind: 'code', mime: 'text/x-python', content: 'print(1)' };
  render(<CopilotTrace events={[art]} />);
  const chip = screen.getByText(/0000-fix\.py/).closest('a');
  expect(chip).toHaveAttribute('download', '0000-fix.py');
});

test('citedRow lifts the line carrying the id', () => {
  expect(citedRow(result.content, 'metrics:1')).toBe('bgp flap [metrics:1]');
  expect(citedRow(result.content, 'metrics:9')).toBe('');
});

test('trace cards are collapsed by default and expand on click', () => {
  render(<CopilotTrace events={events} turn={turn} />);
  // Collapsed: args/evidence hidden.
  expect(screen.queryByText('args')).not.toBeInTheDocument();
  fireEvent.click(screen.getByText('metrics'));
  expect(screen.getByText('args')).toBeInTheDocument();
  expect(screen.getByText('bgp flap [metrics:1]')).toBeInTheDocument();
});

test('citation chip renders, previews the cited row, and jumps on click', () => {
  render(<CopilotTrace events={events} turn={turn} />);
  const chip = screen.getByRole('button', { name: 'metrics:1' });
  expect(chip).toHaveAttribute('title', 'bgp flap [metrics:1]');
  // Card collapsed before click.
  expect(screen.queryByText('bgp flap [metrics:1]')).not.toBeInTheDocument();
  fireEvent.click(chip);
  // Click expands the evidence card + scrolls to it.
  expect(screen.getByText('bgp flap [metrics:1]')).toBeInTheDocument();
  expect(Element.prototype.scrollIntoView).toHaveBeenCalled();
});
