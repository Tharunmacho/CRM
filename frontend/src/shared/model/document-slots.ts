/**
 * The candidate's document slots, in the order they appear everywhere (application form, candidate profile, add candidate).
 * One list, so a label or a rule is changed in one place. `fromStage` is the pipeline stage from which a document is expected
 * (null: always expected, or optional). Mirrors docs/workflows/application-form-v2-contract.md; the server decides what is
 * `required` for a given application, this list is only for labels, order and wording.
 */
export interface DocumentSlot {
  slot: string
  label: string
  hint: string
  /** Only the resume and the passport are read by the document reader. */
  readable: boolean
  /** More than one file may be kept (Other documents). */
  multiple: boolean
  /** Always counted towards the form's completeness. */
  always: boolean
  /** Expected from this stage on (1 = Prescreening ... 11 = Air ticket); null when always expected or optional. */
  fromStage: number | null
  imageOnly?: boolean
}

export const DOCUMENT_SLOTS: DocumentSlot[] = [
  { slot: 'resume', label: 'Resume', hint: 'Name, contact, education and experience are read from it.', readable: true, multiple: false, always: true, fromStage: null },
  { slot: 'passport', label: 'Passport', hint: 'Number, dates, place of issue and father\'s name are read from it.', readable: true, multiple: false, always: true, fromStage: null },
  { slot: 'photo', label: 'Photo', hint: 'Shown in the photo box at the top of the form.', readable: false, multiple: false, always: true, fromStage: null, imageOnly: true },
  { slot: 'education_certificate', label: 'Education certificate', hint: 'The highest qualification.', readable: false, multiple: false, always: true, fromStage: null },
  { slot: 'experience_certificate', label: 'Experience certificate', hint: 'Optional. From the latest employer.', readable: false, multiple: false, always: false, fromStage: null },
  { slot: 'signed_offer', label: 'Signed offer', hint: 'The offer letter signed by the candidate.', readable: false, multiple: false, always: false, fromStage: 5 },
  { slot: 'medical', label: 'Medical', hint: 'The medical report.', readable: false, multiple: false, always: false, fromStage: 6 },
  { slot: 'pcc', label: 'Police clearance certificate (PCC)', hint: 'Needed once the passport stage starts.', readable: false, multiple: false, always: false, fromStage: 7 },
  { slot: 'visa', label: 'Visa', hint: 'The visa received for the candidate.', readable: false, multiple: false, always: false, fromStage: 9 },
  { slot: 'google_review', label: 'Google review', hint: 'Proof of the review, before the visa stamping.', readable: false, multiple: false, always: false, fromStage: 9 },
  { slot: 'visa_stamping', label: 'Visa stamping', hint: 'The stamped passport page.', readable: false, multiple: false, always: false, fromStage: 10 },
  { slot: 'emigration', label: 'Emigration', hint: 'The emigration clearance.', readable: false, multiple: false, always: false, fromStage: 10 },
  { slot: 'air_ticket', label: 'Air ticket', hint: 'The confirmed ticket.', readable: false, multiple: false, always: false, fromStage: 11 },
  { slot: 'other', label: 'Other documents', hint: 'Anything else, several files allowed.', readable: false, multiple: true, always: false, fromStage: null },
]

export const DOCUMENT_SLOT_KEYS = DOCUMENT_SLOTS.map((entry) => entry.slot)
export const documentSlot = (slot: string): DocumentSlot | undefined => DOCUMENT_SLOTS.find((entry) => entry.slot === slot)
export const documentLabel = (slot: string): string => documentSlot(slot)?.label ?? slot.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase())

/** Whether a document is expected for an application at `stage` (no application: only the always-expected ones). */
export function documentExpected(entry: DocumentSlot, stage: number | null | undefined): boolean {
  if (entry.always) return true
  if (entry.fromStage === null) return false
  return typeof stage === 'number' && stage >= entry.fromStage
}
