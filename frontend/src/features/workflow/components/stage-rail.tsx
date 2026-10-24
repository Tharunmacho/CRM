import { cn } from '@/shared/lib/cn'
import { slaState, SLA_HOURS } from '@/shared/lib/format'
import { STAGES, stageByNo } from '@/shared/workflow/stages'
import type { ApplicationStatus } from '@/shared/model/types'

type Segment = 'done' | 'now' | 'closed' | 'todo'

/**
 * The 11-stage tracker: one segment per stage. `numbered` draws the stage number inside each
 * segment (the stage modal); without it the bar is a slim strip for list rows.
 * `reached` lists stages with a recorded outcome; when omitted, every stage before the current
 * one counts as done.
 */
export function StageRail({
  status,
  currentStage,
  reached,
  numbered,
  className,
}: {
  status: ApplicationStatus
  currentStage: number
  reached?: number[]
  numbered?: boolean
  className?: string
}) {
  const stateOf = (no: number): Segment => {
    if (status === 'deployed') return 'done'
    if (status === 'closed') return no < currentStage ? 'done' : no === currentStage ? 'closed' : 'todo'
    if (no === currentStage) return 'now'
    return (reached ? reached.includes(no) : no < currentStage) ? 'done' : 'todo'
  }
  return (
    <ol className={cn('sr', numbered && 'is-numbered', className)} aria-label="The 11 stages">
      {STAGES.map((stage) => {
        const state = stateOf(stage.no)
        const label = `${stage.no}. ${stage.title}`
        return (
          <li
            key={stage.no}
            className={cn('sr-seg', `is-${state}`)}
            title={state === 'todo' ? `${label} (not started)` : state === 'now' ? `${label} (current)` : state === 'closed' ? `${label} (closed here)` : `${label} (done)`}
            aria-current={state === 'now' ? 'step' : undefined}
          >
            {numbered ? <span className="tnum">{stage.no}</span> : <span className="sr-sr">{label}</span>}
          </li>
        )
      })}
    </ol>
  )
}

/** The stage number and name beside a slim rail, e.g. "3 Interview scheduling". */
export function StageCaption({ no, status }: { no: number; status: ApplicationStatus }) {
  if (status === 'deployed') return <span className="sr-caption">Deployed · all 11 stages</span>
  const title = stageByNo(no).title
  return <span className="sr-caption"><b className="tnum">{no}</b>{title}{status === 'closed' ? ' · closed' : ''}</span>
}

/**
 * The 48-hour SLA as a ring: the arc is the share of the window still left, and its colour follows
 * urgency (primary, amber from 36 hours, red once overdue).
 */
export function SlaMeter({ startedAt, title, due }: { startedAt: string; title?: string; due?: string }) {
  const sla = slaState(startedAt)
  // Overdue draws the whole ring in red; otherwise the arc is the share of the window still left.
  const left = sla.overdue ? 1 : Math.max(0, 1 - sla.fill)
  const radius = 20
  const circumference = 2 * Math.PI * radius
  const short = sla.overdue ? `${Math.floor(sla.hoursOverdue)}h` : sla.hoursLeft < 1 ? `${Math.max(0, Math.round(sla.hoursLeft * 60))}m` : `${Math.floor(sla.hoursLeft)}h`
  return (
    <div className={cn('sla', `is-${sla.urgency}`)} role="img" aria-label={`${SLA_HOURS}-hour limit: ${sla.label}`}>
      <svg viewBox="0 0 48 48" width="52" height="52" aria-hidden="true">
        <circle cx="24" cy="24" r={radius} className="sla-track" />
        <circle cx="24" cy="24" r={radius} className="sla-arc" strokeDasharray={`${circumference * left} ${circumference}`} transform="rotate(-90 24 24)" />
        <text x="24" y="28" textAnchor="middle" className="sla-num">{short}</text>
      </svg>
      <div className="sla-copy">
        <b>{sla.label}</b>
        <span>{SLA_HOURS}-hour deadline{title ? ` · ${title}` : ''}</span>
        {due && <span>Due {due}</span>}
      </div>
    </div>
  )
}
