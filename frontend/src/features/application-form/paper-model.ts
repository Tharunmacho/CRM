/**
 * Plain helpers for the paper form (the A4 sheet that is previewed and printed) and the screen around it. No React and no
 * React, so they are tested without a browser. The rule they share: print only what is known. An empty entry is
 * left out; it is never replaced with a dash or with "Not set".
 */
import { ALL_EDITABLE_FIELDS, SECTION_TITLES, cleanAgencyName } from './form-model'
import type { ApplicationFormData, ApplicationRef, AssignmentData, DocumentKind, DocumentSlot, MissingItem, RecruitmentDetails } from './types'

/** The paper form has exactly these seven numbered sections and nothing after them (the official form, plus Documents received). */
export const PAPER_SECTION_NUMBERS = [1, 2, 3, 4, 5, 6, 7] as const
export const paperSections = (): { no: number; title: string }[] => PAPER_SECTION_NUMBERS.map((no) => ({ no, title: SECTION_TITLES[no] }))

/** Where each numbered section of the official form is on the working page: the sections keep the paper form's numbers. */
export function sectionTargetId(section: number): string {
  return ({ 1: 'af-fill-section-1', 2: 'af-fill-section-2', 3: 'af-fill-section-3', 4: 'af-fill-section-4', 5: 'af-documents', 6: 'af-assignment', 7: 'af-process' } as Record<number, string>)[section] ?? 'af-fill-section-1'
}

/** The form model still holds WhatsApp and e-mail; the official form has neither. */
const REMOVED_FIELDS = ['personal.whatsapp', 'personal.email']
const FORM_KEYS = new Set<string>([...ALL_EDITABLE_FIELDS.map((field) => field.path).filter((path) => !REMOVED_FIELDS.includes(path)), 'education', 'experience.history'])

/** Only what the official form asks for (its fields, the education and experience rows, and documents): nothing the server may still list for removed extras. */
export const visibleMissing = (items: MissingItem[]): MissingItem[] => items.filter((item) => item.kind === 'document' || FORM_KEYS.has(item.key))

/** The two ways of looking at the same form: a website layout to fill in, and the paper form to read and print. */
export type FormView = 'fill' | 'form'

export const FORM_VIEWS: { value: FormView; label: string }[] = [
  { value: 'fill', label: 'Fill details' },
  { value: 'form', label: 'Form view' },
]

/** People who may work the candidate start on the layout for filling in; everyone else starts on the paper form. */
export const defaultView = (canEdit: boolean): FormView => (canEdit ? 'fill' : 'form')

/** "5%" as one label, whole numbers only, never below 0 or above 100. */
export function ringLabel(percent: number): string {
  const value = Number.isFinite(percent) ? Math.min(100, Math.max(0, Math.round(percent))) : 0
  return `${value}%`
}

/** "5 of 8 required documents"; nothing required yet reads as a sentence, not "0 of 0". */
export function documentsProgressText(progress: { uploaded: number; total: number }): string {
  return progress.total > 0 ? `${progress.uploaded} of ${progress.total} required documents` : 'No documents are required yet'
}

const text = (value: string | null | undefined): string => (value ?? '').trim()

/** "Welder · ORD-1": the title and the reference of a job, whichever of them is known. */
export function jobText(job: { title?: string; ref?: string } | null | undefined): string {
  return [text(job?.title), text(job?.ref)].filter(Boolean).join(' · ')
}

export interface PaperLine {
  key: 'client' | 'job' | 'country' | 'source' | 'agency' | 'recruiter'
  label: string
  value: string
}

const line = (key: PaperLine['key'], label: string, value: string): PaperLine[] => (text(value) ? [{ key, label, value: text(value) }] : [])

/**
 * The lines under the letterhead: Client, Job, Source and Agency, from the same live assignment the pickers edit (so a pick
 * shows before it is saved). Entries without a value are left out. When nothing is known except the source, the strip is not
 * printed at all, because a lone "Source: Direct" under the address says nothing about this candidate.
 */
export function identLines(assignment: AssignmentData | null | undefined): PaperLine[] {
  if (!assignment) return []
  const lines = [
    ...line('client', 'Client', assignment.client?.name ?? ''),
    ...line('job', 'Job', jobText(assignment.job)),
    ...line('source', 'Source', assignment.source?.label ?? ''),
    ...line('agency', 'Agency', cleanAgencyName(assignment.agency?.name)),
  ]
  return lines.some((item) => item.key !== 'source') ? lines : []
}

/**
 * The rows of section 6, "Client and recruitment details": Client, Job, Country, Source, Agency, Recruiter. Empty rows are left
 * out; an empty result means the page prints one muted line instead (RECRUITMENT_EMPTY_TEXT). The country of a job that is
 * only just picked is not known until the server answers, so it is taken from the saved recruitment details only while the
 * job is still the one those details describe.
 */
export function recruitmentRows(
  assignment: AssignmentData | null | undefined,
  recruitment?: Pick<RecruitmentDetails, 'country'> | null,
  activeOrderId = '',
): PaperLine[] {
  if (!assignment) return []
  const job = assignment.job
  const sameJob = Boolean(job?.orderId) && job?.orderId === activeOrderId
  const country = text(job?.country) || (sameJob ? text(recruitment?.country) : '')
  return [
    ...line('client', 'Client', assignment.client?.name ?? ''),
    ...line('job', 'Job', jobText(job)),
    ...line('country', 'Country', country),
    ...line('source', 'Source', assignment.source?.label ?? ''),
    ...line('agency', 'Agency', cleanAgencyName(assignment.agency?.name)),
    ...line('recruiter', 'Recruiter', assignment.recruiter?.name ?? ''),
  ]
}

export const RECRUITMENT_EMPTY_TEXT = 'Not matched to a job yet'

export interface ReceivedDocument {
  kind: DocumentKind
  label: string
  fileNames: string[]
}

/**
 * The documents that have arrived, in the checklist's order, with their file names. A document that has not arrived is not
 * listed (not even a required one: the checklist and the side panel say what is still missing), so an optional document or
 * one that is not due yet can never print as missing.
 */
export function documentsReceived(
  documents: Record<DocumentKind, Pick<DocumentSlot, 'label' | 'uploaded' | 'current' | 'files' | 'multiple'>>,
  order: DocumentKind[] = Object.keys(documents),
): ReceivedDocument[] {
  const out: ReceivedDocument[] = []
  for (const kind of order) {
    const slot = documents[kind]
    if (!slot || !slot.uploaded) continue
    const files = slot.multiple ? slot.files : slot.current ? [slot.current] : []
    const fileNames = files.map((file) => text(file.fileName)).filter(Boolean)
    out.push({ kind, label: slot.label, fileNames })
  }
  return out
}

/** The same, straight from the form. */
export const receivedFromForm = (form: Pick<ApplicationFormData, 'documents' | 'documentOrder'>): ReceivedDocument[] => documentsReceived(form.documents, form.documentOrder)

/* ------------------------------------------------------------------ The top bar: live title, subtitle and percentage */

/** What an unnamed candidate is stored as until somebody reads the name from a document. It is never shown as a name. */
export const PLACEHOLDER_NAME = 'Name needs review'
export const UNNAMED_TEXT = 'Unnamed candidate'

const realName = (value: string | null | undefined): string => {
  const name = text(value)
  return name.toLowerCase() === PLACEHOLDER_NAME.toLowerCase() ? '' : name
}

/**
 * The title of the top bar: the name as it is typed in the draft (so it follows every keystroke), or the saved name until the draft
 * is there. An empty name, or the placeholder text, reads "Unnamed candidate" and is shown muted.
 */
export function candidateTitle(typed: string | undefined, saved: string | null | undefined): { text: string; muted: boolean } {
  const name = realName(typed !== undefined ? typed : saved)
  return name ? { text: name, muted: false } : { text: UNNAMED_TEXT, muted: true }
}

/** A reference is shown only when it is a short readable code (CND-01004); the long machine ids are left out. */
export const MAX_REFERENCE_LENGTH = 14
export const readableReference = (reference: string | null | undefined): string => (text(reference).length <= MAX_REFERENCE_LENGTH ? text(reference) : '')

/** "CND-01004 · Client name · Job title": the candidate's reference and the live assignment; parts without a value are left out. */
export function candidateSubtitle(reference: string | null | undefined, assignment: Pick<AssignmentData, 'client' | 'job'> | null | undefined): string {
  return [readableReference(reference), text(assignment?.client?.name), text(assignment?.job?.title) || text(assignment?.job?.ref)].filter(Boolean).join(' · ')
}

/** "5% complete". */
export const completeText = (percent: number): string => `${ringLabel(percent)} complete`

/* ------------------------------------------------------------------ Several applications: the Job list */

/** The candidate's applications for the Job list: the ones still running first (the one on screen leading them), then the closed ones. */
export function applicationOptions(applications: ApplicationRef[], activeId = ''): ApplicationRef[] {
  const running = (item: ApplicationRef) => (item.status === 'active' || item.status === '' ? 0 : 1)
  return [...applications].sort((a, b) => running(a) - running(b) || Number(b.id === activeId) - Number(a.id === activeId))
}

/** "ORD-1 · Welder · Gulf Builders (shown now)"; closed ones say so. */
export function applicationOptionText(item: Pick<ApplicationRef, 'orderRef' | 'position' | 'clientName' | 'status'>, shown: boolean): string {
  const closed = item.status !== 'active' && item.status !== '' ? ` (${item.status.replace(/_/g, ' ')})` : ''
  return `${[text(item.orderRef), text(item.position), text(item.clientName)].filter(Boolean).join(' · ')}${shown ? ' (shown now)' : ''}${closed}`
}
