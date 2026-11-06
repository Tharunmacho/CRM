import { missingBySection } from '../form-model'
import type { ApplicationFormData, DocumentKind, MissingItem, TrackingRow } from '../types'
import { AssessmentSection, ExtraCell, FollowUpSection } from './extras'
import { DocumentsSection } from './documents-section'
import { Letterhead } from './letterhead'
import { EducationSection, ExperienceSection, PassportSection, PersonalSection, RecruitmentSection, type FormContext } from './sections'
import { TrackingSection } from './tracking-section'

/**
 * The paper form itself: header, the seven sections in the docx order, signature line. The full-page view and the
 * preview on the Add candidate page both render this, so they cannot drift apart.
 */
export function FormSheet({
  ctx,
  missing,
  canUpload,
  upload,
  busyKinds,
  canOpenStage,
  onOpenStage,
}: {
  ctx: FormContext
  missing: MissingItem[]
  canUpload: boolean
  upload: (kind: DocumentKind, file: File) => Promise<void>
  busyKinds: DocumentKind[]
  canOpenStage: boolean
  onOpenStage: (row: TrackingRow) => void
}) {
  const form: ApplicationFormData = ctx.form
  const bySection = missingBySection(missing)
  const hasOrder = Boolean(form.application)
  return (
    <div className="af-sheet">
      <Letterhead header={form.header} photo={form.documents.photo.current} canUpload={canUpload} onPhoto={(file) => upload('photo', file)} reference={form.candidate.ref} />
      <PersonalSection ctx={ctx} missing={bySection.get(1) ?? []} renderExtra={(path) => <ExtraCell key={path} ctx={ctx} path={path} />} />
      <PassportSection ctx={ctx} missing={bySection.get(2) ?? []} />
      <EducationSection ctx={ctx} missing={bySection.get(3) ?? []} />
      <ExperienceSection ctx={ctx} missing={bySection.get(4) ?? []} />
      <DocumentsSection form={form} canUpload={canUpload} onUpload={upload} busyKinds={busyKinds} />
      <RecruitmentSection form={form} hasOrder={hasOrder} />
      <AssessmentSection ctx={ctx} />
      <FollowUpSection ctx={ctx} />
      <TrackingSection rows={form.tracking.rows} closure={form.tracking.summary.closureLabel} hasOrder={hasOrder} canOpen={canOpenStage} onOpen={onOpenStage} />
      <footer className="af-sign">
        <div><span>Candidate signature</span></div>
        <div><span>Date</span></div>
        <div><span>Recruiter signature</span></div>
      </footer>
    </div>
  )
}
