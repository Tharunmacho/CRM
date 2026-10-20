import { useEffect, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { X } from 'lucide-react'
import { cn } from '@/shared/lib/cn'
import { IconButton } from './primitives'

/* ----------------------------------------------------------- Escape hook */

function useDismiss(open: boolean, onClose: () => void) {
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKey)
    const prev = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.removeEventListener('keydown', onKey)
      document.body.style.overflow = prev
    }
  }, [open, onClose])
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
}: {
  open: boolean
  onClose: () => void
  title: string
  description?: string
  children: ReactNode
  footer?: ReactNode
  /** `full` takes the whole window: the header stays put and the body scrolls under it. */
  width?: 'sm' | 'md' | 'lg' | 'xl' | 'full'
}) {
  useDismiss(open, onClose)
  if (!open) return null

  const full = width === 'full'
  const widths = { sm: 'max-w-md', md: 'max-w-xl', lg: 'max-w-3xl', xl: 'max-w-5xl', full: '' }[width]

  return createPortal(
    <div
      className={cn(
        'fixed inset-0 z-50 flex',
        full ? '' : 'items-start justify-center overflow-y-auto p-4 sm:p-8',
      )}
    >
      <div className="fixed inset-0 bg-ink-900/40 animate-fade-in" onClick={onClose} aria-hidden />
      <div
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className={cn(
          'workspace-dialog relative z-10 w-full overflow-hidden bg-white shadow-overlay animate-sheet-in',
          full ? 'flex h-full flex-col' : 'rounded-panel',
          widths,
        )}
      >
        <header className="flex shrink-0 items-start justify-between gap-4 border-b border-rule-soft px-6 py-5">
          <div className="min-w-0">
            <h2 className="text-h3 font-semibold text-ink">{title}</h2>
            {description && <p className="mt-0.5 text-sm text-ink-500">{description}</p>}
          </div>
          <IconButton label="Close" onClick={onClose}>
            <X className="h-4 w-4" />
          </IconButton>
        </header>
        <div className={cn('px-6 py-5', full && 'scrollbar-thin min-h-0 flex-1 overflow-y-auto')}>{children}</div>
        {footer && (
          <footer className="flex shrink-0 items-center justify-end gap-2 border-t border-rule-soft bg-field/40 px-6 py-4">
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
  useDismiss(open, onClose)
  if (!open) return null

  const widths = { md: 'max-w-xl', lg: 'max-w-3xl', xl: 'max-w-5xl' }[width]

  return createPortal(
    <div className="fixed inset-0 z-50 flex justify-end">
      <div className="fixed inset-0 bg-ink-900/40 animate-fade-in" onClick={onClose} aria-hidden />
      <div
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className={cn('relative z-10 flex h-full w-full flex-col bg-white shadow-overlay', widths)}
        style={{ animation: 'sheet-in 200ms cubic-bezier(.2,.8,.3,1)' }}
      >
        <header className="flex shrink-0 items-start justify-between gap-4 border-b border-rule px-5 py-4">
          <div className="min-w-0">
            <h2 className="text-h3 font-semibold text-ink">{title}</h2>
            {description && <p className="mt-0.5 text-sm text-ink-500">{description}</p>}
          </div>
          <IconButton label="Close" onClick={onClose}>
            <X className="h-4 w-4" />
          </IconButton>
        </header>
        <div className="scrollbar-thin flex-1 overflow-y-auto">{children}</div>
        {footer && (
          <footer className="flex shrink-0 items-center justify-end gap-2 border-t border-rule bg-field/50 px-5 py-3">
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
}: {
  trigger: (props: { open: boolean; toggle: () => void }) => ReactNode
  children: (close: () => void) => ReactNode
  align?: 'left' | 'right'
  width?: string
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
          className={cn(
            'absolute top-[calc(100%+6px)] z-40 rounded-panel border border-rule bg-white py-1 shadow-pop animate-sheet-in',
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
        'flex w-full items-center gap-2.5 px-3 py-2 text-left text-sm transition-colors',
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
}: {
  tabs: { key: string; label: string; count?: number; tone?: 'stamp' | 'amber' }[]
  active: string
  onChange: (key: string) => void
  className?: string
}) {
  return (
    <div
      className={cn(
        'ui-tabs scrollbar-thin flex items-center gap-0.5 overflow-x-auto overflow-y-hidden border-b border-rule',
        className,
      )}
      role="tablist"
    >
      {tabs.map((t) => {
        const on = t.key === active
        return (
          <button
            key={t.key}
            role="tab"
            aria-selected={on}
            onClick={() => onChange(t.key)}
            className={cn(
              'relative shrink-0 px-3 py-2.5 text-sm font-medium transition-colors',
              on ? 'text-ink' : 'text-ink-400 hover:text-ink-600',
            )}
          >
            <span className="flex items-center gap-1.5">
              {t.label}
              {t.count != null && (
                <span
                  className={cn(
                    'rounded-[3px] px-1 py-px text-micro tnum',
                    on ? 'bg-ink-800 text-white' : 'bg-field text-ink-500',
                    t.tone === 'stamp' && t.count > 0 && 'bg-stamp-100 text-stamp-700',
                    t.tone === 'amber' && t.count > 0 && 'bg-amber-100 text-amber-700',
                  )}
                >
                  {t.count}
                </span>
              )}
            </span>
            {on && <span className="absolute inset-x-1.5 bottom-0 h-0.5 rounded-full bg-navy-600" />}
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
    <div className="pointer-events-none fixed bottom-5 left-1/2 z-[60] -translate-x-1/2 px-4">
      <div
        role="status"
        className={cn(
          'pointer-events-auto flex items-center gap-3 rounded-panel px-4 py-2.5 text-sm text-white shadow-overlay animate-sheet-in',
          tone === 'warn' ? 'bg-stamp-700' : 'bg-ink-800',
        )}
      >
        <span>{message}</span>
        <button onClick={onDismiss} aria-label="Dismiss" className="-mr-1 opacity-60 hover:opacity-100">
          <X className="h-3.5 w-3.5" />
        </button>
      </div>
    </div>,
    document.body,
  )
}
