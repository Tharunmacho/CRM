import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { ArrowRight, ChevronDown, ChevronLeft, ChevronRight, ChevronUp, ListFilter } from 'lucide-react'
import { cn } from '@/shared/lib/cn'
import { Checkbox } from './primitives'
import { EmptyState } from './display'

export interface Column<T> {
  key: string
  header: string
  /** Value used for sorting and for the per-column filter options. */
  value?: (row: T) => string | number | null | undefined
  render?: (row: T) => ReactNode
  sortable?: boolean
  /** Renders a distinct-value filter dropdown in the header. */
  filterable?: boolean
  width?: string
  /** Never narrower than this (CSS length, e.g. '12rem'); key columns use it so they are not cut. */
  minWidth?: string
  /** Columns marked `low` fold away below 1280px, `medium` below 1024px (put the fact in the identity cell's second line instead). */
  priority?: 'high' | 'medium' | 'low'
  /** A row-actions column: fixed to its content, right-aligned, never wrapped or clipped. */
  actions?: boolean
  align?: 'left' | 'right' | 'center'
  className?: string
}

interface DataTableProps<T> {
  rows: T[]
  columns: Column<T>[]
  rowKey: (row: T) => string
  onRowClick?: (row: T) => void
  selectable?: boolean
  selected?: string[]
  onSelectedChange?: (ids: string[]) => void
  pageSize?: number
  empty?: ReactNode
  /** Extra class on a row, e.g. to tint overdue rows. */
  rowClassName?: (row: T) => string | undefined
  dense?: boolean
  gridLines?: boolean
  /** Appends the reference product's bordered arrow button as the last cell of every row. */
  openColumn?: boolean
  /** Compact density: 13px text and tighter cells, so every column fits (use with column widths). */
  compact?: boolean
  /**
   * Keeps the header row in view while the rows scroll vertically inside a bounded container
   * (header and rows share one scroll box, so they can never misalign). Pass a CSS height with
   * `maxHeight` (default 70vh).
   */
  sticky?: boolean
  maxHeight?: string
  /**
   * Below this viewport width the table becomes stacked label/value cards instead of a squeezed
   * table. 768 (default), 1024 or 1280; `false` keeps a table at every width.
   */
  stackBelow?: 768 | 1024 | 1280 | false
  /** Reset pagination for an external search without losing column filters or sort. */
  resetPageKey?: string
  /** For bounded endpoints: sort/filter/page decisions are delegated to the server. */
  server?: {
    page: number; total: number; onPageChange: (page: number) => void
    sort: { key: string; dir: 'asc' | 'desc' }; onSortChange: (sort: { key: string; dir: 'asc' | 'desc' }) => void
    filters: Record<string, string>; onFilterChange: (key: string, value: string) => void
  }
}

export function DataTable<T>({
  rows,
  columns,
  rowKey,
  onRowClick,
  selectable,
  selected = [],
  onSelectedChange,
  pageSize = 25,
  empty,
  rowClassName,
  dense,
  gridLines = false,
  openColumn = false,
  compact = false,
  sticky = false,
  maxHeight = '70vh',
  stackBelow = 768,
  resetPageKey,
  server,
}: DataTableProps<T>) {
  const [localSort, setLocalSort] = useState<{ key: string; dir: 'asc' | 'desc' } | null>(null)
  const sort = server?.sort ?? localSort
  const [filters, setFilters] = useState<Record<string, string[]>>({})
  const [page, setPage] = useState(0)
  const [openFilter, setOpenFilter] = useState<string | null>(null)
  useEffect(() => { setPage(0) }, [resetPageKey])

  const valueOf = (col: Column<T>, row: T) => {
    const v = col.value?.(row)
    return v == null ? '' : String(v)
  }

  const filtered = useMemo(() => {
    if (server) return rows
    const active = Object.entries(filters).filter(([, v]) => v.length)
    if (!active.length) return rows
    return rows.filter((row) =>
      active.every(([key, values]) => {
        const col = columns.find((c) => c.key === key)
        if (!col) return true
        return values.includes(valueOf(col, row))
      }),
    )
  }, [rows, filters, columns, server])

  const sorted = useMemo(() => {
    if (server) return rows
    if (!sort) return filtered
    const col = columns.find((c) => c.key === sort.key)
    if (!col?.value) return filtered
    return [...filtered].sort((a, b) => {
      const av = col.value!(a)
      const bv = col.value!(b)
      if (av == null) return 1
      if (bv == null) return -1
      const cmp =
        typeof av === 'number' && typeof bv === 'number'
          ? av - bv
          : String(av).localeCompare(String(bv), undefined, { numeric: true })
      return sort.dir === 'asc' ? cmp : -cmp
    })
  }, [filtered, sort, columns, server, rows])

  const total = server?.total ?? sorted.length
  const pageCount = Math.max(1, Math.ceil(total / pageSize))
  const safePage = Math.min(server?.page ?? page, pageCount - 1)
  const pageRows = server ? rows : sorted.slice(safePage * pageSize, safePage * pageSize + pageSize)
  const changePage = (next: number) => server ? server.onPageChange(next) : setPage(next)

  const allOnPageSelected =
    pageRows.length > 0 && pageRows.every((r) => selected.includes(rowKey(r)))

  const toggleAll = () => {
    if (!onSelectedChange) return
    const ids = pageRows.map(rowKey)
    onSelectedChange(
      allOnPageSelected
        ? selected.filter((id) => !ids.includes(id))
        : [...new Set([...selected, ...ids])],
    )
  }

  const distinct = (col: Column<T>) =>
    [...new Set(rows.map((r) => valueOf(col, r)).filter(Boolean))].sort((a, b) =>
      a.localeCompare(b, undefined, { numeric: true }),
    )

  const pad = compact ? 'px-3 py-2.5' : dense ? 'px-4 py-2' : 'px-4 py-3.5'
  const headPad = compact ? 'px-3 py-2.5' : dense ? 'px-4 py-2' : 'px-4 py-3'
  const fixedLayout = columns.some((col) => col.width)
  const rule = gridLines ? 'border-r border-rule-soft last:border-r-0' : ''

  if (!rows.length && !server) {
    return <>{empty ?? <EmptyState title="Nothing here yet" />}</>
  }

  return (
    <div className={cn('ui-data-table min-w-0 max-w-full', compact && 'is-compact', stackBelow && `ui-table-stack-${stackBelow}`)}>
      <div className={cn('ui-table-scroll scrollbar-thin max-w-full overflow-x-auto', sticky && 'is-sticky overflow-y-auto')} style={sticky ? { maxHeight } : undefined}>
        <table className={cn('w-full border-collapse', compact ? 'text-[13px]' : 'text-[14px]', fixedLayout && 'is-fixed')}>
          <thead>
            <tr className="border-b border-rule">
              {selectable && (
                <th className={cn('w-9 text-left', headPad)}>
                  <Checkbox
                    checked={allOnPageSelected}
                    indeterminate={
                      !allOnPageSelected && pageRows.some((row) => selected.includes(rowKey(row)))
                    }
                    onChange={toggleAll}
                    aria-label="Select all rows on this page"
                  />
                </th>
              )}
              {columns.map((col) => {
                const isSorted = sort?.key === col.key
                const active = server ? !!server.filters[col.key] : (filters[col.key] ?? []).length > 0
                return (
                  <th
                    key={col.key}
                    aria-sort={
                      col.sortable
                        ? isSorted
                          ? sort.dir === 'asc' ? 'ascending' : 'descending'
                          : 'none'
                        : undefined
                    }
                    style={{ width: col.actions ? undefined : col.width, minWidth: col.minWidth }}
                    className={cn(
                      'relative align-middle text-[13px] font-medium text-ink-500',
                      headPad,
                      rule,
                      col.priority && `col-p-${col.priority}`,
                      col.actions && 'ui-actions-cell text-right',
                      col.align === 'right'
                        ? 'text-right'
                        : col.align === 'center'
                          ? 'text-center'
                          : 'text-left',
                    )}
                  >
                    <span className="inline-flex items-center gap-1">
                      <button
                        type="button"
                        disabled={!col.sortable}
                        onClick={() => {
                          if (!col.sortable) return
                          const next = { key: col.key, dir: sort?.key === col.key && sort.dir === 'asc' ? 'desc' as const : 'asc' as const }
                          if (server) server.onSortChange(next)
                          else setLocalSort(next)
                        }}
                        className={cn(
                          'text-[13px] font-medium',
                          col.sortable && 'cursor-pointer hover:text-ink',
                          isSorted && 'text-ink',
                        )}
                      >
                        {col.header}
                      </button>
                      {isSorted &&
                        (sort!.dir === 'asc' ? (
                          <ChevronUp className="h-3 w-3 text-ink-500" />
                        ) : (
                          <ChevronDown className="h-3 w-3 text-ink-500" />
                        ))}
                      {col.filterable && (
                        <button
                          type="button"
                          aria-label={`Filter by ${col.header}`}
                          onClick={() => setOpenFilter((k) => (k === col.key ? null : col.key))}
                          className={cn(
                            'ui-th-filter rounded-[4px] p-0.5 transition-colors',
                            active
                              ? 'bg-navy-600 text-[color:var(--on-primary)]'
                              : 'text-ink-400 hover:bg-field hover:text-ink',
                          )}
                        >
                          <ListFilter className="h-3 w-3" />
                        </button>
                      )}
                    </span>

                    {openFilter === col.key && (
                      <div className="absolute left-2 top-full z-30 mt-1 w-56 rounded-[10px] border border-rule bg-white p-1 shadow-pop">
                        {server ? <div className="p-2"><label className="block text-[13px] text-ink-500">Filter {col.header}<input autoFocus value={server.filters[col.key] || ''} onChange={event => server.onFilterChange(col.key, event.target.value)} className="ui-input mt-2 h-9 w-full rounded-control border bg-white px-2.5 text-[14px] text-ink" placeholder={['experience', 'stage'].includes(col.key) ? 'Type a number' : 'Type to filter'} /></label><button className="mt-2 text-[13px] font-medium text-ink underline-offset-2 hover:underline" onClick={() => setOpenFilter(null)}>Done</button></div> : <div className="scrollbar-thin max-h-64 overflow-y-auto">
                          {distinct(col).map((v) => {
                            const on = (filters[col.key] ?? []).includes(v)
                            return (
                              <div key={v} className="rounded-[6px] px-2 py-1.5 hover:bg-field">
                                <Checkbox
                                  checked={on}
                                  label={<span className="truncate">{v}</span>}
                                  onChange={() =>
                                    setFilters((f) => {
                                      const cur = f[col.key] ?? []
                                      const next = on ? cur.filter((x) => x !== v) : [...cur, v]
                                      setPage(0)
                                      return { ...f, [col.key]: next }
                                    })
                                  }
                                />
                              </div>
                            )
                          })}
                        </div>}
                        {active && (
                          <button
                            onClick={() => server ? server.onFilterChange(col.key, '') : setFilters((f) => ({ ...f, [col.key]: [] }))}
                            className="mt-1 w-full border-t border-rule-soft px-2 py-1.5 text-left text-[13px] text-ink-500 hover:text-ink"
                          >
                            Clear filter
                          </button>
                        )}
                      </div>
                    )}
                  </th>
                )
              })}
              {openColumn && <th className={cn('w-14', headPad)} aria-label="Open" />}
            </tr>
          </thead>
          <tbody>
            {pageRows.map((row) => {
              const id = rowKey(row)
              const isSelected = selected.includes(id)
              return (
                <tr
                  key={id}
                  onClick={() => onRowClick?.(row)}
                  tabIndex={onRowClick ? 0 : undefined}
                  onKeyDown={(event) => {
                    if (
                      onRowClick &&
                      event.target === event.currentTarget &&
                      (event.key === 'Enter' || event.key === ' ')
                    ) {
                      event.preventDefault()
                      onRowClick(row)
                    }
                  }}
                  className={cn(
                    'border-b border-rule-soft transition-colors last:border-0',
                    onRowClick && 'cursor-pointer hover:bg-navy-50',
                    isSelected && 'bg-field/50',
                    rowClassName?.(row),
                  )}
                >
                  {selectable && (
                    <td className={cn('align-middle', pad)} data-label="" data-select onClick={(e) => e.stopPropagation()}>
                      <Checkbox
                        checked={isSelected}
                        aria-label={`Select row ${id}`}
                        onChange={() =>
                          onSelectedChange?.(
                            isSelected ? selected.filter((x) => x !== id) : [...selected, id],
                          )
                        }
                      />
                    </td>
                  )}
                  {columns.map((col) => (
                    <td
                      key={col.key}
                      data-label={col.header}
                      style={{ minWidth: col.minWidth }}
                      className={cn(
                        'align-middle text-ink-700',
                        pad,
                        rule,
                        col.priority && `col-p-${col.priority}`,
                        col.actions && 'ui-actions-cell text-right',
                        col.align === 'right' && 'text-right',
                        col.align === 'center' && 'text-center',
                        col.className,
                      )}
                    >
                      {col.render ? col.render(row) : <span className="block max-w-full break-words line-clamp-2" title={valueOf(col, row) || undefined}>{valueOf(col, row) || '—'}</span>}
                    </td>
                  ))}
                  {openColumn && (
                    <td className={cn('ui-actions-cell w-14 align-middle text-right', pad)} data-label="">
                      <RowOpen />
                    </td>
                  )}
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      {!sorted.length && (
        empty ?? <EmptyState
          title="Nothing matches your filters"
          description="Remove a filter to see more results."
        />
      )}

      {sorted.length > 0 && (
        <div className="ui-table-pagination flex items-center justify-between border-t border-rule-soft px-4 py-3 text-[13px] text-ink-500">
          <span className="tnum" aria-live="polite">
            <span className="pagination-label">Showing </span>
            {safePage * pageSize + 1}–{Math.min(total, (safePage + 1) * pageSize)} of{' '}
            {total}
          </span>
          <div className="flex items-center gap-2">
            <button
              onClick={() => changePage(Math.max(0, safePage - 1))}
              disabled={safePage === 0}
              className="ui-page-button flex h-8 w-8 items-center justify-center rounded-control border border-rule bg-white hover:bg-field disabled:opacity-40"
              aria-label="Previous page"
            >
              <ChevronLeft className="h-4 w-4" />
            </button>
            <span className="tnum px-1">
              {safePage + 1} / {pageCount}
            </span>
            <button
              onClick={() => changePage(Math.min(pageCount - 1, safePage + 1))}
              disabled={safePage >= pageCount - 1}
              className="ui-page-button flex h-8 w-8 items-center justify-center rounded-control border border-rule bg-white hover:bg-field disabled:opacity-40"
              aria-label="Next page"
            >
              <ChevronRight className="h-4 w-4" />
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

/** The reference product's bordered arrow, drawn as the last cell of a clickable row. Decorative: the row itself is the control. */
export function RowOpen({ className }: { className?: string }) {
  return (
    <span
      className={cn('ui-row-open inline-flex h-[34px] w-[34px] items-center justify-center rounded-control border border-rule bg-white text-ink-500', className)}
      aria-hidden="true"
    >
      <ArrowRight className="h-4 w-4" />
    </span>
  )
}
