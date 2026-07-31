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
