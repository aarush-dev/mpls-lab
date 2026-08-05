import React from 'react';
import { render, screen, fireEvent, within } from '@testing-library/react';
import '@testing-library/jest-dom';
import { createTheme } from '@grafana/data';

import { colorForState, colorForSeverity } from './severity';
import { sortRows, DataTable, Column } from './DataTable';
import { sparkPath } from './Sparkline';
import { gaugeGeometry } from './Gauge';

const theme = createTheme();

describe('severity', () => {
  it('folds both vocabularies onto the same tones (no hardcoded hex)', () => {
    expect(colorForState(theme, 'red').text).toBe(theme.colors.error.text);
    expect(colorForState(theme, 'amber').text).toBe(theme.colors.warning.text);
    expect(colorForState(theme, 'green').text).toBe(theme.colors.success.text);
    expect(colorForState(theme, 'unknown').text).toBe(theme.colors.text.secondary);
    // high state == red severity, etc.
    expect(colorForSeverity(theme, 'high').main).toBe(colorForState(theme, 'red').main);
    expect(colorForSeverity(theme, 'med').main).toBe(colorForState(theme, 'amber').main);
    expect(colorForSeverity(theme, 'low').main).toBe(colorForState(theme, 'green').main);
    expect(colorForState(theme, undefined).main).toBe(theme.colors.text.secondary);
  });
});

interface Row {
  name: string;
  n: number;
}
const cols: Array<Column<Row>> = [
  { key: 'name', header: 'Name', cell: (r) => r.name, sort: (r) => r.name },
  { key: 'n', header: 'N', cell: (r) => r.n, align: 'num', sort: (r) => r.n },
];
const rows: Row[] = [
  { name: 'b', n: 2 },
  { name: 'a', n: 3 },
  { name: 'c', n: 1 },
];

describe('DataTable', () => {
  it('sortRows orders asc/desc by the column accessor', () => {
    expect(sortRows(rows, cols[1], 'asc').map((r) => r.n)).toEqual([1, 2, 3]);
    expect(sortRows(rows, cols[1], 'desc').map((r) => r.n)).toEqual([3, 2, 1]);
    expect(sortRows(rows, undefined, 'asc')).toBe(rows); // no-op without a sortable column
  });

  it('sorts on header click and fires per-row click', () => {
    const onRowClick = jest.fn();
    render(<DataTable columns={cols} rows={rows} rowKey={(r) => r.name} onRowClick={onRowClick} />);
    fireEvent.click(screen.getByText('N'));
    const firstCells = within(screen.getAllByRole('row')[1]).getAllByRole('cell');
    expect(firstCells[0]).toHaveTextContent('c'); // n=1 rises to top asc
    fireEvent.click(firstCells[0]);
    expect(onRowClick).toHaveBeenCalledWith({ name: 'c', n: 1 });
  });
});

describe('Sparkline', () => {
  it('builds a path and splits on nulls', () => {
    expect(sparkPath([0, 10], 100, 20)).toBe('M0.0,20.0 L100.0,0.0');
    expect(sparkPath([], 100, 20)).toBe('');
    // a null gap yields two segments (two M commands)
    expect((sparkPath([1, null, 2], 100, 20).match(/M/g) || []).length).toBe(2);
  });
});

describe('Gauge', () => {
  it('maps value to clamped fill geometry', () => {
    expect(gaugeGeometry(50, 100, 200)).toEqual({ pct: 0.5, fillWidth: 100 });
    expect(gaugeGeometry(150, 100, 200)).toEqual({ pct: 1, fillWidth: 200 }); // clamp high
    expect(gaugeGeometry(-5, 100, 200)).toEqual({ pct: 0, fillWidth: 0 }); // clamp low
    expect(gaugeGeometry(5, 0, 200)).toEqual({ pct: 0, fillWidth: 0 }); // max<=0 safe
  });
});
