import { Building2, Route } from 'lucide-react'
import { recruitmentRows, RECRUITMENT_EMPTY_TEXT } from '../paper-model'
import { STAGE_NAMES } from '../form-model'
import type { ApplicationFormData } from '../types'

/**
 * One line above the tabs that is always the same: the client, the job, the stage and the recruiter of what this form is about, so
 * nobody has to open Overview to remember it. "Change" opens Overview, where they are edited.
 */
export function ContextStrip({ form, onOpen }: { form: Pick<ApplicationFormData, 'assignment' | 'application' | 'tracking' | 'recruitment'>; onOpen: () => void }) {
  const rows = recruitmentRows(form.assignment, form.recruitment, form.application?.orderId ?? '').filter((row) => row.key === 'client' || row.key === 'job' || row.key === 'recruiter')
  const current = form.tracking.summary.currentStage || form.application?.currentStage || 0
  const stage = form.application ? STAGE_NAMES[current] || (current ? `Stage ${current}` : '') : ''
  return (
    <div className="af-context">
      <Building2 aria-hidden="true" />
      {rows.length === 0 ? <span className="af-context-empty">{RECRUITMENT_EMPTY_TEXT}</span> : (
        <dl>
          {rows.map((row) => <div key={row.key}><dt>{row.label}</dt><dd title={row.value}>{row.value}</dd></div>)}
          {stage && <div><dt><Route aria-hidden="true" />Stage</dt><dd>{stage}{current ? <small className="tnum"> {current}/11</small> : null}</dd></div>}
        </dl>
      )}
      <button type="button" onClick={onOpen}>{form.application ? 'Change' : 'Choose job'}</button>
    </div>
  )
}
