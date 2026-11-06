import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { ArrowRight, ChevronDown, ChevronLeft, ChevronRight, ChevronUp, ChevronsUpDown, ListFilter, MoreHorizontal, type LucideIcon } from 'lucide-react'
import { cn } from '@/shared/lib/cn'
import { Checkbox } from './primitives'
import { EmptyState } from './display'
import '@/styles/data-table.css'

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
  /**
   * Header and cell alignment. Short values read best centred (the default), so every column is centred except the
   * first one, which carries the record's identity and starts on the left. Set 'left' on long text columns.
   */
  align?: 'left' | 'right' | 'center'
  /**
   * Keeps the column in view while the table scrolls sideways. The first column and the actions column pin themselves;
   * use 'none' to opt out.
   */
  pin?: 'start' | 'end' | 'none'
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
  /** Shows shaped placeholder rows instead of the rows (first load). */
  loading?: boolean
  /** Opts out of the shared grid look (header band, column dividers, zebra rows) for a table that needs the plain list. */
  plain?: boolean
  /** Zebra stripes (default true). */
  zebra?: boolean
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
  loading = false,
  plain = false,
  zebra = true,
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

  const scrollRef = useRef<HTMLDivElement>(null)
  const edgeKey = `${pageRows.length}:${columns.length}:${stackBelow}`
  useEffect(() => {
    const el = scrollRef.current
    if (!el) return
    const update = () => {
      const max = el.scrollWidth - el.clientWidth
      el.dataset.edgeStart = el.scrollLeft > 2 ? 'true' : 'false'
      el.dataset.edgeEnd = max > 2 && el.scrollLeft < max - 2 ? 'true' : 'false'
    }
    update()
    el.addEventListener('scroll', update, { passive: true })
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(update)
    observer?.observe(el)
    const table = el.querySelector('table')
    if (table) observer?.observe(table)
    return () => { el.removeEventListener('scroll', update); observer?.disconnect() }
  }, [edgeKey])

  const grid = !plain
  const pad = grid ? '' : compact ? 'px-3 py-2.5' : dense ? 'px-4 py-2' : 'px-4 py-3.5'
  const headPad = grid ? '' : compact ? 'px-3 py-2.5' : dense ? 'px-4 py-2' : 'px-4 py-3'
  const fixedLayout = columns.some((col) => col.width)
  const rule = !grid && gridLines ? 'border-r border-rule-soft last:border-r-0' : ''
  const alignOf = (col: Column<T>, index: number): 'left' | 'center' | 'right' =>
    col.actions ? 'center' : col.align ?? (index === 0 ? 'left' : 'center')
  const pinOf = (col: Column<T>, index: number): 'start' | 'end' | undefined => {
    if (!grid || col.pin === 'none') return undefined
    if (col.pin) return col.pin
    if (col.actions) return 'end'
    return index === 0 && columns.length > 2 ? 'start' : undefined
  }
  const widthOf = (col: Column<T>) => {
    const raw = col.minWidth ?? col.width ?? ''
    const px = /^([d.]+)px$/.exec(raw)
    if (px) return Number(px[1])
    const rem = /^([d.]+)rem$/.exec(raw)
    if (rem) return Number(rem[1]) * 16
    return col.actions ? 96 : 120
  }
  const minWidthFor = (skip: (col: Column<T>) => boolean) =>
    columns.filter((col) => !skip(col)).reduce((sum, col) => sum + widthOf(col), 0) + (selectable ? 44 : 0) + (openColumn ? 64 : 0)
  const pinStyle = (pin: 'start' | 'end' | undefined): React.CSSProperties | undefined =>
    pin === 'start' ? { left: selectable ? 44 : 0 } : pin === 'end' ? { right: 0 } : undefined
  const tableVars = grid
    ? ({
        '--tbl-min': `${minWidthFor(() => false)}px`,
        '--tbl-min-md': `${minWidthFor((col) => col.priority === 'low')}px`,
        '--tbl-min-sm': `${minWidthFor((col) => col.priority === 'low' || col.priority === 'medium')}px`,
      } as React.CSSProperties)
    : undefined
  const skeletonRows = Array.from({ length: Math.min(pageSize, 6) })

  if (!rows.length && !server && !loading) {
    return <>{empty ?? <EmptyState title="Nothing here yet" />}</>
  }

  return (
    <div className={cn('ui-data-table min-w-0 max-w-full', compact && 'is-compact', dense && 'is-dense', grid ? 'is-grid' : 'is-plain', grid && zebra && 'is-zebra', loading && 'is-loading', stackBelow && `ui-table-stack-${stackBelow}`)}>
      <div ref={scrollRef} data-edge-start="false" data-edge-end="false" className={cn('ui-table-scroll scrollbar-thin max-w-full overflow-x-auto', sticky && 'is-sticky overflow-y-auto')} style={sticky ? { maxHeight } : undefined}>
        <table className={cn('w-full border-collapse', compact ? 'text-[13px]' : 'text-[14px]', fixedLayout && 'is-fixed')} style={tableVars} aria-busy={loading || undefined}>
          <thead>
            <tr className="border-b border-rule">
              {selectable && (
                <th className={cn('w-9 text-left', grid && 'ui-select-cell is-pin-start', headPad)}>
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
              {columns.map((col, colIndex) => {
                const al = alignOf(col, colIndex)
                const pin = pinOf(col, colIndex)
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
                    style={{ width: col.actions ? undefined : col.width, minWidth: col.minWidth, ...pinStyle(pin) }}
                    className={cn(
                      'relative align-middle text-[13px] font-medium text-ink-500',
                      headPad,
                      rule,
                      col.priority && `col-p-${col.priority}`,
                      col.actions && 'ui-actions-cell',
                      grid
                        ? [`ui-c-${al}`, pin && `is-pin-${pin}`, col.sortable && 'is-sortable', isSorted && 'is-sorted']
                        : [col.actions && 'text-right', col.align === 'right' ? 'text-right' : col.align === 'center' ? 'text-center' : 'text-left'],
                    )}
                  >
                    <span className="ui-th-inner inline-flex items-center gap-1">
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
                      {isSorted
                        ? (sort!.dir === 'asc' ? (
                          <ChevronUp className="h-3 w-3 text-ink-500" />
                        ) : (
                          <ChevronDown className="h-3 w-3 text-ink-500" />
                        ))
                        : grid && col.sortable && <ChevronsUpDown className="ui-sort-hint h-3 w-3" aria-hidden="true" />}
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
              {openColumn && <th className={cn('w-14', grid && 'ui-actions-cell ui-c-center is-pin-end', headPad)} style={grid ? pinStyle('end') : undefined} aria-label="Open" />}
            </tr>
          </thead>
          <tbody>
            {loading && skeletonRows.map((_, rowIndex) => (
              <tr key={`skeleton-${rowIndex}`} className="ui-skel-row" aria-hidden="true">
                {selectable && <td className="ui-select-cell is-pin-start" data-label="" />}
                {columns.map((col, colIndex) => (
                  <td key={col.key} data-label={col.header} className={cn(`ui-c-${alignOf(col, colIndex)}`, col.priority && `col-p-${col.priority}`, col.actions && 'ui-actions-cell')}>
                    <span className="ui-skel" style={{ width: `${46 + ((rowIndex * 7 + colIndex * 13) % 40)}%` }} />
                  </td>
                ))}
                {openColumn && <td className="ui-actions-cell ui-c-center" data-label="" />}
              </tr>
            ))}
            {!loading && pageRows.map((row) => {
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
                    <td className={cn('align-middle', grid && 'ui-select-cell is-pin-start', pad)} data-label="" data-select onClick={(e) => e.stopPropagation()}>
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
                  {columns.map((col, colIndex) => {
                    const al = alignOf(col, colIndex)
                    const pin = pinOf(col, colIndex)
                    return (
                    <td
                      key={col.key}
                      data-label={col.header}
                      style={{ minWidth: col.minWidth, ...pinStyle(pin) }}
                      className={cn(
                        'align-middle text-ink-700',
                        pad,
                        rule,
                        col.priority && `col-p-${col.priority}`,
                        col.actions && 'ui-actions-cell',
                        grid
                          ? [`ui-c-${al}`, pin && `is-pin-${pin}`]
                          : [col.actions && 'text-right', col.align === 'right' && 'text-right', col.align === 'center' && 'text-center'],
                        col.className,
                      )}
                    >
                      {col.render ? col.render(row) : <span className="block max-w-full break-words line-clamp-2" title={valueOf(col, row) || undefined}>{valueOf(col, row) || '—'}</span>}
                    </td>
                    )
                  })}
                  {openColumn && (
                    <td className={cn('ui-actions-cell w-14 align-middle', grid ? 'ui-c-center is-pin-end' : 'text-right', pad)} style={grid ? pinStyle('end') : undefined} data-label="">
                      <RowOpen />
                    </td>
                  )}
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      {!loading && !sorted.length && (
        empty ?? <EmptyState
          title="Nothing matches your filters"
          description="Remove a filter to see more results."
        />
      )}

      {!loading && sorted.length > 0 && (
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

export interface RowMenuItem {
  label: string
  icon?: LucideIcon
  onSelect: () => void
  tone?: 'default' | 'danger'
  /** Keeps the item visible but unusable, with the reason as its tooltip. */
  disabledReason?: string
}

/**
 * The three-dot actions menu of a table row. It opens as a fixed popover in a portal, so the table's scroll area
 * and the card edge can never clip it, and it closes on scroll, resize, Escape and outside clicks.
 */
export function RowMenu({ label = 'Row actions', items }: { label?: string; items: RowMenuItem[] }) {
  const [open, setOpen] = useState(false)
  const [at, setAt] = useState({ top: 0, left: 0 })
  const trigger = useRef<HTMLButtonElement>(null)
  useEffect(() => {
    if (!open) return
    const close = () => setOpen(false)
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') { setOpen(false); trigger.current?.focus() } }
    const onDown = (event: MouseEvent) => { if (!(event.target as HTMLElement).closest('.ui-row-menu, .ui-row-menu-trigger')) setOpen(false) }
    window.addEventListener('scroll', close, true)
    window.addEventListener('resize', close)
    window.addEventListener('keydown', onKey)
    window.addEventListener('mousedown', onDown)
    return () => {
      window.removeEventListener('scroll', close, true)
      window.removeEventListener('resize', close)
      window.removeEventListener('keydown', onKey)
      window.removeEventListener('mousedown', onDown)
    }
  }, [open])
  if (items.length === 0) return null
  return (
    <>
      <button
        ref={trigger}
        type="button"
        className="ui-row-menu-trigger"
        title={label}
        aria-label={label}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={(event) => {
          event.stopPropagation()
          const box = event.currentTarget.getBoundingClientRect()
          const height = items.length * 38 + 12
          setAt({ top: box.bottom + height + 8 > window.innerHeight ? Math.max(8, box.top - height - 4) : box.bottom + 4, left: Math.max(8, box.right - 184) })
          setOpen((value) => !value)
        }}
      >
        <MoreHorizontal size={16} />
      </button>
      {open && createPortal(
        <div className="ui-row-menu" role="menu" style={{ top: at.top, left: at.left }}>
          {items.map(({ label: text, icon: Icon, onSelect, tone, disabledReason }) => (
            <button
              key={text}
              type="button"
              role="menuitem"
              className={cn('ui-row-menu-item', tone === 'danger' && 'is-danger')}
              disabled={Boolean(disabledReason)}
              title={disabledReason}
              onClick={(event) => { event.stopPropagation(); setOpen(false); onSelect() }}
            >
              {Icon && <Icon size={15} aria-hidden="true" />}
              {text}
            </button>
          ))}
        </div>,
        document.body,
      )}
    </>
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
