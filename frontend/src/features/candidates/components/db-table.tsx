import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from 'react'
import { ChevronDown, ChevronRight, ChevronUp, ChevronsUpDown } from 'lucide-react'
import { cn } from '@/shared/lib/cn'
import { Checkbox } from '@/shared/ui/primitives'
import '@/styles/db-fit.css'

export interface DbColumn<T> {
  key: string
  header: string
  /** Server/client sort key; the header becomes a sort button when set. */
  sortKey?: string
  /** Design width in px. Classic tables turn the widths into shares so they always add up to 100%; the grid table uses them as minimum widths. */
  width: number
  /** Cell alignment. The grid table centres by default. */
  align?: 'left' | 'center' | 'right'
  /** Grid table only: pin the column to the left or right edge while the table scrolls sideways. */
  sticky?: 'start' | 'end'
  /** Fit-to-screen tables: when the columns do not fit, the column with the highest number is hidden first (column-fit.ts). Unset = always shown. */
  optional?: number
  /** With `optional`: the column stays hidden until the person switches it on in the Columns menu. */
  off?: boolean
  /** Columns menu group. */
  group?: string
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
  /**
   * 'grid' is the reference-style data table: centred cells, hairline column dividers, zebra rows, roomy two-line cells,
   * a sticky header and pinned edge columns inside its own scroll box. The default keeps the compact fixed-share table.
   */
  variant?: 'classic' | 'grid'
  /**
   * Grid only. Fit-to-screen: one table that is always exactly as wide as its box. Widths are shares, never minimums; nothing is
   * pinned and there is no sideways scroll. The row checkbox moves into the first column's cell, so the caller picks the columns
   * that fit (column-fit.ts) and the rest are reachable from the Columns menu, the drawer and the cards.
   */
  fit?: boolean
  /** Fit mode: an expandable row. `expandedIds` are the open rows, `onToggleExpand` flips one; `renderExpanded` is the panel under the row. */
  expandedIds?: string[]
  onToggleExpand?: (id: string) => void
  renderExpanded?: (row: T) => ReactNode
}

const CHECK = 32
const GRID_CHECK = 42

/** Whether the scroll box has more to show on its left / right, kept current as it scrolls or resizes. */
function useScrollEdges(active: boolean) {
  const ref = useRef<HTMLDivElement>(null)
  const [edges, setEdges] = useState({ start: false, end: false, width: 0 })
  useEffect(() => {
    const box = ref.current
    if (!box) return
    const read = () => {
      const start = box.scrollLeft > 1
      const end = box.scrollLeft + box.clientWidth < box.scrollWidth - 1
      const width = box.clientWidth
      setEdges(current => (current.start === start && current.end === end && current.width === width ? current : { start, end, width }))
    }
    read()
    box.addEventListener('scroll', read, { passive: true })
    if (typeof ResizeObserver === 'undefined') return () => box.removeEventListener('scroll', read)
    const observer = new ResizeObserver(read)
    observer.observe(box)
    if (box.firstElementChild) observer.observe(box.firstElementChild)
    return () => { box.removeEventListener('scroll', read); observer.disconnect() }
  }, [active])
  return [ref, edges] as const
}

/**
 * One grid for header and body. Classic: fixed layout, equal cell padding, no inner horizontal scroll, long values truncate
 * in their own cell. Grid: see `variant`. Rows are keyboard-focusable and open on Enter or Space when `onRowClick` is set.
 */
export function DbTable<T>({ rows, columns, rowKey, onRowClick, sort, onSort, selectable, selected = [], onSelectedChange, rowClassName, label, variant = 'classic', fit = false, expandedIds, onToggleExpand, renderExpanded }: Props<T>) {
  const grid = variant === 'grid'
  // A fit table whose columns all state their alignment (the Data Bank) is a flush-left table: header and cells share one padding and one alignment.
  const flush = grid && fit && columns.length > 0 && columns.every(column => column.align === 'left' || column.align === 'right')
  const merged = grid && fit && !!selectable  // fit mode: the checkbox lives inside the first column
  const expandable = grid && fit && !!renderExpanded && !!onToggleExpand
  const [scrollRef, edges] = useScrollEdges(grid)
  const ids = rows.map(rowKey)
  const checkWidth = merged ? 0 : grid ? GRID_CHECK : CHECK
  const total = columns.reduce((sum, column) => sum + column.width, selectable ? checkWidth : 0)
  const share = (width: number) => `${((width / total) * 100).toFixed(3)}%`
  const allSelected = ids.length > 0 && ids.every(id => selected.includes(id))
  const someSelected = !allSelected && ids.some(id => selected.includes(id))

  // Pinned columns: the left ones sit after the checkbox, the right ones count from the far edge.
  // Left columns are only pinned while they leave most of the scroll box free (about 45%), so a narrow screen never loses the table to them.
  const starts: DbColumn<T>[] = []
  let pinned = selectable ? checkWidth : 0
  for (const column of columns) {
    if (fit || column.sticky !== 'start' || (edges.width > 0 && pinned + column.width > edges.width * 0.45)) break
    starts.push(column)
    pinned += column.width
  }
  const ends = fit ? [] : columns.filter(column => column.sticky === 'end')
  const offsets = new Map<string, number>()
  let left = selectable && grid && starts.length ? checkWidth : 0
  for (const column of starts) { offsets.set(column.key, left); left += column.width }
  let right = 0
  for (const column of [...ends].reverse()) { offsets.set(column.key, right); right += column.width }
  const lastStart = starts[starts.length - 1]?.key
  const firstEnd = ends[0]?.key
  const checkPinned = grid && !!selectable && !merged && starts.length > 0
  const cellClass = (column: DbColumn<T>) => cn(
    column.align === 'right' && 'is-right',
    grid && (column.align ?? 'center') === 'center' && 'is-center',
    grid && starts.includes(column) && 'is-pin-start',
    grid && !fit && column.sticky === 'end' && 'is-pin-end',
    grid && edges.start && column.key === lastStart && 'is-edge',
    grid && edges.end && column.key === firstEnd && 'is-edge',
  )
  const cellStyle = (column: DbColumn<T>): CSSProperties | undefined => {
    if (!grid || !offsets.has(column.key)) return undefined
    return column.sticky === 'start' ? { left: offsets.get(column.key) } : { right: offsets.get(column.key) }
  }

  const table = (
    <table className={cn('db-tbl', grid && 'is-grid', fit && grid && 'is-fit', flush && 'is-bank')} aria-label={label} style={grid && !fit ? { minWidth: total } : undefined}>
      <colgroup>
        {selectable && !merged && <col style={{ width: grid ? checkWidth : share(checkWidth) }} />}
        {columns.map(column => <col key={column.key} style={{ width: grid && !fit ? column.width : share(column.width) }} />)}
      </colgroup>
      <thead>
        <tr>
          {selectable && !merged && (
            <th className={cn('db-th-check', checkPinned && 'is-pin-start')} style={checkPinned ? { left: 0 } : undefined}>
              <Checkbox checked={allSelected} indeterminate={someSelected} aria-label="Select all rows on this page"
                onChange={() => onSelectedChange?.(allSelected ? selected.filter(id => !ids.includes(id)) : [...new Set([...selected, ...ids])])} />
            </th>
          )}
          {columns.map((column, index) => {
            const active = !!column.sortKey && sort?.key === column.sortKey
            const selectAll = merged && index === 0
            const selectAllBox = (
              <Checkbox checked={allSelected} indeterminate={someSelected} aria-label="Select all rows on this page"
                onChange={() => onSelectedChange?.(allSelected ? selected.filter(id => !ids.includes(id)) : [...new Set([...selected, ...ids])])} />
            )
            const label = column.sortKey
              ? <button type="button" className={cn('db-th-sort', active && 'is-on')} onClick={() => onSort?.(column.sortKey!)}><span className="db-th-label">{column.header}</span>{fit ? <ChevronsUpDown className="db-th-caret is-idle" size={12} aria-hidden="true" /> : active ? (sort!.dir === 'asc' ? <ChevronUp size={13} aria-hidden="true" /> : <ChevronDown size={13} aria-hidden="true" />) : null}</button>
              : column.header || <span className="sr-only">Actions</span>
            return (
              <th key={column.key} className={cn(cellClass(column), (selectAll || (flush && expandable && index === 0)) && 'is-select')} style={cellStyle(column)}
                aria-sort={column.sortKey ? (active ? (sort!.dir === 'asc' ? 'ascending' : 'descending') : 'none') : undefined}>
                {selectAll && !flush
                  ? selectAllBox
                  : flush && index === 0 && (selectAll || expandable)
                  ? <span className="db-selcell">{expandable && <span className="db-expand-gap" aria-hidden="true" />}{selectAll && selectAllBox}{label}</span>
                  : label}
              </th>
            )
          })}
        </tr>
      </thead>
      <tbody>
        {rows.map(row => {
          const id = rowKey(row)
          const open = expandable && !!expandedIds?.includes(id)
          return [
            <tr key={id} className={cn(onRowClick && 'is-clickable', selected.includes(id) && 'is-selected', open && 'is-open', rowClassName?.(row))}
              onClick={() => onRowClick?.(row)}
              tabIndex={onRowClick ? 0 : undefined}
              onKeyDown={event => { if (onRowClick && event.target === event.currentTarget && (event.key === 'Enter' || event.key === ' ')) { event.preventDefault(); onRowClick(row) } }}>
              {selectable && !merged && (
                <td className={cn('db-td-check', checkPinned && 'is-pin-start')} style={checkPinned ? { left: 0 } : undefined} onClick={event => event.stopPropagation()}>
                  <Checkbox checked={selected.includes(id)} aria-label="Select this row"
                    onChange={() => onSelectedChange?.(selected.includes(id) ? selected.filter(x => x !== id) : [...selected, id])} />
                </td>
              )}
              {columns.map((column, index) => (
                <td key={column.key} className={cn(cellClass(column), (merged || expandable) && index === 0 && 'is-select')} style={cellStyle(column)}>
                  {(merged || expandable) && index === 0
                    ? (
                      <span className="db-selcell" onClick={event => event.stopPropagation()}>
                        {expandable && (
                          <button type="button" className={cn('db-expand', open && 'is-open')} aria-expanded={open} aria-label={open ? 'Hide all fields' : 'Show all fields'} title={open ? 'Hide all fields' : 'Show all fields'} onClick={() => onToggleExpand?.(id)}>
                            <ChevronRight size={15} aria-hidden="true" />
                          </button>
                        )}
                        {merged && (
                          <Checkbox checked={selected.includes(id)} aria-label="Select this row"
                            onChange={() => onSelectedChange?.(selected.includes(id) ? selected.filter(x => x !== id) : [...selected, id])} />
                        )}
                        {column.render(row)}
                      </span>
                    )
                    : column.render(row)}
                </td>
              ))}
            </tr>,
            open ? (
              <tr key={`${id}:more`} className="db-xrow">
                <td colSpan={columns.length + (selectable && !merged ? 1 : 0)}>{renderExpanded!(row)}</td>
              </tr>
            ) : null,
          ]
        })}
      </tbody>
    </table>
  )
  if (!grid) return table
  return (
    <div className="db-scroll" ref={scrollRef} data-start={edges.start || undefined} data-end={edges.end || undefined} role="region" aria-label={`${label} (scrolls sideways when it is wider than the screen)`}>
      {table}
    </div>
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

/** The grid table scrolls sideways with pinned edge columns; below this width it still gives way to cards. */
export const GRID_TABLE_MIN_WIDTH = 720
