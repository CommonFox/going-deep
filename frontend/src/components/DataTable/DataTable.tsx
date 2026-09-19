import { useMemo, useState, type ReactNode } from 'react'
import styles from './DataTable.module.css'

export interface Column<T> {
  key: string
  header: string
  sortable?: boolean
  /** Backs both default rendering and sort comparisons. */
  accessor?: (row: T) => string | number | null
  /** Overrides the default `String(accessor(row))` rendering — e.g. to apply an `unknown` tone. */
  render?: (row: T) => ReactNode
}

/** The sortable, narrow-readable data table every real page (#128, #129) renders through, rather
 * than each page building its own. Renders a real <table> above 480px and switches to a stacked
 * label:value card per row below it via CSS — no JS breakpoint logic, no horizontal scroll at
 * 320px (see DataTable.module.css). */
export function DataTable<T>({
  columns,
  rows,
  rowKey,
}: {
  columns: Column<T>[]
  rows: T[]
  rowKey: (row: T, index: number) => string
}) {
  const [sortKey, setSortKey] = useState<string | null>(null)
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('asc')

  const sortedRows = useMemo(() => {
    const column = columns.find((c) => c.key === sortKey)
    if (!column?.accessor) return rows

    // Direction flips the sign of a real comparison, never the null placement — reversing the
    // whole sorted array after the fact (the previous approach) moved nulls from the end to the
    // start on 'desc', which is exactly the placement this rule forbids.
    return [...rows].sort((a, b) => {
      const av = column.accessor!(a)
      const bv = column.accessor!(b)
      if (av == null && bv == null) return 0
      if (av == null) return 1 // nulls sort last regardless of direction
      if (bv == null) return -1
      const comparison = av < bv ? -1 : av > bv ? 1 : 0
      return sortDir === 'asc' ? comparison : -comparison
    })
  }, [rows, sortKey, sortDir, columns])

  function handleSort(column: Column<T>) {
    if (!column.sortable) return
    if (sortKey === column.key) {
      setSortDir((direction) => (direction === 'asc' ? 'desc' : 'asc'))
    } else {
      setSortKey(column.key)
      setSortDir('asc')
    }
  }

  return (
    <div className={styles.wrapper}>
      <table className={styles.table}>
        <thead>
          <tr>
            {columns.map((column) => (
              <th
                key={column.key}
                className="label"
                data-sortable={column.sortable}
                onClick={() => handleSort(column)}
              >
                {column.header}
                {sortKey === column.key ? (sortDir === 'asc' ? ' ▲' : ' ▼') : ''}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sortedRows.map((row, index) => (
            <tr key={rowKey(row, index)} className={styles.row}>
              {columns.map((column) => (
                <td key={column.key} className={`mono ${styles.cell}`} data-label={column.header}>
                  {column.render ? column.render(row) : String(column.accessor?.(row) ?? '')}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
