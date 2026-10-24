import type { ReactNode } from 'react'
import { FolderOpen } from 'lucide-react'
import { cn } from '@/shared/lib/cn'
import { slaState, urgencyBar, urgencyText } from '@/shared/lib/format'

/* ------------------------------------------------------------------ Panel */

export function Panel({
  children,
  className,
  flush,
  id,
}: {
  children: ReactNode
  className?: string
  flush?: boolean
  id?: string
}) {
  return (
    <section
      id={id}
      className={cn('ui-panel rounded-[12px] border border-rule bg-white', !flush && 'p-5', className)}
    >
      {children}
    </section>
  )
}

export function PanelHeader({
  title,
  meta,
  action,
  className,
}: {
  title: ReactNode
  meta?: ReactNode
  action?: ReactNode
  className?: string
}) {
  return (
    <header
      className={cn(
        'ui-panel-header flex flex-wrap items-center justify-between gap-3 border-b border-rule-soft px-5 py-4',
        className,
      )}
    >
      <div className="min-w-0">
        <h2 className="truncate text-[17px] font-semibold tracking-[-0.02em] text-ink">{title}</h2>
        {meta && <p className="mt-0.5 truncate text-[13px] text-ink-500">{meta}</p>}
      </div>
      {action && <div className="flex shrink-0 flex-wrap items-center gap-2">{action}</div>}
    </header>
  )
}

export function PageHeader({
  title,
  description,
  actions,
  children,
  eyebrow,
}: {
  title: string
  description?: string
  actions?: ReactNode
  children?: ReactNode
  eyebrow?: string
}) {
  return (
    <header className="workspace-page-header mb-5">
      <div className="workspace-page-header-row flex flex-wrap items-end justify-between gap-3">
        <div className="workspace-heading-copy min-w-0">
          {eyebrow && <p className="workspace-eyebrow">{eyebrow}</p>}
          <h1 className="text-h1 font-semibold text-ink">{title}</h1>
          {description && <p className="mt-1 max-w-[62ch] text-sm text-ink-500">{description}</p>}
        </div>
        {actions && <div className="workspace-header-actions flex shrink-0 flex-wrap items-center gap-2">{actions}</div>}
      </div>
      {children}
    </header>
  )
}

/* ------------------------------------------------------------------ Badge */

export type Tone = 'neutral' | 'navy' | 'stamp' | 'amber' | 'ink' | 'green'

/**
 * Two looks, both token based (styles/depth.css):
 * - `dot` (default for every coloured tone, and for StatusPill): a small coloured dot and plain
 *   text, the reference product's status style.
 * - `chip` (default for the neutral tone): a quiet bordered label for tags, skills and categories.
 */
export type BadgeVariant = 'dot' | 'chip'

export function Badge({
  children,
  tone = 'neutral',
  className,
  dot,
  variant,
}: {
  children: ReactNode
  tone?: Tone
  className?: string
  /** Show the coloured dot (on a neutral chip it adds a dot to the chip). */
  dot?: boolean
  variant?: BadgeVariant
}) {
  const look: BadgeVariant = variant ?? (tone === 'neutral' && !dot ? 'chip' : 'dot')
  return (
    <span className={cn('ui-badge', `ui-badge-${look}`, className)} data-tone={tone}>
      {(look === 'dot' || dot) && <i className="ui-badge-mark" aria-hidden="true" />}
      {children}
    </span>
  )
}

const STATUS_TONE: Record<string, Tone> = {
  new: 'navy',
  in_process: 'neutral',
  not_interested: 'amber',
  deployed: 'green',
  needs_review: 'amber',
  active: 'green',
  closed: 'stamp',
  open: 'green',
  on_hold: 'amber',
  filled: 'green',
  inactive: 'neutral',
}

const STATUS_LABEL: Record<string, string> = {
  new: 'New',
  in_process: 'In process',
  not_interested: 'Not interested',
  deployed: 'Deployed',
  needs_review: 'Needs review',
  active: 'Active',
  closed: 'Closed',
  open: 'Open',
  on_hold: 'On hold',
  filled: 'Filled',
  inactive: 'Inactive',
}

export function StatusPill({ status, className }: { status: string; className?: string; dot?: boolean }) {
  return (
    <Badge tone={STATUS_TONE[status] ?? 'neutral'} variant="dot" className={className}>
      {STATUS_LABEL[status] ?? status}
    </Badge>
  )
}

/* -------------------------------------------------------------- Stat tile */

export function StatTile({
  label,
  value,
  sub,
  tone = 'neutral',
  onClick,
}: {
  label: string
  value: ReactNode
  sub?: ReactNode
  tone?: 'neutral' | 'amber' | 'stamp' | 'navy'
  onClick?: () => void
}) {
  const accent = {
    neutral: 'text-ink',
    amber: 'text-amber-700',
    stamp: 'text-stamp-700',
    navy: 'text-ink',
  }[tone]

  const Tag = onClick ? 'button' : 'div'
  return (
    <Tag
      onClick={onClick}
      className={cn(
        'ui-stat-tile flex min-h-[8.5rem] flex-col justify-between rounded-[12px] border border-rule bg-white p-5 text-left',
        onClick && 'transition-colors hover:border-[color:var(--border-hover)]',
      )}
    >
      <span className="text-[14px] text-ink-500">{label}</span>
      <span className={cn('mt-3 block text-[36px] font-semibold leading-none tracking-[-0.04em] tnum', accent)}>{value}</span>
      {sub && <span className="mt-2 text-[13px] text-ink-500">{sub}</span>}
    </Tag>
  )
}

/* --------------------------------------------------------------- Time bar */

/**
 * The 48-hour clock drawn as rope left, not a badge. A badge tells you a task is
 * late; the bar tells you how much time is actually remaining.
 */
export function TimeBar({
  createdAt,
  showLabel = true,
  className,
}: {
  createdAt: string
  showLabel?: boolean
  className?: string
}) {
  const sla = slaState(createdAt)
  return (
    <div className={cn('flex items-center gap-2', className)}>
      <div
        className="h-1 w-full min-w-[40px] overflow-hidden rounded-full bg-field"
        role="img"
        aria-label={`48-hour limit: ${sla.label}`}
      >
        <div
          className={cn('h-full rounded-full transition-all', urgencyBar[sla.urgency])}
          style={{ width: `${Math.max(3, sla.fill * 100)}%` }}
        />
      </div>
      {showLabel && (
        <span className={cn('hidden shrink-0 whitespace-nowrap text-[12.5px] tnum font-medium sm:inline', urgencyText[sla.urgency])}>
          {sla.label}
        </span>
      )}
    </div>
  )
}

/* ------------------------------------------------------------ Empty state */

export function EmptyState({
  title,
  description,
  action,
  icon,
}: {
  title: string
  description?: string
  action?: ReactNode
  icon?: ReactNode
}) {
  return (
    <div className="ui-empty-state flex flex-col items-center justify-center px-6 py-14 text-center">
      <div className="empty-state-icon mb-3 flex h-11 w-11 items-center justify-center rounded-[10px] border border-rule bg-white text-ink-500">
        {icon ?? <FolderOpen size={20} strokeWidth={1.6} />}
      </div>
      <p className="text-[16px] font-semibold tracking-[-0.01em] text-ink">{title}</p>
      {description && <p className="mt-1 max-w-[46ch] text-[14px] leading-6 text-ink-500">{description}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  )
}

/* ------------------------------------------------------- Definition lists */

export function DefList({ children, cols = 2 }: { children: ReactNode; cols?: 1 | 2 | 3 }) {
  return (
    <dl
      className={cn(
        'ui-def-list grid gap-x-6 gap-y-4',
        cols === 1 ? 'grid-cols-1' : cols === 2 ? 'sm:grid-cols-2' : 'sm:grid-cols-2 lg:grid-cols-3',
      )}
    >
      {children}
    </dl>
  )
}

export function Def({
  label,
  children,
  ident,
}: {
  label: string
  children: ReactNode
  ident?: boolean
}) {
  return (
    <div className="min-w-0">
      <dt className="text-[13px] text-ink-500">{label}</dt>
      <dd className={cn('mt-0.5 truncate text-[14px] text-ink', ident && 'ident')}>{children}</dd>
    </div>
  )
}

/** The stage number, identical wherever a stage is named in a table or list. */
export function StageDot({ no }: { no: number }) {
  return (
    <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full border border-rule bg-field text-[12.5px] font-semibold text-ink tnum">
      {no}
    </span>
  )
}

/* ----------------------------------------------------------- Stage number */

export function StageChip({
  no,
  state,
  className,
}: {
  no: number
  state: 'done' | 'current' | 'closed' | 'future'
  className?: string
}) {
  // A small bordered tile; fill and ink carry the state. The fixed width keeps the numbers in
  // a tidy column wherever several rows stack.
  const styles = {
    done: 'border-rule bg-field text-ink',
    current: 'border-navy-600 bg-navy-600 text-[color:var(--on-primary)]',
    closed: 'border-stamp-100 bg-stamp-50 text-stamp-700',
    future: 'border-rule bg-white text-ink-400',
  }[state]
  return (
    <span
      data-state={state}
      className={cn(
        'inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-[6px] border text-[12.5px] font-semibold tnum',
        styles,
        className,
      )}
    >
      {no}
    </span>
  )
}
