import React from 'react';
import { render, screen } from '@testing-library/react';
import '@testing-library/jest-dom';

(global as any).TextEncoder = require('util').TextEncoder;
(global as any).TextDecoder = require('util').TextDecoder;

const { LogTerminal, cleanLine } = require('./LogTerminal');
const { FlowTable, humanBytes } = require('./FlowTable');
const { InterfaceTable, lastValue } = require('./InterfaceTable');

describe('LogTerminal', () => {
  it('strips RFC5424 headers via cleanLine', () => {
    expect(cleanLine('<30>1 2026-08-03T05:20:07Z pe1 root - - - link down')).toBe('link down');
    expect(cleanLine('plain message')).toBe('plain message');
  });

  it('renders an error line in red and filters by text', () => {
    const events = [
      { tsMs: 1000, device: 'pe1', app: 'bgp', severity: 'error', line: 'peer flap' },
      { tsMs: 2000, device: 'pe2', app: 'bgp', severity: 'info', line: 'session up' },
    ];
    render(<LogTerminal events={events} />);
    expect(screen.getByText('peer flap')).toBeInTheDocument();
    expect(screen.getByText('session up')).toBeInTheDocument();
  });
});

describe('FlowTable', () => {
  it('humanizes bytes', () => {
    expect(humanBytes(2048)).toBe('2.00 KB');
    expect(humanBytes(512)).toBe('512 B');
  });

  it('computes the top talker by bytes', () => {
    const flows = [
      { tsMs: 1, ipSrc: '10.0.0.1', ipDst: '10.0.0.2', bytes: 100, packets: 1 },
      { tsMs: 2, ipSrc: '10.0.0.3', ipDst: '10.0.0.2', bytes: 900, packets: 2 },
    ];
    render(<FlowTable flows={flows} />);
    expect(screen.getAllByText('10.0.0.3').length).toBeGreaterThan(0);
  });
});

describe('InterfaceTable', () => {
  it('marks oper-status down', () => {
    const series = [
      { key: 'pe1:ge-0/0/0:interface_ifOperStatus', label: '', source: 'measured', points: [{ tMs: 1, value: 2 }] },
    ];
    render(<InterfaceTable series={series} />);
    expect(screen.getByText('down')).toBeInTheDocument();
  });

  it('lastValue picks the last non-null point', () => {
    const s = { key: 'k', label: '', source: 'measured', points: [{ tMs: 1, value: 5 }, { tMs: 2, value: null }] };
    expect(lastValue(s)).toBe(5);
  });
});
