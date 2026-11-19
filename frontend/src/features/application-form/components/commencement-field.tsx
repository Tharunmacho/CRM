import { cn } from '@/shared/lib/cn'
import { DatePicker } from '@/shared/ui/date-picker'
import { COMMENCEMENT_PATH, formatFormDate } from '../form-model'
import type { FormContext } from './sections'

/**
 * The date of commencement (DOC) of the selected placement, in the Overview strip next to Job, Stage and Recruiter. It is typed into the
 * draft like every other field and goes out with "Save changes" (path `application.commencementDate`). Whoever can edit the form gets the
 * same date picker the passport dates use; everybody else sees the date as text. Without a placement there is nothing to date, so the
 * field gives way to a one-line hint. Optional: it is never in "Still missing".
 */
export function CommencementField({ ctx, canWork }: { ctx: FormContext; canWork: boolean }) {
  const value = ctx.draft.extras[COMMENCEMENT_PATH] ?? ''
  const label = 'Date of commencement (DOC)'
  if (!ctx.form.application) {
    return (
      <div className="af-assign-field af-doc-field" data-assign="doc">
        <div className="af-assign-label"><span>{label}</span></div>
        <p className="af-note">Choose a job first</p>
      </div>
    )
  }
  return (
    <div className={cn('af-assign-field af-doc-field', ctx.updated.includes(COMMENCEMENT_PATH) && 'is-updated')} data-assign="doc" data-field={COMMENCEMENT_PATH}>
      <div className="af-assign-label"><label htmlFor="af-assign-doc">{label}</label></div>
      {canWork
        ? <DatePicker id="af-assign-doc" value={value} aria-label={label} onChange={(next) => ctx.setDraft((current) => ({ ...current, extras: { ...current.extras, [COMMENCEMENT_PATH]: next } }))} />
        : <p className="af-assign-value" id="af-assign-doc">{value ? formatFormDate(value) : <span className="af-assign-empty">Not set</span>}</p>}
    </div>
  )
}
