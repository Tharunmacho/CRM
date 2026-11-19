import { CalendarClock, Route } from 'lucide-react'
import { recruitmentRows, RECRUITMENT_EMPTY_TEXT } from '../paper-model'
import { STAGE_NAMES, formatFormDate } from '../form-model'
import type { ApplicationFormData } from '../types'

/**
 * The strip above the tabs that is always the same: the client, the job, the recruiter, the stage and the date of commencement (DOC)
 * of what this form is about, so nobody has to open Overview to remember it. It is one row of equal-height "definition" cells (a small
 * muted label over a medium value) with a hairline between them; "Change" opens Overview, where they are edited (the DOC date picker is
 * there too, this strip only reads it).
 */
export function ContextStrip({ form, commencement, onOpen }: { form: Pick<ApplicationFormData, 'assignment' | 'application' | 'tracking' | 'recruitment'>; commencement?: string; onOpen: () => void }) {
  const rows = recruitmentRows(form.assignment, form.recruitment, form.application?.orderId ?? '').filter((row) => row.key === 'client' || row.key === 'job' || row.key === 'recruiter')
  const current = form.tracking.summary.currentStage || form.application?.currentStage || 0
  const stage = form.application ? STAGE_NAMES[current] || (current ? `Stage ${current}` : '') : ''
  const doc = form.application && commencement ? formatFormDate(commencement) : ''
  return (
    <div className="af-context" role="group" aria-label="Client, job, recruiter, stage and date of commencement">
      {rows.length === 0 ? <span className="af-context-empty">{RECRUITMENT_EMPTY_TEXT}</span> : (
        <dl className="af-context-cells">
          {rows.map((row) => <div className="af-ctx" data-ctx={row.key} key={row.key}><dt>{row.label}</dt><dd title={row.value}>{row.value}</dd></div>)}
          {stage && <div className="af-ctx" data-ctx="stage"><dt>Stage</dt><dd title={stage}><Route aria-hidden="true" />{stage}{current ? <small className="tnum">{current}/11</small> : null}</dd></div>}
          {form.application && <div className="af-ctx" data-ctx="doc"><dt>DOC</dt><dd title="Date of commencement">{doc ? <><CalendarClock aria-hidden="true" />{doc}</> : <span className="af-ctx-empty">Not set</span>}</dd></div>}
        </dl>
      )}
      <button type="button" className="af-context-change" onClick={onOpen}>{form.application ? 'Change' : 'Choose job'}</button>
    </div>
  )
}
