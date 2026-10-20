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
      className={cn(
        'ui-panel rounded-panel border border-rule-soft bg-white shadow-soft',
        !flush && 'p-5',
        className,
      )}
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
    <header className={cn('ui-panel-header flex items-center justify-between gap-3 border-b border-rule-soft px-5 py-4', className)}>
      <div className="min-w-0">
        <h2 className="truncate text-h3 font-semibold text-ink">{title}</h2>
        {meta && <p className="mt-0.5 truncate text-xs text-ink-400">{meta}</p>}
      </div>
      {action && <div className="flex shrink-0 items-center gap-2">{action}</div>}
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

export type Tone = 'neutral' | 'navy' | 'stamp' | 'amber' | 'ink'

const TONES: Record<Tone, string> = {
  neutral: 'bg-field text-ink-600 border-rule',
  navy: 'bg-navy-50 text-navy-700 border-navy-100',
  stamp: 'bg-stamp-50 text-stamp-700 border-stamp-100',
  amber: 'bg-amber-50 text-amber-700 border-amber-100',
  ink: 'bg-ink-800 text-white border-ink-800',
}

export function Badge({
  children,
  tone = 'neutral',
  className,
  dot,
}: {
  children: ReactNode
  tone?: Tone
  className?: string
  dot?: boolean
}) {
  return (
    <span
      className={cn(
        'ui-badge inline-flex items-center gap-1.5 whitespace-nowrap rounded-[3px] border px-1.5 py-0.5 text-micro font-medium',
        TONES[tone],
        className,
      )}
    >
      {dot && <span className={cn('h-1.5 w-1.5 rounded-full', tone === 'stamp' ? 'bg-stamp-600' : tone === 'amber' ? 'bg-amber-600' : tone === 'navy' ? 'bg-navy-600' : 'bg-ink-400')} />}
      {children}
    </span>
  )
}

const STATUS_TONE: Record<string, Tone> = {
  new: 'navy',
  in_process: 'neutral',
  not_interested: 'amber',
  deployed: 'ink',
  needs_review: 'amber',
  active: 'navy',
  closed: 'stamp',
  open: 'navy',
  on_hold: 'amber',
  filled: 'ink',
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

export function StatusPill({ status, className }: { status: string; className?: string }) {
  return (
    <Badge tone={STATUS_TONE[status] ?? 'neutral'} className={className}>
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
    navy: 'text-navy-700',
  }[tone]

  const Tag = onClick ? 'button' : 'div'
  return (
    <Tag
      onClick={onClick}
      className={cn(
        'flex min-h-[9.5rem] flex-col justify-between rounded-panel border border-rule-soft bg-white p-6 text-left shadow-soft',
        onClick && 'transition-shadow hover:shadow-pop',
      )}
    >
      <span className="text-sm font-medium text-ink-500">{label}</span>
      <span className={cn('mt-3 block text-stat font-semibold tnum', accent)}>{value}</span>
      {sub && <span className="mt-2 text-xs text-ink-400">{sub}</span>}
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
        className="h-1 w-full min-w-[40px] overflow-hidden rounded-full bg-rule-soft"
        role="img"
        aria-label={`48-hour limit: ${sla.label}`}
      >
        <div
          className={cn('h-full rounded-full transition-all', urgencyBar[sla.urgency])}
          style={{ width: `${Math.max(3, sla.fill * 100)}%` }}
        />
      </div>
      {showLabel && (
        <span className={cn('hidden shrink-0 whitespace-nowrap text-micro tnum font-medium sm:inline', urgencyText[sla.urgency])}>
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
      <div className="empty-state-icon mb-3 text-ink-300">{icon ?? <FolderOpen size={26} strokeWidth={1.4} />}</div>
      <p className="text-sm font-medium text-ink">{title}</p>
      {description && <p className="mt-1 max-w-[46ch] text-sm text-ink-400">{description}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  )
}

/* ------------------------------------------------------- Definition lists */

export function DefList({ children, cols = 2 }: { children: ReactNode; cols?: 1 | 2 | 3 }) {
  return (
    <dl
      className={cn(
        'grid gap-x-6 gap-y-3.5',
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
      <dt className="text-micro text-ink-400">{label}</dt>
      <dd className={cn('mt-0.5 truncate text-sm text-ink', ident && 'ident')}>{children}</dd>
    </div>
  )
}

/** The stage number, identical wherever a stage is named in a table or list. */
export function StageDot({ no }: { no: number }) {
  return (
    <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-navy-100 text-micro font-semibold text-navy-700 tnum">
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
  // Plain number; colour alone carries the state. The fixed width keeps the
  // numbers in a tidy column wherever several rows stack.
  const styles = {
    done: 'text-navy-700',
    current: 'text-ink font-semibold',
    closed: 'text-stamp-700',
    future: 'text-ink-300',
  }[state]
  return (
    <span
      className={cn(
        'inline-flex w-6 shrink-0 justify-center text-sm font-medium tnum',
        styles,
        className,
      )}
    >
      {no}
    </span>
  )
}
