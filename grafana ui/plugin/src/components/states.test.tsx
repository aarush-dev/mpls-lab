import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import '@testing-library/jest-dom';

// @grafana/ui pulls in react-dom/server, which needs TextEncoder/TextDecoder — jsdom doesn't
// provide them. Polyfill from node's `util`, then require the components (plain `require`, not
// `import`, so it isn't hoisted above the polyfill by the module transform).
(global as any).TextEncoder = require('util').TextEncoder;
(global as any).TextDecoder = require('util').TextDecoder;
const { EmptyState } = require('./EmptyState');
const { ErrorState } = require('./ErrorState');

describe('EmptyState', () => {
  it('renders the default message', () => {
    render(<EmptyState />);
    expect(screen.getByText('No data for the current view.')).toBeInTheDocument();
  });

  it('renders a custom message', () => {
    render(<EmptyState message="Nothing here." />);
    expect(screen.getByText('Nothing here.')).toBeInTheDocument();
  });
});

describe('ErrorState', () => {
  it('renders a retry button that calls onRetry when provided', () => {
    const onRetry = jest.fn();
    render(<ErrorState message="Boom" onRetry={onRetry} />);

    expect(screen.getByText('Boom')).toBeInTheDocument();
    const retryBtn = screen.getByRole('button', { name: /retry/i });
    fireEvent.click(retryBtn);
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it('renders no retry button when onRetry is omitted', () => {
    render(<ErrorState message="Boom" />);
    expect(screen.queryByRole('button', { name: /retry/i })).not.toBeInTheDocument();
  });
});
