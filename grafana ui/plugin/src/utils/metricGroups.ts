import { MetricSeries } from '../data/types';

// Groups a flat metric list into single-unit panels so no chart mixes magnitudes
// (TimeSeriesPanel shares one y-axis per panel). Matches on the metric SUFFIX — the last
// `:`-separated segment of the namespaced key (`ce_branch1:eth0:if_in_octets` → `if_in_octets`).
// ponytail: Tunnel is two panels (latency/jitter vs loss) because ms and % can't share a y-axis.
export function groupSeries(series: MetricSeries[]): Array<{ title: string; unit?: string; series: MetricSeries[] }> {
  const suffix = (s: MetricSeries) => s.key.split(':').pop() ?? s.key;
  const groups: Array<{ title: string; unit?: string; series: MetricSeries[] }> = [];
  const claimed = new Set<MetricSeries>();

  const bucket = (title: string, test: (suf: string) => boolean) => {
    const matched = series.filter((s) => !claimed.has(s) && test(suffix(s)));
    if (matched.length) {
      matched.forEach((s) => claimed.add(s));
      groups.push({ title, unit: matched[0].unit, series: matched });
    }
  };

  // Injected-fault leading indicator — shown first so the forecast is the headline panel.
  bucket('Fault predictor', (suf) => suf === 'predictor');
  bucket('Host CPU / memory', (suf) => suf === 'cpu_pct' || suf === 'mem_pct');
  bucket('Interface throughput', (suf) => /if_(in|out)_(octets|bytes|bps)/.test(suf));
  bucket('Interface errors', (suf) => ['if_in_errors', 'if_in_discards', 'if_out_errors'].includes(suf));
  bucket('Queue', (suf) => suf.startsWith('q_'));
  bucket('Transceiver', (suf) => suf.startsWith('xcvr_'));
  bucket('Tunnel latency / jitter', (suf) => suf === 'tunnel_latency_ms' || suf === 'tunnel_jitter_ms');
  bucket('Tunnel loss', (suf) => suf === 'tunnel_loss_pct');
  bucket('Other', () => true);

  return groups;
}
