import { useCallback, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { ArrowRight, CalendarClock, LayoutGrid, List, MoreHorizontal, Pause, Pencil, Play, Trash2 } from 'lucide-react'
import { cn } from '@/shared/lib/cn'
import type { Order } from '@/shared/model/types'
import { dateLabel } from '@/shared/lib/format'
import { daysToDeadline, deadlineNote, ORDER_STATUS_TEXT, orderStatusKey } from '@/features/jobs/order-meta'

/** Soft-filled status pill with a small dot. */
export function OrderStatusText({ order, className }: { order: Pick<Order, 'status' | 'deadline'>; className?: string }) {
  const key = orderStatusKey(order)
  return <span className={cn('jv-status', `s-${key}`, className)}><i aria-hidden="true" />{ORDER_STATUS_TEXT[key]}</span>
}

/** The deadline as a chip: neutral, amber in the last week, red once it has passed. */
export function DueChip({ order, short }: { order: Pick<Order, 'status' | 'deadline'>; short?: boolean }) {
  if (!order.deadline) return <span className="jv-due"><CalendarClock size={14} aria-hidden="true" />No deadline</span>
  const days = daysToDeadline(order.deadline)
  const finished = order.status === 'filled' || order.status === 'closed'
  const tone = finished ? '' : days !== null && days < 0 ? 'is-late' : days !== null && days <= 7 ? 'is-soon' : ''
  const note = deadlineNote(order)
  return <span className={cn("jv-due", tone)} title={note || undefined}><CalendarClock size={14} aria-hidden="true" />{dateLabel(order.deadline)}{!short && note && <small>{note}</small>}</span>
}

/* ---------------------------------------------------------------- List / grid toggle */

export type OrdersLayout = 'list' | 'grid'

/** A remembered per-viewer layout choice; storage may be unavailable, so it only ever degrades to the default. */
export function useStoredLayout(key: string, fallback: OrdersLayout): [OrdersLayout, (value: OrdersLayout) => void] {
  const [layout, setLayout] = useState<OrdersLayout>(() => {
    try { const saved = window.localStorage.getItem(key); return saved === 'list' || saved === 'grid' ? saved : fallback } catch { return fallback }
  })
  const update = useCallback((value: OrdersLayout) => {
    setLayout(value)
    try { window.localStorage.setItem(key, value) } catch { /* the choice just is not remembered */ }
  }, [key])
  return [layout, update]
}

export function LayoutToggle({ value, onChange }: { value: OrdersLayout; onChange: (value: OrdersLayout) => void }) {
  return (
    <div className="jv-seg" role="group" aria-label="Layout">
      <button type="button" aria-pressed={value === 'list'} className={cn(value === 'list' && 'is-on')} onClick={() => onChange('list')} title="List view"><List size={15} aria-hidden="true" />List</button>
      <button type="button" aria-pressed={value === 'grid'} className={cn(value === 'grid' && 'is-on')} onClick={() => onChange('grid')} title="Card view"><LayoutGrid size={15} aria-hidden="true" />Cards</button>
    </div>
  )
}

/* ---------------------------------------------------------------- Row actions */

export interface OrderActionHandlers {
  onOpen: (order: Order) => void
  onEdit: (order: Order) => void
  onSetStatus: (order: Order, status: 'open' | 'on_hold') => void
  onDelete?: (order: Order) => void
}

interface MenuPosition { top: number; left: number }

/** Menu drawn in a portal so a scrolling table or card cannot clip it. With `text` the trigger is a labelled button. */
export function ActionMenu({ label, text, children }: { label: string; text?: string; children: (close: () => void) => ReactNode }) {
  const trigger = useRef<HTMLButtonElement>(null)
  const panel = useRef<HTMLDivElement>(null)
  const [pos, setPos] = useState<MenuPosition | null>(null)
  const close = useCallback(() => setPos(null), [])

  const place = useCallback(() => {
    const rect = trigger.current?.getBoundingClientRect()
    if (!rect) return
    const width = 208
    const height = panel.current?.offsetHeight ?? 180
    const left = Math.max(8, Math.min(window.innerWidth - width - 8, rect.right - width))
    const below = rect.bottom + 6
    const top = below + height > window.innerHeight - 8 ? Math.max(8, rect.top - height - 6) : below
    setPos({ top, left })
  }, [])
  useLayoutEffect(() => { if (pos) place() }, [pos === null]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!pos) return
    const onDown = (event: MouseEvent) => {
      const target = event.target as Node
      if (panel.current?.contains(target) || trigger.current?.contains(target)) return
      close()
    }
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') { close(); trigger.current?.focus() } }
    window.addEventListener('mousedown', onDown)
    window.addEventListener('keydown', onKey)
    window.addEventListener('resize', close)
    window.addEventListener('scroll', close, true)
    return () => {
      window.removeEventListener('mousedown', onDown)
      window.removeEventListener('keydown', onKey)
      window.removeEventListener('resize', close)
      window.removeEventListener('scroll', close, true)
    }
  }, [pos, close])

  return (
    <>
      <button
        ref={trigger}
        type="button"
        className={text ? 'ui-button ui-button-secondary jv-text-menu' : 'jv-icon-btn'}
        aria-label={label}
        title={label}
        aria-haspopup="menu"
        aria-expanded={!!pos}
        onClick={(event) => { event.stopPropagation(); setPos((current) => (current ? null : { top: 0, left: 0 })) }}
      >
        <MoreHorizontal size={16} aria-hidden="true" />{text}
      </button>
      {pos && createPortal(
        <div ref={panel} role="menu" className="jv-menu" style={{ top: pos.top, left: pos.left }} onClick={(event) => event.stopPropagation()}>
          {children(close)}
        </div>,
        document.body,
      )}
    </>
  )
}

export function MenuButton({ icon, danger, onClick, children }: { icon: ReactNode; danger?: boolean; onClick: () => void; children: ReactNode }) {
  return <button type="button" role="menuitem" className={cn('jv-menu-item', danger && 'is-danger')} onClick={onClick}>{icon}{children}</button>
}

/** The bordered arrow that opens an order, plus a menu of quick actions. */
export function OrderRowActions({ order, handlers, name, compact }: { order: Order; handlers: OrderActionHandlers; name: string; compact?: boolean }) {
  return (
    <div className="jv-row-actions" onClick={(event) => event.stopPropagation()}>
      <ActionMenu label={`More actions for ${name}`}>
        {(close) => (
          <>
            <MenuButton icon={<ArrowRight size={15} />} onClick={() => { close(); handlers.onOpen(order) }}>Open order</MenuButton>
            <MenuButton icon={<Pencil size={15} />} onClick={() => { close(); handlers.onEdit(order) }}>Edit order</MenuButton>
            {order.status === 'open' && <MenuButton icon={<Pause size={15} />} onClick={() => { close(); handlers.onSetStatus(order, 'on_hold') }}>Put on hold</MenuButton>}
            {order.status === 'on_hold' && <MenuButton icon={<Play size={15} />} onClick={() => { close(); handlers.onSetStatus(order, 'open') }}>Start matching again</MenuButton>}
            {handlers.onDelete && <MenuButton danger icon={<Trash2 size={15} />} onClick={() => { close(); handlers.onDelete?.(order) }}>Delete order</MenuButton>}
          </>
        )}
      </ActionMenu>
      <button type="button" className={cn('jv-open-btn', compact && 'is-icon')} aria-label={`Open order ${order.ref}`} title="Open order" onClick={() => handlers.onOpen(order)}>
        {!compact && 'Open'}<ArrowRight size={15} aria-hidden="true" />
      </button>
    </div>
  )
}

/** Content width of an element, kept current as it resizes (0 until it has been measured). */
export function useElementWidth(element: HTMLElement | null): number {
  const [width, setWidth] = useState(0)
  useLayoutEffect(() => {
    if (!element) return
    setWidth(Math.round(element.getBoundingClientRect().width))
    if (typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(([entry]) => setWidth(Math.round(entry.contentRect.width)))
    observer.observe(element)
    return () => observer.disconnect()
  }, [element])
  return width
}

/** True while the window is at least `px` wide; tables give way to cards below it. */
export function useMinWidth(px: number): boolean {
  const query = `(min-width: ${px}px)`
  const [matches, setMatches] = useState(() => typeof window === 'undefined' || !window.matchMedia ? true : window.matchMedia(query).matches)
  useEffect(() => {
    if (!window.matchMedia) return
    const media = window.matchMedia(query)
    const update = () => setMatches(media.matches)
    update()
    media.addEventListener('change', update)
    return () => media.removeEventListener('change', update)
  }, [query])
  return matches
}
