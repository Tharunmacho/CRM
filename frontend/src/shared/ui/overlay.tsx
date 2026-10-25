import { useEffect, useRef, useState, type ReactNode, type RefObject } from 'react'
import { createPortal } from 'react-dom'
import { X } from 'lucide-react'
import { cn } from '@/shared/lib/cn'
import { IconButton } from './primitives'

/* ----------------------------------------------------------- Escape hook */

const openModals: HTMLElement[] = []
let originalOverflow = ''

function useDismiss(open: boolean, onClose: () => void, root: RefObject<HTMLDivElement>) {
  const close = useRef(onClose)
  close.current = onClose
  useEffect(() => {
    const element = root.current
    if (!open || !element) return
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null
    if (!openModals.length) {
      originalOverflow = document.body.style.overflow
      document.body.style.overflow = 'hidden'
    }
    openModals.push(element)
    const focusable = () => Array.from(element.querySelectorAll<HTMLElement>(
      'button:not([disabled]), a[href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex="0"]',
    )).filter((item) => item.getClientRects().length > 0 && !item.closest('[aria-hidden="true"]'))
    const frame = requestAnimationFrame(() => {
      if (!element.contains(document.activeElement)) (element.querySelector<HTMLElement>('[autofocus]') || focusable()[0] || element).focus()
    })
    const onKey = (e: KeyboardEvent) => {
      if (openModals[openModals.length - 1] !== element || document.querySelector('.custom-select-popup')) return
      if (e.key === 'Escape') { e.preventDefault(); close.current() }
      if (e.key === 'Tab') {
        const items = focusable()
        const first = items[0]
        const last = items[items.length - 1]
        if (!first) { e.preventDefault(); element.focus() }
        else if (e.shiftKey && (document.activeElement === first || !element.contains(document.activeElement))) { e.preventDefault(); last.focus() }
        else if (!e.shiftKey && (document.activeElement === last || !element.contains(document.activeElement))) { e.preventDefault(); first.focus() }
      }
    }
    document.addEventListener('keydown', onKey)
    return () => {
      cancelAnimationFrame(frame)
      document.removeEventListener('keydown', onKey)
      const index = openModals.indexOf(element)
      if (index >= 0) openModals.splice(index, 1)
      if (!openModals.length) document.body.style.overflow = originalOverflow
      if (previousFocus?.isConnected) previousFocus.focus()
    }
  }, [open, root])
}

/* ---------------------------------------------------------------- Dialog */

export function Dialog({
  open,
  onClose,
  title,
  description,
  children,
  footer,
  width = 'md',
  scroll = 'inside',
  appearance = 'default',
}: {
  open: boolean
  onClose: () => void
  title: string
  description?: string
  children: ReactNode
  footer?: ReactNode
  /** `clean` lightens the backdrop and keeps every part of the card white. */
  appearance?: 'default' | 'clean'
  /** `full` takes the whole window: the header stays put and the body scrolls under it. */
  width?: 'sm' | 'md' | 'lg' | 'xl' | 'full'
  /** `inside` (default) keeps the title and actions visible while a long form scrolls within its card. */
  scroll?: 'page' | 'inside'
}) {
  const root = useRef<HTMLDivElement>(null)
  useDismiss(open, onClose, root)
  if (!open) return null

  const full = width === 'full'
  const contained = full || scroll === 'inside'
  const widths = { sm: 'max-w-md', md: 'max-w-xl', lg: 'max-w-3xl', xl: 'max-w-5xl', full: '' }[width]

  return createPortal(
    <div
      className={cn(
        'dialog-layer fixed inset-0 z-50 flex',
        // Phones (under 640px): every dialog is a bottom sheet that only scrolls its body. Wider windows keep the centred card.
        full ? '' : scroll === 'inside' ? 'items-end justify-center overflow-hidden p-0 sm:items-center sm:p-6' : 'items-end justify-center overflow-hidden p-0 sm:items-start sm:overflow-y-auto sm:p-8',
      )}
    >
      <div className={cn('ui-backdrop fixed inset-0 animate-fade-in', appearance === 'clean' && 'ui-backdrop-clean')} onClick={onClose} aria-hidden />
      <div
        ref={root}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className={cn(
          'workspace-dialog relative z-10 w-full min-w-0 overflow-hidden border border-rule bg-white shadow-overlay animate-sheet-in',
          full ? 'flex h-full flex-col' : scroll === 'inside' ? 'flex max-h-[calc(100dvh-0.75rem)] flex-col rounded-b-none rounded-t-[16px] sm:max-h-[calc(100dvh-3rem)] sm:rounded-[12px]' : 'flex max-h-[calc(100dvh-0.75rem)] flex-col rounded-b-none rounded-t-[16px] sm:block sm:max-h-none sm:rounded-[12px]',
          widths,
        )}
      >
        <header className="dialog-head flex shrink-0 items-start justify-between gap-4 border-b border-rule-soft px-6 py-5">
          <div className="min-w-0">
            <h2 className="text-[19px] font-semibold leading-snug tracking-[-0.02em] text-ink">{title}</h2>
            {description && <p className="mt-1 text-[14px] leading-6 text-ink-500">{description}</p>}
          </div>
          <IconButton label="Close" className="ui-dialog-close" onClick={onClose}>
            <X className="h-4 w-4" />
          </IconButton>
        </header>
        <div className={cn('dialog-body px-6 py-5', contained ? 'dialog-scroll-body scrollbar-thin min-h-0 flex-1 overflow-y-auto overscroll-contain' : 'min-h-0 flex-1 overflow-y-auto overscroll-contain sm:flex-none sm:overflow-visible')}>{children}</div>
        {footer && (
          <footer className="dialog-foot flex shrink-0 items-center justify-end gap-2 border-t border-rule-soft bg-white px-6 py-4">
            {footer}
          </footer>
        )}
      </div>
    </div>,
    document.body,
  )
}

/* ----------------------------------------------------------------- Sheet */

export function Sheet({
  open,
  onClose,
  title,
  description,
  children,
  footer,
  width = 'md',
}: {
  open: boolean
  onClose: () => void
  title: string
  description?: string
  children: ReactNode
  footer?: ReactNode
  width?: 'md' | 'lg' | 'xl'
}) {
  const root = useRef<HTMLDivElement>(null)
  useDismiss(open, onClose, root)
  if (!open) return null

  const widths = { md: 'max-w-xl', lg: 'max-w-3xl', xl: 'max-w-5xl' }[width]

  return createPortal(
    <div className="fixed inset-0 z-50 flex justify-end">
      <div className="ui-backdrop fixed inset-0 animate-fade-in" onClick={onClose} aria-hidden />
      <div
        ref={root}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className={cn('workspace-sheet relative z-10 flex h-[100dvh] w-full flex-col border-l border-rule bg-white shadow-overlay', widths)}
        style={{ animation: 'sheet-in 200ms cubic-bezier(.2,.8,.3,1)' }}
      >
        <header className="flex shrink-0 items-start justify-between gap-4 border-b border-rule-soft px-5 py-4">
          <div className="min-w-0">
            <h2 className="text-[18px] font-semibold leading-snug tracking-[-0.02em] text-ink">{title}</h2>
            {description && <p className="mt-1 text-[14px] leading-6 text-ink-500">{description}</p>}
          </div>
          <IconButton label="Close" className="ui-dialog-close" onClick={onClose}>
            <X className="h-4 w-4" />
          </IconButton>
        </header>
        <div className="scrollbar-thin flex-1 overflow-y-auto">{children}</div>
        {footer && (
          <footer className="dialog-foot flex shrink-0 items-center justify-end gap-2 border-t border-rule-soft bg-white px-5 py-3">
            {footer}
          </footer>
        )}
      </div>
    </div>,
    document.body,
  )
}

/* -------------------------------------------------------------- Dropdown */

export function Dropdown({
  trigger,
  children,
  align = 'right',
  width = 'w-64',
  label,
  side = 'bottom',
}: {
  trigger: (props: { open: boolean; toggle: () => void }) => ReactNode
  children: (close: () => void) => ReactNode
  align?: 'left' | 'right'
  width?: string
  /** Names the open panel for screen readers. */
  label?: string
  /** Opens below the trigger (default) or above it, for triggers at the bottom of the window. */
  side?: 'bottom' | 'top'
}) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false)
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  return (
    <div ref={ref} className="relative">
      {trigger({ open, toggle: () => setOpen((v) => !v) })}
      {open && (
        <div
          aria-label={label}
          className={cn(
            'ui-menu absolute z-40 max-h-[calc(100dvh-5rem)] overflow-y-auto rounded-[10px] border border-rule bg-white p-1 shadow-pop animate-sheet-in',
            side === 'top' ? 'bottom-[calc(100%+6px)]' : 'top-[calc(100%+6px)]',
            align === 'right' ? 'right-0' : 'left-0',
            width,
          )}
        >
          {children(() => setOpen(false))}
        </div>
      )}
    </div>
  )
}

export function MenuItem({
  children,
  onClick,
  tone = 'default',
  icon,
}: {
  children: ReactNode
  onClick?: () => void
  tone?: 'default' | 'danger'
  icon?: ReactNode
}) {
  return (
    <button
      onClick={onClick}
      className={cn(
        'ui-menu-item flex w-full items-center gap-2.5 rounded-[6px] px-3 py-2 text-left text-[14px] transition-colors',
        tone === 'danger' ? 'text-stamp-600 hover:bg-stamp-50' : 'text-ink-700 hover:bg-field',
      )}
    >
      {icon}
      {children}
    </button>
  )
}

/* ------------------------------------------------------------------ Tabs */

export function Tabs({
  tabs,
  active,
  onChange,
  className,
  variant = 'line',
}: {
  /**
   * `count` and `tone` are accepted so existing callers keep compiling, but a tab never shows a
   * number (product decision: counts belong on KPI cards, not on tabs and filter chips).
   */
  tabs: { key: string; label: string; count?: number; tone?: 'stamp' | 'amber' }[]
  active: string
  onChange: (key: string) => void
  className?: string
  /** `line` (default): a clean strip with a dark underline. `pill`: a bordered segmented control with a dark active segment. */
  variant?: 'line' | 'pill'
}) {
  const strip = useRef<HTMLDivElement>(null)

  // A strip wider than its space scrolls inside itself (never the page) and fades at the edge that has more tabs.
  useEffect(() => {
    const element = strip.current
    if (!element) return undefined
    const update = () => {
      const more = element.scrollWidth - element.clientWidth > 2
      element.dataset.fadeStart = more && element.scrollLeft > 2 ? '1' : '0'
      element.dataset.fadeEnd = more && element.scrollLeft + element.clientWidth < element.scrollWidth - 2 ? '1' : '0'
    }
    update()
    element.addEventListener('scroll', update, { passive: true })
    const observer = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(update) : null
    observer?.observe(element)
    return () => { element.removeEventListener('scroll', update); observer?.disconnect() }
  }, [tabs.length])

  // Keep the chosen tab in view without moving the page.
  useEffect(() => {
    const element = strip.current
    const chosen = element?.querySelector<HTMLElement>('[aria-selected="true"]')
    if (!element || !chosen) return
    const left = chosen.offsetLeft - element.offsetLeft
    if (left < element.scrollLeft + 24) element.scrollLeft = Math.max(0, left - 24)
    else if (left + chosen.offsetWidth > element.scrollLeft + element.clientWidth - 24) element.scrollLeft = left + chosen.offsetWidth - element.clientWidth + 24
  }, [active])

  return (
    <div
      ref={strip}
      className={cn('ui-tabs scrollbar-thin', variant === 'pill' ? 'ui-tabs-pill' : 'ui-tabs-line', className)}
      role="tablist"
    >
      {tabs.map((t) => {
        const on = t.key === active
        return (
          <button
            key={t.key}
            type="button"
            role="tab"
            aria-selected={on}
            onClick={() => onChange(t.key)}
            className="ui-tab"
          >
            {t.label}
          </button>
        )
      })}
    </div>
  )
}

/* ----------------------------------------------------------------- Toast */

export function Toast({
  message,
  tone,
  onDismiss,
}: {
  message: string
  tone: 'ok' | 'warn'
  onDismiss: () => void
}) {
  useEffect(() => {
    const t = setTimeout(onDismiss, 4200)
    return () => clearTimeout(t)
  }, [message, onDismiss])

  return createPortal(
    <div className="ui-toast-layer pointer-events-none fixed inset-x-0 bottom-5 z-[60] flex justify-center px-4">
      <div
        role="status"
        className={cn(
          'ui-toast pointer-events-auto flex max-w-full items-center gap-3 rounded-[10px] border px-4 py-2.5 text-[14px] shadow-overlay animate-sheet-in',
        )}
        data-tone={tone}
      >
        <span className="min-w-0 break-words">{message}</span>
        <button type="button" onClick={onDismiss} aria-label="Dismiss message" className="ui-toast-close -mr-1 grid shrink-0 place-items-center opacity-70 hover:opacity-100">
          <X className="h-3.5 w-3.5" />
        </button>
      </div>
    </div>,
    document.body,
  )
}
