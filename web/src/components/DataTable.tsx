import { useMemo, useState, type ReactElement, type ReactNode } from 'react';

export interface Column<T> {
  key: string;
  header: string;
  render?: (row: T) => ReactNode;
  sortValue?: (row: T) => string | number | null | undefined;
  align?: 'left' | 'right';
  width?: string | number;
}

export interface DataTableProps<T> {
  columns: Column<T>[];
  rows: T[];
  rowKey: (row: T) => string;
  rowClass?: (row: T) => string | undefined;
  onRowClick?: (row: T) => void;
  initialSort?: { key: string; dir: 'asc' | 'desc' };
  emptyText?: string;
}

/**
 * Tight hairline observation table: 36px rows, sortable headers,
 * alert rows keep their pale vermilion ground until viewed.
 */
export default function DataTable<T>({ columns, rows, rowKey, rowClass, onRowClick, initialSort, emptyText = '暂无数据' }: DataTableProps<T>): ReactElement {
  const [sortKey, setSortKey] = useState<string | undefined>(initialSort?.key);
  const [dir, setDir] = useState<'asc' | 'desc'>(initialSort?.dir ?? 'asc');

  const sorted = useMemo(() => {
    const col = columns.find((c) => c.key === sortKey);
    if (!col?.sortValue) return rows;
    const copy = [...rows];
    copy.sort((a, b) => {
      const av = col.sortValue!(a);
      const bv = col.sortValue!(b);
      const cmp = typeof av === 'number' && typeof bv === 'number' ? av - bv : String(av ?? '').localeCompare(String(bv ?? ''));
      return dir === 'asc' ? cmp : -cmp;
    });
    return copy;
  }, [rows, columns, sortKey, dir]);

  const toggle = (key: string): void => {
    if (key === sortKey) {
      setDir((d) => (d === 'asc' ? 'desc' : 'asc'));
    } else {
      setSortKey(key);
      setDir('asc');
    }
  };

  return (
    <div className="table-wrap">
    <table className="obs-table">
      <thead>
        <tr>
          {columns.map((col) => (
            <th
              key={col.key}
              className={(col.sortValue ? 'sortable ' : '') + 'col-' + col.key}
              onClick={col.sortValue ? () => toggle(col.key) : undefined}
              style={{ textAlign: col.align ?? 'left', width: col.width }}
              aria-sort={sortKey === col.key ? (dir === 'asc' ? 'ascending' : 'descending') : undefined}
            >
              {col.header}
              {sortKey === col.key ? (dir === 'asc' ? ' ▲' : ' ▼') : ''}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {sorted.length === 0 ? (
          <tr>
            <td colSpan={columns.length} className="empty">{emptyText}</td>
          </tr>
        ) : (
          sorted.map((row) => (
            <tr
              key={rowKey(row)}
              className={rowClass?.(row)}
              onClick={onRowClick ? () => onRowClick(row) : undefined}
            >
              {columns.map((col) => (
                <td key={col.key} className={'col-' + col.key} style={{ textAlign: col.align ?? 'left' }}>
                  {col.render ? col.render(row) : '—'}
                </td>
              ))}
            </tr>
          ))
        )}
      </tbody>
    </table>
    </div>
  );
}
