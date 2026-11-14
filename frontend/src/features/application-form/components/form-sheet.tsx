import { missingBySection } from '../form-model'
import { receivedFromForm, recruitmentRows } from '../paper-model'
import type { ApplicationFormData, MissingItem, TrackingRow } from '../types'
import { Letterhead } from './letterhead'
import { EducationSection, ExperienceSection, PassportSection, PersonalSection, ReceivedDocumentsSection, RecruitmentSection, type FormContext } from './sections'
import { TrackingSection } from './tracking-section'

/**
 * The paper form itself: header, exactly the seven numbered sections of the official form (and nothing after them), signature line. It is the live preview and the
 * print: it only reads (the draft, the assignment, the documents), so it follows every keystroke and upload, and it is
 * never edited in place. The full-page view and the preview on the Add candidate page both render this, so they cannot
 * drift apart.
 */
export function FormSheet({
  ctx,
  missing,
  canOpenStage,
  onOpenStage,
}: {
  ctx: FormContext
  missing: MissingItem[]
  canOpenStage: boolean
  onOpenStage: (row: TrackingRow) => void
}) {
  const form: ApplicationFormData = ctx.form
  const bySection = missingBySection(missing)
  const hasOrder = Boolean(form.application)
  return (
    <div className="af-sheet">
      <Letterhead header={form.header} photo={form.documents.photo.current} candidate={form.candidate} assignment={form.assignment} />
      <PersonalSection ctx={ctx} missing={bySection.get(1) ?? []} />
      <PassportSection ctx={ctx} missing={bySection.get(2) ?? []} />
      <EducationSection ctx={ctx} missing={bySection.get(3) ?? []} />
      <ExperienceSection ctx={ctx} missing={bySection.get(4) ?? []} />
      <ReceivedDocumentsSection received={receivedFromForm(form)} missing={bySection.get(5) ?? []} />
      <RecruitmentSection rows={recruitmentRows(form.assignment, form.recruitment, form.application?.orderId)} />
      <TrackingSection rows={form.tracking.rows} closure={form.tracking.summary.closureLabel} hasOrder={hasOrder} canOpen={canOpenStage} onOpen={onOpenStage} />
      <footer className="af-sign">
        <div><span>Candidate signature</span></div>
        <div><span>Date</span></div>
        <div><span>Recruiter signature</span></div>
      </footer>
    </div>
  )
}
