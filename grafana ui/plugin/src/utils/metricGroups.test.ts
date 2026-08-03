import { groupSeries } from './metricGroups';
import { MetricSeries } from '../data/types';
import { METRIC_GROUPS } from '../data/metricCatalog';

const s = (key: string, unit = ''): MetricSeries => ({
  key,
  label: key,
  unit,
  source: 'measured',
  points: [],
});

describe('groupSeries', () => {
  it('derives group from the catalog, keyed on the trailing metric name', () => {
    const groups = groupSeries([
      s('pe1:node_cpu_pct', '%'),
      s('pe1:eth1:interface_ifInErrors', '1/s'),
      s('ce_branch1:tunnel-x:sdwan_tunnel_latency_ms', 'ms'),
    ]);
    const titleOf = (key: string) => groups.find((g) => g.series.some((x) => x.key === key))?.title;
    expect(titleOf('pe1:node_cpu_pct')).toBe('Host CPU / memory');
    expect(titleOf('pe1:eth1:interface_ifInErrors')).toBe('Interface errors');
    expect(titleOf('ce_branch1:tunnel-x:sdwan_tunnel_latency_ms')).toBe('Tunnel');
  });

  it('puts the fault-predictor series in its own leading panel', () => {
    const groups = groupSeries([s('pe1:predictor', '%'), s('pe1:node_cpu_pct', '%')]);
    expect(groups[0].title).toBe('Fault predictor');
    expect(groups[0].series).toHaveLength(1);
  });

  it('sends uncatalogued series to a trailing Other panel', () => {
    const groups = groupSeries([s('pe1:node_cpu_pct', '%'), s('pe1:some_unknown_metric', 'x')]);
    const other = groups.find((g) => g.title === 'Other');
    expect(other?.series.map((x) => x.key)).toEqual(['pe1:some_unknown_metric']);
  });

  it('omits the Other panel when every series is catalogued', () => {
    const groups = groupSeries([s('pe1:node_cpu_pct', '%')]);
    expect(groups.some((g) => g.title === 'Other')).toBe(false);
  });

  it('drops empty groups and puts each series in exactly one group', () => {
    const input = [s('d:node_cpu_pct'), s('d:eth0:interface_ifInErrors'), s('d:eth0:iface_queue_drops')];
    const groups = groupSeries(input);
    const total = groups.reduce((n, g) => n + g.series.length, 0);
    expect(total).toBe(input.length);
    expect(groups.every((g) => g.series.length > 0)).toBe(true);
  });

  it('orders panels by METRIC_GROUPS, with Fault predictor first and Other last', () => {
    const input = [
      s('d:eth0:interface_ifHCInOctets'),
      s('d:node_cpu_pct'),
      s('d:predictor'),
      s('d:some_unknown_metric'),
    ];
    const groups = groupSeries(input);
    const titles = groups.map((g) => g.title);
    expect(titles).toEqual(['Fault predictor', 'Interface throughput', 'Host CPU / memory', 'Other']);
    // Sanity: the catalog-derived titles really do follow METRIC_GROUPS order.
    const catalogTitles = titles.filter((t) => METRIC_GROUPS.includes(t));
    const expectedOrder = METRIC_GROUPS.filter((g) => catalogTitles.includes(g));
    expect(catalogTitles).toEqual(expectedOrder);
  });
});
