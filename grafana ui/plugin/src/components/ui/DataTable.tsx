import React, { useMemo, useState } from 'react';
import { css, cx } from '@emotion/css';
import { GrafanaTheme2 } from '@grafana/data';
import { useStyles2 } from '@grafana/ui';

// Styled, sortable table. A column becomes sortable by giving it a `sort` accessor; clicking its
// header cycles asc -> desc. `.num` right-aligns, `.mono` monospaces. Optional per-row click.
export interface Column<T> {
  key: string;
  header: React.ReactNode;
  cell: (row: T) => React.ReactNode;
  align?: 'num' | 'mono';
  /** sort key; presence makes the header clickable */
  sort?: (row: T) => number | string;
}

interface Props<T> {
  columns: Array<Column<T>>;
  rows: T[];
  rowKey: (row: T) => string;
  onRowClick?: (row: T) => void;
  className?: string;
}

interface SortState {
  key: string;
  dir: 'asc' | 'desc';
}

// Exported for unit test: pure sort so ordering is verifiable without the DOM.
export function sortRows<T>(rows: T[], col: Column<T> | undefined, dir: 'asc' | 'desc'): T[] {
  if (!col?.sort) {
    return rows;
  }
  const sign = dir === 'asc' ? 1 : -1;
  return [...rows].sort((a, b) => {
    const av = col.sort!(a);
    const bv = col.sort!(b);
    if (av < bv) {
      return -sign;
    }
    if (av > bv) {
      return sign;
    }
    return 0;
  });
}

export function DataTable<T>({ columns, rows, rowKey, onRowClick, className }: Props<T>) {
  const styles = useStyles2(getStyles);
  const [sort, setSort] = useState<SortState | null>(null);

  const sorted = useMemo(() => {
    if (!sort) {
      return rows;
    }
    return sortRows(rows, columns.find((c) => c.key === sort.key), sort.dir);
  }, [rows, columns, sort]);

  const toggle = (col: Column<T>) => {
    if (!col.sort) {
      return;
    }
    setSort((s) => (s?.key === col.key ? { key: col.key, dir: s.dir === 'asc' ? 'desc' : 'asc' } : { key: col.key, dir: 'asc' }));
  };

  return (
    <table className={cx(styles.table, className)}>
      <thead>
        <tr>
          {columns.map((c) => (
            <th
              key={c.key}
              className={cx(c.align === 'num' && styles.num, c.sort && styles.sortable)}
              onClick={() => toggle(c)}
              aria-sort={sort?.key === c.key ? (sort.dir === 'asc' ? 'ascending' : 'descending') : undefined}
            >
              {c.header}
              {sort?.key === c.key && <span className={styles.arrow}>{sort.dir === 'asc' ? ' ▲' : ' ▼'}</span>}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {sorted.map((r) => (
          <tr key={rowKey(r)} className={cx(onRowClick && styles.clickable)} onClick={onRowClick ? () => onRowClick(r) : undefined}>
            {columns.map((c) => (
              <td key={c.key} className={cx(c.align === 'num' && styles.num, c.align === 'mono' && styles.mono)}>
                {c.cell(r)}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

const getStyles = (theme: GrafanaTheme2) => ({
  table: css`
    width: 100%;
    border-collapse: collapse;

    th {
      text-align: left;
      padding: ${theme.spacing(1)};
      color: ${theme.colors.text.secondary};
      border-bottom: 1px solid ${theme.colors.border.weak};
      font-weight: ${theme.typography.fontWeightMedium};
    }
    td {
      padding: ${theme.spacing(1)};
      border-bottom: 1px solid ${theme.colors.border.weak};
    }
  `,
  num: css`
    text-align: right;
  `,
  mono: css`
    font-family: ${theme.typography.fontFamilyMonospace};
  `,
  sortable: css`
    cursor: pointer;
    user-select: none;
    &:hover {
      color: ${theme.colors.text.primary};
    }
  `,
  arrow: css`
    font-size: 10px;
  `,
  clickable: css`
    cursor: pointer;
    &:hover {
      background: ${theme.colors.action.hover};
    }
  `,
});
