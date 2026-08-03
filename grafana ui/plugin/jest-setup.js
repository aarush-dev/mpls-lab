// Jest setup provided by Grafana scaffolding
import './.config/jest-setup';

// jsdom here lacks TextEncoder/TextDecoder, which @grafana/ui pulls in via react-dom/server.
// Polyfill once, globally, so component tests can import @grafana/ui normally.
import { TextEncoder, TextDecoder } from 'util';
if (typeof global.TextEncoder === 'undefined') {
  global.TextEncoder = TextEncoder;
}
if (typeof global.TextDecoder === 'undefined') {
  global.TextDecoder = TextDecoder;
}

// jsdom has no fetch; api-mode HttpDataClient calls it. Stub it to an empty-success Response so
// component tests that mount data-fetching pages get empty data (not a ReferenceError or throw) and
// render their normal empty state. Copilot methods forward to the mock client and don't touch fetch.
// Individual tests can override global.fetch with their own jest.fn where they assert on responses.
if (typeof global.fetch === 'undefined') {
  global.fetch = () =>
    Promise.resolve({
      ok: true,
      status: 200,
      json: () => Promise.resolve({ result: [], rows: [], nodes: [], links: [], scenarios: [] }),
      text: () => Promise.resolve(''),
    });
}
