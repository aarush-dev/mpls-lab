# ui/ — shared presentation primitives

Presentation only. No data fetch, no backend calls. Consumed by the page redesign (T2+).

| Export | What | Key API |
|---|---|---|
| `severity` | single state/severity → Grafana token map | `colorForState(theme, 'red'\|'amber'\|'green'\|'unknown')`, `colorForSeverity(theme, 'low'\|'med'\|'high'\|'unknown')` → `{text, main, bg}`. No hex — all `GrafanaTheme2`. |
| `Panel` | card chrome | `<Panel title action>…</Panel>` |
| `DataTable<T>` | sortable styled table | `columns` (`sort` accessor makes a header clickable; `align:'num'\|'mono'`), `rowKey`, optional `onRowClick`. Pure `sortRows` for tests. |
| `Sparkline` | leaf SVG trend | `values: (number\|null)[]`; nulls break the line. Pure `sparkPath`. |
| `Gauge` / `Meter` | SVG percent meter | `value`, `max`, `thresholds`. Color from `severity`. Pure `gaugeGeometry`. |
| `StatusGrid` | NxN device-health matrix | `devices: {id, state}[]`, cols = ceil(√n), optional `onSelect`. |
| `SeverityStrip` | aggregate count bar | `counts: {red?,amber?,green?,unknown?}`, proportional segments + legend. |

`TimeSeriesPanel` (in `../`) is responsive (container width via ResizeObserver, fallback 640) with y-gridlines, a hover crosshair readout, and an optional `threshold` line.

Tests: `ui.test.tsx` (severity map, DataTable sort + row-click, Sparkline path, Gauge geometry).
