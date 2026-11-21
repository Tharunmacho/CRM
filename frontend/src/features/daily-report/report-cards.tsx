import { ArrowDown, ArrowRight, ArrowUp } from 'lucide-react'
import type { ReactNode } from 'react'
import { cn } from '@/shared/lib/cn'
import { trendSentence, type Trend } from './report-management'

/** The arrow and number of a summary card: "↑ +12 vs last week". Colour says whether the change is good (green), bad (red) or just a change (grey). */
export function TrendLine({ trend }: { trend: Trend | null }) {
  if (!trend) return null
  const Icon = trend.kind === 'up' ? ArrowUp : trend.kind === 'down' ? ArrowDown : ArrowRight
  return <span className={cn('rp-trend', `is-${trend.kind}`, `is-${trend.tone}`)} title={trendSentence(trend)}>
    <Icon size={13} strokeWidth={2.4} aria-hidden="true" />
    <b className="tnum">{trend.delta}</b>
    <span>vs {trend.against.replace(/^the /, '')}</span>
    <span className="rp-sr">{`, ${trendSentence(trend)}`}</span>
  </span>
}

/** The caption under a card's figure: the trend line first (when there is one), then one short plain sentence. */
export function CardNote({ trend, children }: { trend?: Trend | null; children?: ReactNode }) {
  return <span className="rp-note">
    {trend ? <TrendLine trend={trend} /> : null}
    {children ? <span className="rp-note-text">{children}</span> : null}
  </span>
}
