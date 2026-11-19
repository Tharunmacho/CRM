/**
 * The status of one row of the Documents list, worked out from the shared document list (`shared/model/document-slots.ts`) and
 * whether a file has arrived. Documents are never locked: every one can be uploaded at any time by a person who may work the
 * candidate. (The stages are the part of the form that shows locks: see `process-stages.ts`.) Plain data in, plain data out, so
 * the rules are tested without a browser.
 *
 *   Uploaded  a file has arrived (green tick).
 *   Pending   still to collect (amber clock): every document that is not optional and has no file yet.
 *   Optional  never counted (the experience certificate, other documents).
 */
import { documentSlot, type DocumentSlot as SharedDocumentEntry } from '@/shared/model/document-slots'
import type { ApplicationFormData, DocumentSlot } from './types'

export type DocumentRowKey = 'uploaded' | 'pending' | 'optional'
export type DocumentRowIcon = 'check' | 'clock' | 'dot'
export type DocumentChip = 'Uploaded' | 'Pending' | 'Optional'

export interface DocumentRowState {
  key: DocumentRowKey
  icon: DocumentRowIcon
  chip: DocumentChip
  /** The upload button cannot be used because the person may not work this candidate. */
  disabled: boolean
  /** Counted in "N of M documents": every document that is not optional. */
  counted: boolean
}

export function documentRowState({ entry, hasFile, canWork }: {
  entry: Pick<SharedDocumentEntry, 'always' | 'fromStage'>
  hasFile: boolean
  canWork: boolean
}): DocumentRowState {
  const optional = !entry.always && entry.fromStage === null
  if (hasFile) return { key: 'uploaded', icon: 'check', chip: 'Uploaded', disabled: !canWork, counted: !optional }
  if (optional) return { key: 'optional', icon: 'dot', chip: 'Optional', disabled: !canWork, counted: false }
  return { key: 'pending', icon: 'clock', chip: 'Pending', disabled: !canWork, counted: true }
}

/** "N of M documents": uploaded among all the documents that are not optional. */
export function documentRowsProgress(states: Pick<DocumentRowState, 'key' | 'counted'>[]): { uploaded: number; total: number; percent: number } {
  const counted = states.filter((item) => item.counted)
  const uploaded = counted.filter((item) => item.key === 'uploaded').length
  return { uploaded, total: counted.length, percent: counted.length ? Math.round((uploaded / counted.length) * 100) : 100 }
}

export const documentsCountText = (progress: { uploaded: number; total: number }): string => `${progress.uploaded} of ${progress.total} documents`

/** Every document of the form with its row state, in the shared order. A document the shared list does not know is collected when the server says it is required. */
export function documentRowStates(
  form: Pick<ApplicationFormData, 'documents' | 'documentOrder'>,
  canWork: boolean,
): { slot: DocumentSlot; state: DocumentRowState }[] {
  return form.documentOrder
    .map((kind) => form.documents[kind])
    .filter((slot): slot is DocumentSlot => Boolean(slot))
    .filter((slot) => slot.slot !== 'application_form' || slot.uploaded || Boolean(slot.current) || slot.files.length > 0)
    .map((slot) => ({
      slot,
      state: documentRowState({ entry: documentSlot(slot.slot) ?? { always: slot.required, fromStage: slot.requiredFromStage }, hasFile: slot.uploaded, canWork }),
    }))
}

/* ------------------------------------------------------------------ The groups of the Documents grid */

export type DocumentGroupKey = 'identity' | 'process' | 'certificates' | 'other'

/** Which group each document sits in on the page: who the candidate is, the process papers, the certificates, and everything else. */
const DOCUMENT_GROUPS: { key: DocumentGroupKey; label: string; kinds: string[] }[] = [
  { key: 'identity', label: 'Identity', kinds: ['resume', 'passport', 'aadhaar', 'photo'] },
  { key: 'process', label: 'Process', kinds: ['medical', 'signed_offer', 'pcc', 'visa', 'visa_stamping', 'air_ticket', 'undertaking_letter', 'emigration', 'insurance', 'invoice'] },
  { key: 'certificates', label: 'Certificates', kinds: ['education_certificate', 'experience_certificate', 'google_review'] },
  { key: 'other', label: 'Other', kinds: [] },
]

export interface DocumentGroup<T> { key: DocumentGroupKey; label: string; rows: T[] }

/**
 * Splits the document rows into the four groups, each in the order listed above; a document the groups do not name (the old
 * application form, a kind a newer server adds) goes to Other, so no document ever disappears. Empty groups are left out.
 */
export function groupDocumentRows<T extends { slot: { slot: string } }>(rows: T[]): DocumentGroup<T>[] {
  const named = new Set(DOCUMENT_GROUPS.flatMap((group) => group.kinds))
  return DOCUMENT_GROUPS
    .map((group) => ({
      key: group.key,
      label: group.label,
      rows: group.key === 'other'
        ? rows.filter((row) => !named.has(row.slot.slot) || group.kinds.includes(row.slot.slot))
        : group.kinds.map((kind) => rows.find((row) => row.slot.slot === kind)).filter((row): row is T => Boolean(row)),
    }))
    .filter((group) => group.rows.length > 0)
}

/** "2 of 4" for one group: the counted documents that have arrived, over the counted ones; nothing for a group of optional papers only. */
export function groupProgressText(rows: { state: Pick<DocumentRowState, 'key' | 'counted'> }[]): string {
  const progress = documentRowsProgress(rows.map((row) => row.state))
  return progress.total > 0 ? `${progress.uploaded} of ${progress.total}` : ''
}
