import { MetricSeries } from '../data/types';
import { METRIC_GROUPS, catalogGroupFor } from '../data/metricCatalog';

// Groups a flat metric list into single-unit panels so no chart mixes magnitudes
// (TimeSeriesPanel shares one y-axis per panel). Group comes from the catalog
// (data/metricCatalog.ts), keyed on the metric __name__ — the last `:`-separated
// segment of the namespaced key (`ce_branch1:eth0:interface_ifHCInOctets` -> `interface_ifHCInOctets`).
export function groupSeries(series: MetricSeries[]): Array<{ title: string; unit?: string; series: MetricSeries[] }> {
  const titleFor = (s: MetricSeries) => {
    if (s.key.endsWith(':predictor')) {
      return 'Fault predictor';
    }
    return catalogGroupFor(s.key);
  };

  const byTitle = new Map<string, MetricSeries[]>();
  for (const s of series) {
    const title = titleFor(s);
    const bucket = byTitle.get(title);
    if (bucket) {
      bucket.push(s);
    } else {
      byTitle.set(title, [s]);
    }
  }

  const order = ['Fault predictor', ...METRIC_GROUPS, 'Other'];
  const groups: Array<{ title: string; unit?: string; series: MetricSeries[] }> = [];
  for (const title of order) {
    const matched = byTitle.get(title);
    if (matched && matched.length) {
      const unit = matched.every((s) => s.unit === matched[0].unit) ? matched[0].unit : undefined;
      groups.push({ title, unit, series: matched });
    }
  }

  return groups;
}
