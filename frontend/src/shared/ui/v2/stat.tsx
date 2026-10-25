import type { CSSProperties, ReactNode } from 'react'
import { ArrowDownRight, ArrowRight, ArrowUpRight } from 'lucide-react'
import { cn } from '@/shared/lib/cn'

/**
 * StatTile: label with a small notched node icon, a big tabular number, a route sub-line and an optional mini chart.
 *
 * Props
 *  - label   string
 *  - value   node           the number (tabular figures)
 *  - sub?    node           muted sub-line under the number
 *  - tone?   'primary' | 'success' | 'warning' | 'danger' | 'neutral'   colours the icon node (default 'primary')
 *  - icon?   node           a lucide icon (size 16), shown in the node at the left of the label
 *  - delta?  { text, trend?: 'up' | 'down' | 'flat', good?: boolean }   ONLY when real history exists
 *  - spark?  node           mini chart for the right half (hidden on phones)
 *  - onClick? / active?     turns the tile into a toggle button (filter), like SummaryCards
 */
export interface StatTileProps {
  label: string
  value: ReactNode
  sub?: ReactNode
  tone?: 'primary' | 'success' | 'warning' | 'danger' | 'neutral'
  icon?: ReactNode
  delta?: { text: ReactNode; trend?: 'up' | 'down' | 'flat'; good?: boolean }
  spark?: ReactNode
  onClick?: () => void
  active?: boolean
  className?: string
}

export function StatTile({ label, value, sub, tone = 'primary', icon, delta, spark, onClick, active, className }: StatTileProps) {
  const body = (
    <>
      <span className="v2-tile-main">
        <span className="v2-tile-label">
          <span className="v2-notch" data-tone={tone} aria-hidden="true">{icon}</span>
          <span className="v2-tile-label-text">{label}</span>
        </span>
        <strong className="v2-tile-value">{value}</strong>
        {(sub || delta) && (
          <span className="v2-tile-sub" data-tone={tone}>
            {delta && (
              <span className="v2-delta" data-trend={delta.trend ?? 'flat'} data-good={delta.good == null ? undefined : String(delta.good)}>
                {delta.trend === 'up' ? <ArrowUpRight size={13} aria-hidden="true" /> : delta.trend === 'down' ? <ArrowDownRight size={13} aria-hidden="true" /> : <ArrowRight size={13} aria-hidden="true" />}
                {delta.text}
              </span>
            )}
            {sub && <span className="v2-tile-sub-text">{sub}</span>}
          </span>
        )}
      </span>
      {spark && <span className="v2-tile-spark" aria-hidden="true">{spark}</span>}
    </>
  )
  const cls = cn('v2-tile', spark && 'has-spark', onClick && 'is-action', active && 'is-active', className)
  return onClick
    ? <button type="button" className={cls} onClick={onClick} aria-pressed={!!active}>{body}</button>
    : <div className={cls} role="group" aria-label={label}>{body}</div>
}

/** StatGrid: equal-height tiles on one aligned grid. `columns` = the most columns on wide screens (2-6, default 4). */
export function StatGrid({ children, columns = 4, className }: { children: ReactNode; columns?: 2 | 3 | 4 | 5 | 6; className?: string }) {
  return <div className={cn('v2-stat-grid', className)} style={{ '--v2-cols': columns } as CSSProperties}>{children}</div>
}
