import type { CSSProperties, ReactNode } from 'react'
import { cn } from '@/shared/lib/cn'

/**
 * Meter: a progress or time-left bar. Honest values only (pass real numbers).
 *
 * Props
 *  - value     number           current amount (clamped between 0 and max)
 *  - max?      number           default 100
 *  - tone?     'primary' | 'success' | 'warning' | 'danger' | 'neutral'   default 'primary' (use warning/danger for little time left)
 *  - label     string           accessible name (e.g. "Time left on this task")
 *  - caption?  node             text above/right of the bar, e.g. "6 of 10 deployed" (visible)
 *  - size?     'sm' (4px) | 'md' (8px, default)
 *  - valueText? string          spoken value, e.g. "6 hours left"; default "value of max"
 */
export function Meter({ value, max = 100, tone = 'primary', label, caption, size = 'md', valueText, className }: { value: number; max?: number; tone?: 'primary' | 'success' | 'warning' | 'danger' | 'neutral'; label: string; caption?: ReactNode; size?: 'sm' | 'md'; valueText?: string; className?: string }) {
  const safeMax = max > 0 ? max : 1
  const clamped = Math.min(Math.max(value, 0), safeMax)
  const pct = (clamped / safeMax) * 100
  return (
    <div className={cn('v2-meter', `is-${size}`, className)} data-tone={tone}>
      {caption != null && <div className="v2-meter-caption">{caption}</div>}
      <div className="v2-meter-track" role="progressbar" aria-label={label} aria-valuemin={0} aria-valuemax={safeMax} aria-valuenow={Math.round(clamped * 100) / 100} aria-valuetext={valueText ?? `${Math.round(clamped * 100) / 100} of ${safeMax}`}>
        <span className="v2-meter-fill" style={{ '--v2-pct': `${pct}%` } as CSSProperties} />
      </div>
    </div>
  )
}
