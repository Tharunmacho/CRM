import { ChevronLeft, ChevronRight, ChevronsLeft, ChevronsRight, LayoutGrid, MoreHorizontal, Rows3, type LucideIcon } from 'lucide-react'
import { useEffect, useLayoutEffect, useRef, useState, type CSSProperties, type KeyboardEvent, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { cn } from '@/shared/lib/cn'
import { initials } from '@/shared/lib/format'
import { CountryFlag as SharedCountryFlag } from '@/shared/ui/country-flag'
import { Select } from '@/shared/ui/primitives'
import { trendCaption, type ArrivalTrend } from '@/features/candidates/data-bank-trends'
import { ALL_PAGE_SIZE, allNote, countText, pageList } from '@/features/candidates/pager-model'

/* Small building blocks shared by the Data Bank and Mapped candidates screens (flat reference look). */

export type DotTone = 'ok' | 'warn' | 'bad' | 'info' | 'quiet'

/** Soft-filled status pill: tinted background, coloured text, a tiny dot. */
export function StatusDot({ tone, children, title }: { tone: DotTone; children: ReactNode; title?: string }) {
  return (
    <span className={cn('db-pill', `is-${tone}`)} title={title}>
      <i className="db-pill-dot" aria-hidden="true" />
      <span>{children}</span>
    </span>
  )
}

/** One of six soft hues, chosen from the name so a person keeps the same colour everywhere. */
export const hueOf = (name: string) => name.split('').reduce((total, character) => total + character.charCodeAt(0), 0) % 6

/** Circle with the initials on that person's soft hue. */
export function Avatar({ name, className, size }: { name: string; className?: string; size?: 'sm' }) {
  return <span className={cn('db-avatar', `hue-${hueOf(name)}`, size === 'sm' && 'is-sm', className)} aria-hidden="true">{initials(name)}</span>
}

const STAGE_COUNT = 11

/**
 * The 11-stage tracker: one segment per stage (finished, current, still to come) with the
 * current stage's number and name beside it. Compact enough for a table cell or a card.
 */
export function StageTracker({ stage, title }: { stage: number; title: string }) {
  return (
    <span className="db-track" title={`Stage ${stage} of ${STAGE_COUNT}: ${title}`}>
      <span className="db-track-bar" aria-hidden="true">
        {Array.from({ length: STAGE_COUNT }, (_, index) => (
          <i key={index} className={index + 1 < stage ? 'is-done' : index + 1 === stage ? 'is-now' : undefined} />
        ))}
      </span>
      <span className="db-track-text"><b className="tnum">{stage}</b><span>{title}</span></span>
    </span>
  )
}

/** Avatar tile, name and a muted sub-line: the first column of every list and the head of every card. */
export function Who({ name, sub, title, compact }: { name: string; sub?: ReactNode; title?: string; compact?: boolean }) {
  return (
    <span className={cn('db-who', compact && 'is-compact')}>
      <Avatar name={name} size={compact ? 'sm' : undefined} />
      <span className="db-who-text">
        <strong title={title ?? name}>{name}</strong>
        {sub && <small>{sub}</small>}
      </span>
    </span>
  )
}

/* ---------------------------------------------------------------- KPI cards */

export interface KpiCardItem {
  label: string
  value: number | string
  detail?: string
  icon: LucideIcon
  /** Real arrival history for this slice, when there is some. */
  trend?: ArrivalTrend
  /** Weekly arrivals for the small trend line. */
  spark?: number[]
  /** Colour of the top accent line and the icon circle; primary when omitted. */
  tone?: 'ok' | 'warn' | 'bad' | 'quiet'
  active?: boolean
  onSelect?: () => void
}

function Spark({ points }: { points: number[] }) {
  const max = Math.max(...points, 1)
  const width = 100
  const height = 30
  const step = width / Math.max(points.length - 1, 1)
  const coords = points.map((point, index) => [index * step, height - 3 - (point / max) * (height - 8)] as const)
  const line = coords.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(' ')
  return (
    <svg className="db-kpi-spark" viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" aria-hidden="true" focusable="false">
      <polygon points={`0,${height} ${line} ${width},${height}`} className="db-kpi-spark-fill" />
      <polyline points={line} className="db-kpi-spark-line" vectorEffect="non-scaling-stroke" />
    </svg>
  )
}

/**
 * Key figures that are also the list filter. The selected card gets a dark 1.5px border;
 * the arrow and percent only appear when last month gives a real baseline.
 */
export function KpiCards({ items }: { items: KpiCardItem[] }) {
  return (
    <div className="db-kpis" data-count={items.length} style={{ '--db-kpi-cols': items.length } as CSSProperties}>
      {items.map(({ label, value, detail, icon: Icon, trend, spark, tone, active, onSelect }) => {
        const caption = trend ? trendCaption(trend) : null
        const body = (
          <>
            <span className="db-kpi-top">
              <span className="db-kpi-label">{label}</span>
              <span className="db-kpi-icon" aria-hidden="true"><Icon size={16} strokeWidth={1.8} /></span>
            </span>
            <span className="db-kpi-mid">
              <strong className="db-kpi-value tnum">{value}</strong>
              {spark && spark.some(point => point > 0) && <Spark points={spark} />}
            </span>
            {detail && <span className="db-kpi-detail">{detail}</span>}
            {caption && (
              <span className="db-kpi-foot">
                <em className={`is-${caption.tone}`}>{caption.lead}</em> {caption.rest}
              </span>
            )}
          </>
        )
        return onSelect ? (
          <section
            key={label}
            className={cn('db-kpi is-action', tone && `is-${tone}`, active && 'is-on')}
            role="button"
            tabIndex={0}
            aria-pressed={!!active}
            aria-label={label}
            onClick={onSelect}
            onKeyDown={(event: KeyboardEvent<HTMLElement>) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); onSelect() } }}
          >
            {body}
          </section>
        ) : (
          <section key={label} className={cn('db-kpi', tone && `is-${tone}`)} aria-label={label}>{body}</section>
        )
      })}
    </div>
  )
}

/* ------------------------------------------------------------- view toggle */

export type DbView = 'table' | 'cards'

export function ViewToggle({ view, onChange }: { view: DbView; onChange: (view: DbView) => void }) {
  return (
    <div className="db-seg" role="group" aria-label="View">
      <button type="button" className={cn('db-seg-btn', view === 'table' && 'is-on')} aria-pressed={view === 'table'} title="List view" aria-label="List view" onClick={() => onChange('table')}><Rows3 size={15} /></button>
      <button type="button" className={cn('db-seg-btn', view === 'cards' && 'is-on')} aria-pressed={view === 'cards'} title="Grid view" aria-label="Grid view" onClick={() => onChange('cards')}><LayoutGrid size={15} /></button>
    </div>
  )
}

const VIEW_KEY = 'crm-db-view'
/** The last chosen view survives a reload; a phone starts on cards because a wide list would only scroll sideways. */
export function readView(scope: string): DbView {
  try {
    const stored = window.localStorage.getItem(`${VIEW_KEY}:${scope}`)
    if (stored === 'table' || stored === 'cards') return stored
  } catch { /* storage unavailable: fall through */ }
  return typeof window !== 'undefined' && window.matchMedia('(max-width: 767px)').matches ? 'cards' : 'table'
}
export function saveView(scope: string, view: DbView) {
  try { window.localStorage.setItem(`${VIEW_KEY}:${scope}`, view) } catch { /* ignore */ }
}

/* ------------------------------------------------------------------- pager */

export function Pager({ page, pageSize, total, noun, onPage, onPageSize, sizes = [10, 20, 50], allowAll = false, filtered = true }: {
  /** Zero-based page. */
  page: number
  pageSize: number
  total: number
  noun: string
  onPage: (page: number) => void
  /** Omit to hide the per-page choice. */
  onPageSize?: (size: number) => void
  /** The per-page choices (the Data Bank keeps 10 / 20 / 50). */
  sizes?: readonly number[]
  /** Offer "All" (one request, up to the server's cap, with a note when more exist). */
  allowAll?: boolean
  /** Whether the count describes a narrowed list ("matching") or everything. */
  filtered?: boolean
}) {
  const pageCount = Math.max(1, Math.ceil(total / pageSize))
  const safe = Math.min(page, pageCount - 1)
  const [jump, setJump] = useState('')
  const all = allowAll && pageSize === ALL_PAGE_SIZE
  const go = (next: number) => onPage(Math.min(pageCount - 1, Math.max(0, next)))
  const submitJump = () => { const wanted = Number(jump); if (Number.isFinite(wanted) && wanted >= 1) go(Math.floor(wanted) - 1); setJump('') }
  const choices = [...sizes, ...(sizes.includes(pageSize) || all ? [] : [pageSize])].filter((size) => size !== ALL_PAGE_SIZE).sort((x, y) => x - y)
  const note = all ? allNote(total, noun) : null
  return (
    <div className="db-foot">
      <span className="tnum" aria-live="polite">{countText({ page: safe, pageSize, total, noun, filtered })}{note && <em className="db-foot-note"> {note}</em>}</span>
      <div className="db-pager" aria-label="Pages"
        onKeyDown={event => {
          if (event.target instanceof HTMLInputElement) return
          if (event.key === 'ArrowLeft') { event.preventDefault(); go(safe - 1) } else if (event.key === 'ArrowRight') { event.preventDefault(); go(safe + 1) }
        }}>
        {onPageSize && (
          <Select className="db-pagesize" aria-label={`${noun} per page`} value={all ? 'all' : String(pageSize)} onChange={event => onPageSize(event.target.value === 'all' ? ALL_PAGE_SIZE : Number(event.target.value))}>
            {choices.map(size => <option key={size} value={size}>{size} / page</option>)}
            {allowAll && <option value="all">All</option>}
          </Select>
        )}
        {pageCount > 1 && (
          <>
            <button type="button" className="db-pager-btn" onClick={() => go(0)} disabled={safe === 0} aria-label="First page"><ChevronsLeft size={15} /></button>
            <button type="button" className="db-pager-btn" onClick={() => go(safe - 1)} disabled={safe === 0} aria-label="Previous page"><ChevronLeft size={15} /></button>
            {pageList(safe, pageCount).map((entry, index) => entry === '…'
              ? <span key={`gap${index}`} className="db-pager-gap" aria-hidden="true">…</span>
              : <button key={entry} type="button" className={cn('db-pager-num tnum', entry === safe + 1 && 'is-on')} aria-label={`Page ${entry}`} aria-current={entry === safe + 1 ? 'page' : undefined} onClick={() => go(entry - 1)}>{entry}</button>)}
            <button type="button" className="db-pager-btn" onClick={() => go(safe + 1)} disabled={safe >= pageCount - 1} aria-label="Next page"><ChevronRight size={15} /></button>
            <button type="button" className="db-pager-btn" onClick={() => go(pageCount - 1)} disabled={safe >= pageCount - 1} aria-label="Last page"><ChevronsRight size={15} /></button>
            {pageCount > 7 && (
              <label className="db-pager-go">Go to
                <input type="number" min={1} max={pageCount} value={jump} aria-label="Go to page" placeholder={String(safe + 1)} onChange={event => setJump(event.target.value)} onKeyDown={event => { if (event.key === 'Enter') submitJump() }} onBlur={() => { if (jump) submitJump() }} />
              </label>
            )}
          </>
        )}
        {pageCount <= 1 && <span className="tnum db-pager-text">Page 1 of 1</span>}
      </div>
    </div>
  )
}

/* ------------------------------------------------------------------- flags */

/** Flag of a country by name (files in /flags, any country the shared helper knows), a globe when there is no flag file for it. */
export function CountryFlag({ country }: { country?: string | null }) {
  if (!country) return null
  return <SharedCountryFlag country={country} width={18} height={13} className="db-flag" globeClassName="db-flag-globe" title={country} />
}

/* ---------------------------------------------------------------- row menu */

/** `disabledReason` keeps the item visible but unusable and explains why in its tooltip. */
export interface RowMenuItem { label: string; icon: LucideIcon; onSelect: () => void; disabledReason?: string }

/** The "..." overflow menu of a row: opens as a fixed popover so a card or table edge can never clip it. */
export function RowMenu({ label, items }: { label: string; items: RowMenuItem[] }) {
  const [open, setOpen] = useState(false)
  const [at, setAt] = useState({ top: 0, left: 0 })
  const trigger = useRef<HTMLButtonElement>(null)
  const menu = useRef<HTMLDivElement>(null)
  /* The menu is as tall and wide as its labels need (44px rows on touch screens): once drawn, nudge it back inside the window so no item is ever cut off. */
  useLayoutEffect(() => {
    if (!open || !menu.current) return
    const rect = menu.current.getBoundingClientRect()
    const left = Math.max(8, Math.min(at.left, window.innerWidth - 8 - rect.width))
    const top = Math.max(8, Math.min(at.top, window.innerHeight - 8 - rect.height))
    if (left !== at.left || top !== at.top) setAt({ top, left })
  }, [open, at.left, at.top])
  useEffect(() => {
    if (!open) return
    const close = () => setOpen(false)
    const onKey = (event: globalThis.KeyboardEvent) => { if (event.key === 'Escape') { setOpen(false); trigger.current?.focus() } }
    const onDown = (event: MouseEvent) => { if (!(event.target as HTMLElement).closest('.db-menu, .db-menu-trigger')) setOpen(false) }
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
      <button ref={trigger} type="button" className="db-menu-trigger" title={label} aria-label={label} aria-haspopup="menu" aria-expanded={open}
        onClick={(event) => {
          event.stopPropagation()
          const box = event.currentTarget.getBoundingClientRect()
          const height = items.length * 38 + 12
          setAt({ top: box.bottom + height + 8 > window.innerHeight ? Math.max(8, box.top - height - 4) : box.bottom + 4, left: Math.max(8, box.right - 176) })
          setOpen(value => !value)
        }}>
        <MoreHorizontal size={16} />
      </button>
      {open && createPortal(
        <div ref={menu} className="db-menu" role="menu" style={{ top: at.top, left: at.left }}>
          {items.map(({ label: text, icon: Icon, onSelect, disabledReason }) => (
            <button key={text} type="button" role="menuitem" className="db-menu-item" disabled={Boolean(disabledReason)} title={disabledReason} onClick={(event) => { event.stopPropagation(); setOpen(false); onSelect() }}><Icon size={15} aria-hidden="true" />{text}</button>
          ))}
        </div>,
        document.body,
      )}
    </>
  )
}
