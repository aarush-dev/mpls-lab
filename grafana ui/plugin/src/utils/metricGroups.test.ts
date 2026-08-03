import { groupSeries } from './metricGroups';
import { MetricSeries } from '../data/types';

const s = (key: string, unit = ''): MetricSeries => ({
  key,
  label: key,
  unit,
  source: 'measured',
  points: [],
});

describe('groupSeries', () => {
  it('matches on the metric suffix, not the full namespaced key', () => {
    const groups = groupSeries([
      s('ce_branch1:cpu_pct', '%'),
      s('ce_branch1:eth0:if_in_octets', 'bytes'),
    ]);
    const titleOf = (key: string) => groups.find((g) => g.series.some((x) => x.key === key))?.title;
    expect(titleOf('ce_branch1:cpu_pct')).toBe('Host CPU / memory');
    expect(titleOf('ce_branch1:eth0:if_in_octets')).toBe('Interface throughput');
  });

  it('splits tunnel latency/jitter (ms) from loss (%) so a panel never mixes units', () => {
    const groups = groupSeries([
      s('d:t:tunnel_latency_ms', 'ms'),
      s('d:t:tunnel_jitter_ms', 'ms'),
      s('d:t:tunnel_loss_pct', '%'),
    ]);
    const latency = groups.find((g) => g.title === 'Tunnel latency / jitter');
    const loss = groups.find((g) => g.title === 'Tunnel loss');
    expect(latency?.series).toHaveLength(2);
    expect(loss?.series).toHaveLength(1);
  });

  it('drops empty groups and puts each series in exactly one group', () => {
    const input = [s('d:cpu_pct'), s('d:eth0:if_in_errors'), s('d:eth0:q_drops')];
    const groups = groupSeries(input);
    const total = groups.reduce((n, g) => n + g.series.length, 0);
    expect(total).toBe(input.length);
    expect(groups.every((g) => g.series.length > 0)).toBe(true);
  });
});
