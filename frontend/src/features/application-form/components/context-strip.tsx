import { CalendarClock, Route, TimerReset } from 'lucide-react'
import { cn } from '@/shared/lib/cn'
import { TOTAL_STAGES } from '@/shared/workflow/stages'
import { recruitmentRows, RECRUITMENT_EMPTY_TEXT } from '../paper-model'
import { STAGE_NAMES, formatFormDate, stageClockText, stageClockTone } from '../form-model'
import type { ApplicationFormData, StageClock } from '../types'

/**
 * The strip above the tabs that is always the same: the client, the job, the recruiter, the stage (with the time it has taken and its
 * limit) and the date of commencement (DOC) of what this form is about, so nobody has to open Overview to remember it. It is one
 * descriptions grid: a small muted label over a medium value, a hairline between the items, and every value wraps to a second line
 * instead of being cut. "Change" is a text button at the end; it opens Overview, where these are edited (the DOC date picker is there too,
 * this strip only reads it).
 */
export function ContextStrip({ form, clock, commencement, onOpen }: { form: Pick<ApplicationFormData, 'assignment' | 'application' | 'tracking' | 'recruitment'>; clock?: StageClock | null; commencement?: string; onOpen: () => void }) {
  const rows = recruitmentRows(form.assignment, form.recruitment, form.application?.orderId ?? '').filter((row) => row.key === 'client' || row.key === 'job' || row.key === 'recruiter')
  const current = form.tracking.summary.currentStage || form.application?.currentStage || 0
  const stage = form.application ? STAGE_NAMES[current] || (current ? `Stage ${current}` : '') : ''
  const doc = form.application && commencement ? formatFormDate(commencement) : ''
  const tone = clock ? stageClockTone(clock) : null
  const timing = clock ? stageClockText({ stageLabel: '', hours: clock.hours, limitHours: clock.limitHours }) : ''
  return (
    <section className="af-context" aria-label="Client, job, recruiter, stage and date of commencement">
      {rows.length === 0 ? <span className="af-context-empty">{RECRUITMENT_EMPTY_TEXT}</span> : (
        <dl className="af-context-cells">
          {rows.map((row) => <div className="af-ctx" data-ctx={row.key} key={row.key}><dt>{row.label}</dt><dd>{row.value}</dd></div>)}
          {stage && (
            <div className="af-ctx" data-ctx="stage">
              <dt>Stage</dt>
              <dd><Route aria-hidden="true" /><span>{stage}</span>{current ? <small className="tnum">{current}/{TOTAL_STAGES}</small> : null}</dd>
              {timing && <dd className={cn('af-ctx-clock', tone && `is-${tone}`)} title={tone === 'over' ? 'This stage has taken longer than the limit' : 'Time spent in the current stage, and its limit'}><TimerReset aria-hidden="true" />{timing}</dd>}
            </div>
          )}
          {form.application && <div className="af-ctx" data-ctx="doc"><dt>DOC</dt><dd title="Date of commencement">{doc ? <><CalendarClock aria-hidden="true" /><span>{doc}</span></> : <span className="af-ctx-empty">Not set</span>}</dd></div>}
        </dl>
      )}
      <button type="button" className="af-context-change" onClick={onOpen}>{form.application ? 'Change' : 'Choose job'}</button>
    </section>
  )
}
