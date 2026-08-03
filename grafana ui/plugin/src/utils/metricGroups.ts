import { MetricSeries } from '../data/types';
import { METRIC_CATALOG, metricInfoForName } from '../data/metricCatalog';

const nameOf = (key: string) => key.slice(key.lastIndexOf(':') + 1);

// One panel per entity-multiplied metric (e.g. "Interface RX errors"), one panel per
// non-entity group (e.g. "Host CPU / memory"); 'Fault predictor' leads, 'Other' trails.
// Catalog order is preserved since a group's metric names are contiguous in METRIC_CATALOG.
const PANEL_ORDER = ['Fault predictor', ...METRIC_CATALOG.reduce<string[]>((acc, m) => {
  const key = m.entityLabel ? m.label : m.group;
  if (!acc.includes(key)) {
    acc.push(key);
  }
  return acc;
}, []), 'Other'];

// All-zero series on an entity panel are noise (e.g. an interface with no errors ever);
// non-entity panels keep zeroes since a single "0 = healthy" metric must stay visible there.
const isAllZero = (s: MetricSeries) => s.points.length > 0 && s.points.every((p) => !p.value);

// Groups a flat metric list into single-unit panels so no chart mixes magnitudes
// (TimeSeriesPanel shares one y-axis per panel). Grouping comes from the catalog
// (data/metricCatalog.ts), keyed on the metric __name__ — the last `:`-separated
// segment of the namespaced key (`ce_branch1:eth0:interface_ifHCInOctets` -> `interface_ifHCInOctets`).
export function groupSeries(series: MetricSeries[]): Array<{ title: string; unit?: string; series: MetricSeries[] }> {
  const titleFor = (s: MetricSeries) => {
    if (s.key.endsWith(':predictor')) {
      return 'Fault predictor';
    }
    const info = metricInfoForName(nameOf(s.key));
    if (!info) {
      return 'Other';
    }
    return info.entityLabel ? info.label : info.group;
  };

  const byTitle = new Map<string, MetricSeries[]>();
  for (const s of series) {
    const info = metricInfoForName(nameOf(s.key));
    if (info?.entityLabel && isAllZero(s)) {
      continue;
    }
    const title = titleFor(s);
    const bucket = byTitle.get(title);
    if (bucket) {
      bucket.push(s);
    } else {
      byTitle.set(title, [s]);
    }
  }

  const groups: Array<{ title: string; unit?: string; series: MetricSeries[] }> = [];
  for (const title of PANEL_ORDER) {
    const matched = byTitle.get(title);
    if (matched && matched.length) {
      const unit = matched.every((s) => s.unit === matched[0].unit) ? matched[0].unit : undefined;
      groups.push({ title, unit, series: matched });
    }
  }

  return groups;
}
