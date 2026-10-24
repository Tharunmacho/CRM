import { useEffect, useState, type ReactNode } from 'react'
import { ChevronDown, ChevronUp } from 'lucide-react'
import { cn } from '@/shared/lib/cn'
import { Checkbox } from '@/shared/ui/primitives'

export interface DbColumn<T> {
  key: string
  header: string
  /** Server/client sort key; the header becomes a sort button when set. */
  sortKey?: string
  /** Design width in px; the columns are turned into shares of the table so they always add up to 100%. */
  width: number
  align?: 'left' | 'right'
  render: (row: T) => ReactNode
}

interface Props<T> {
  rows: T[]
  columns: DbColumn<T>[]
  rowKey: (row: T) => string
  onRowClick?: (row: T) => void
  sort?: { key: string; dir: 'asc' | 'desc' } | null
  onSort?: (key: string) => void
  selectable?: boolean
  selected?: string[]
  onSelectedChange?: (ids: string[]) => void
  rowClassName?: (row: T) => string | undefined
  label: string
}

/**
 * One grid for header and body: fixed layout, equal cell padding, no pinned columns, no inner
 * horizontal scroll. Long values truncate inside their own cell (and carry a title).
 */
export function DbTable<T>({ rows, columns, rowKey, onRowClick, sort, onSort, selectable, selected = [], onSelectedChange, rowClassName, label }: Props<T>) {
  const ids = rows.map(rowKey)
  const CHECK = 32
  const total = columns.reduce((sum, column) => sum + column.width, selectable ? CHECK : 0)
  const share = (width: number) => `${((width / total) * 100).toFixed(3)}%`
  const allSelected = ids.length > 0 && ids.every(id => selected.includes(id))
  const someSelected = !allSelected && ids.some(id => selected.includes(id))
  return (
    <table className="db-tbl" aria-label={label}>
      <colgroup>
        {selectable && <col style={{ width: share(CHECK) }} />}
        {columns.map(column => <col key={column.key} style={{ width: share(column.width) }} />)}
      </colgroup>
      <thead>
        <tr>
          {selectable && (
            <th className="db-th-check">
              <Checkbox checked={allSelected} indeterminate={someSelected} aria-label="Select all rows on this page"
                onChange={() => onSelectedChange?.(allSelected ? selected.filter(id => !ids.includes(id)) : [...new Set([...selected, ...ids])])} />
            </th>
          )}
          {columns.map(column => {
            const active = !!column.sortKey && sort?.key === column.sortKey
            return (
              <th key={column.key} className={cn(column.align === 'right' && 'is-right')}
                aria-sort={column.sortKey ? (active ? (sort!.dir === 'asc' ? 'ascending' : 'descending') : 'none') : undefined}>
                {column.sortKey
                  ? <button type="button" className={cn('db-th-sort', active && 'is-on')} onClick={() => onSort?.(column.sortKey!)}>{column.header}{active && (sort!.dir === 'asc' ? <ChevronUp size={13} aria-hidden="true" /> : <ChevronDown size={13} aria-hidden="true" />)}</button>
                  : column.header || <span className="sr-only">Actions</span>}
              </th>
            )
          })}
        </tr>
      </thead>
      <tbody>
        {rows.map(row => {
          const id = rowKey(row)
          return (
            <tr key={id} className={cn(onRowClick && 'is-clickable', selected.includes(id) && 'is-selected', rowClassName?.(row))}
              onClick={() => onRowClick?.(row)}
              tabIndex={onRowClick ? 0 : undefined}
              onKeyDown={event => { if (onRowClick && event.target === event.currentTarget && (event.key === 'Enter' || event.key === ' ')) { event.preventDefault(); onRowClick(row) } }}>
              {selectable && (
                <td className="db-td-check" onClick={event => event.stopPropagation()}>
                  <Checkbox checked={selected.includes(id)} aria-label="Select this row"
                    onChange={() => onSelectedChange?.(selected.includes(id) ? selected.filter(x => x !== id) : [...selected, id])} />
                </td>
              )}
              {columns.map(column => <td key={column.key} className={cn(column.align === 'right' && 'is-right')}>{column.render(row)}</td>)}
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}

/** Width of an element, kept current; used to swap a table for stacked cards when it would not fit. */
export function useElementWidth<E extends HTMLElement>() {
  const [element, setElement] = useState<E | null>(null)
  const [width, setWidth] = useState(0)
  useEffect(() => {
    if (!element) return
    const read = () => setWidth(Math.round(element.getBoundingClientRect().width))
    read()
    if (typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(read)
    observer.observe(element)
    return () => observer.disconnect()
  }, [element])
  return [setElement, width] as const
}

/** Below this card width the eleven columns would no longer fit without scrolling, so cards take over. */
export const TABLE_MIN_WIDTH = 1040
