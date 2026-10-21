import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { ChevronDown, ChevronLeft, ChevronRight, ChevronUp, ListFilter } from 'lucide-react'
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

  const pad = dense ? 'px-4 py-2' : 'px-4 py-3.5'
  const rule = gridLines ? 'border-r border-rule-soft last:border-r-0' : ''

  if (!rows.length && !server) {
    return <>{empty ?? <EmptyState title="Nothing here yet" />}</>
  }

  return (
    <div className="ui-data-table">
      <div className="ui-table-scroll scrollbar-thin overflow-x-auto">
        <table className="w-full border-collapse text-sm">
          <thead>
            <tr className="border-b border-rule bg-field/60">
              {selectable && (
                <th className={cn('w-9 text-left', pad)}>
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
                    style={{ width: col.width }}
                    className={cn(
                      'relative whitespace-nowrap font-medium text-ink-500',
                      pad,
                      rule,
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
                          'text-xs font-medium',
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
                            'rounded-[3px] p-0.5 transition-colors',
                            active
                              ? 'bg-navy-100 text-navy-700'
                              : 'text-ink-300 hover:bg-white hover:text-ink-600',
                          )}
                        >
                          <ListFilter className="h-3 w-3" />
                        </button>
                      )}
                    </span>

                    {openFilter === col.key && (
                      <div className="absolute left-2 top-full z-30 mt-1 w-56 rounded-panel border border-rule bg-white p-1 shadow-pop">
                        {server ? <div className="p-2"><label className="block text-xs text-ink-500">Filter {col.header}<input autoFocus value={server.filters[col.key] || ''} onChange={event => server.onFilterChange(col.key, event.target.value)} className="mt-2 w-full rounded-control border border-rule px-2 py-2 text-sm" placeholder={['experience', 'stage'].includes(col.key) ? 'Exact number' : 'Contains…'} /></label><button className="mt-2 text-xs text-navy-600" onClick={() => setOpenFilter(null)}>Done</button></div> : <div className="scrollbar-thin max-h-64 overflow-y-auto">
                          {distinct(col).map((v) => {
                            const on = (filters[col.key] ?? []).includes(v)
                            return (
                              <div key={v} className="px-2 py-1.5 hover:bg-field">
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
                            className="mt-1 w-full border-t border-rule-soft px-2 py-1.5 text-left text-xs text-ink-500 hover:text-ink"
                          >
                            Clear this filter
                          </button>
                        )}
                      </div>
                    )}
                  </th>
                )
              })}
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
                    onRowClick && 'cursor-pointer hover:bg-field/70',
                    isSelected && 'bg-navy-50/60',
                    rowClassName?.(row),
                  )}
                >
                  {selectable && (
                    <td className={pad} onClick={(e) => e.stopPropagation()}>
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
                      className={cn(
                        'text-ink-700',
                        pad,
                        rule,
                        col.align === 'right' && 'text-right',
                        col.align === 'center' && 'text-center',
                        col.className,
                      )}
                    >
                      {col.render ? col.render(row) : valueOf(col, row) || '—'}
                    </td>
                  ))}
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      {!sorted.length && (
        empty ?? <EmptyState
          title="No rows match these filters"
          description="Clear a filter to widen the search."
        />
      )}

      {sorted.length > 0 && (
        <div className="ui-table-pagination flex items-center justify-between border-t border-rule-soft px-3 py-2.5 text-xs text-ink-500">
          <span className="tnum" aria-live="polite">
            <span className="pagination-label">Showing </span>
            {safePage * pageSize + 1}–{Math.min(total, (safePage + 1) * pageSize)} of{' '}
            {total}
          </span>
          <div className="flex items-center gap-1">
            <button
              onClick={() => changePage(Math.max(0, safePage - 1))}
              disabled={safePage === 0}
              className="flex h-7 w-7 items-center justify-center rounded-control hover:bg-field disabled:opacity-40"
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
              className="flex h-7 w-7 items-center justify-center rounded-control hover:bg-field disabled:opacity-40"
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
