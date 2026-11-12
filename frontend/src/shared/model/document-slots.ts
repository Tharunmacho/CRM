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
  /** Resume, passport and Aadhaar files can be read after staff start extraction. */
  readable: boolean
  /** More than one file may be kept (Passport, Aadhaar and Other documents). */
  multiple: boolean
  /** Always counted towards the form's completeness. */
  always: boolean
  /** Expected from this stage on (1 = Prescreening ... 11 = Air ticket); null when always expected or optional. */
  fromStage: number | null
  imageOnly?: boolean
}

export const DOCUMENT_SLOTS: DocumentSlot[] = [
  { slot: 'application_form', label: 'Application form', hint: 'Optional. A previously uploaded candidate application form.', readable: false, multiple: false, always: false, fromStage: null },
  { slot: 'resume', label: 'Resume', hint: 'Name, contact, education and experience are read from it.', readable: true, multiple: false, always: true, fromStage: null },
  { slot: 'passport', label: 'Passport', hint: 'Add the photo page, address page and other passport pages. Every file is kept.', readable: true, multiple: true, always: true, fromStage: null },
  { slot: 'aadhaar', label: 'Aadhaar copy', hint: 'Optional. Add the front and back pages, then extract the details.', readable: true, multiple: true, always: false, fromStage: null },
  { slot: 'medical', label: 'Medical', hint: 'The medical report.', readable: false, multiple: false, always: false, fromStage: 6 },
  { slot: 'signed_offer', label: 'Signed offer', hint: 'The offer letter signed by the candidate.', readable: false, multiple: false, always: false, fromStage: 5 },
  { slot: 'pcc', label: 'Police clearance certificate (PCC)', hint: 'Needed once the passport stage starts.', readable: false, multiple: false, always: false, fromStage: 7 },
  { slot: 'visa', label: 'Visa', hint: 'The visa received for the candidate.', readable: false, multiple: false, always: false, fromStage: 9 },
  { slot: 'air_ticket', label: 'Air ticket', hint: 'The confirmed ticket.', readable: false, multiple: false, always: false, fromStage: 11 },
  { slot: 'undertaking_letter', label: 'Undertaking letter', hint: 'The candidate’s signed undertaking, before deployment.', readable: false, multiple: false, always: false, fromStage: 11 },
  { slot: 'emigration', label: 'Emigration', hint: 'The emigration clearance, required after the visa stage.', readable: false, multiple: false, always: false, fromStage: 10 },
  { slot: 'insurance', label: 'Insurance', hint: 'The candidate’s insurance policy, before deployment.', readable: false, multiple: false, always: false, fromStage: 11 },
  { slot: 'invoice', label: 'Invoice', hint: 'The candidate’s invoice, before deployment.', readable: false, multiple: false, always: false, fromStage: 11 },
  { slot: 'photo', label: 'Photo', hint: 'Optional. Shown in the photo box at the top of the form.', readable: false, multiple: false, always: false, fromStage: null, imageOnly: true },
  { slot: 'education_certificate', label: 'Education certificate', hint: 'Optional. The highest qualification.', readable: false, multiple: false, always: false, fromStage: null },
  { slot: 'experience_certificate', label: 'Experience certificate', hint: 'Optional. From the latest employer.', readable: false, multiple: false, always: false, fromStage: null },
  { slot: 'google_review', label: 'Google review', hint: 'Optional. Proof of the candidate’s Google review.', readable: false, multiple: false, always: false, fromStage: null },
  { slot: 'visa_stamping', label: 'Visa stamping', hint: 'Optional. The stamped passport page.', readable: false, multiple: false, always: false, fromStage: null },
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
