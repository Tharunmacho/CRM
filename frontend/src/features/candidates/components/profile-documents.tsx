import type { DocumentSlot as ServerSlot } from '@/shared/model/types'
import { DocumentSlotRow } from '@/shared/ui/file-drop'
import { profileDocuments, type ProfileDocument } from '../profile-documents-model'
import { CertificateSlotRow } from './certificate-slot-row'

export type DocumentSlot = ProfileDocument

/**
 * Every document slot of a candidate in the shared order and with the shared labels: single-file slots as the shared document
 * row (newest version, history, replace), "Other documents" as a multi-file list where each added file is kept. The caller
 * supplies the wrapper (the profile uses `.cp-docs`), so the profile and the stage modal show the same rows.
 */
export function DocumentList({ documents, uploaderName, onUpload, emptyFor }: {
  documents: ServerSlot[]
  uploaderName: (id: string) => string
  /** Provide to let this viewer upload; `key` and `label` identify the slot. Return nothing for slots that cannot be uploaded to. */
  onUpload?: (slot: DocumentSlot) => ((file: File) => Promise<void>) | undefined
  emptyFor?: (slot: DocumentSlot) => string | undefined
}) {
  return (
    <>
      {profileDocuments(documents).map((slot) => slot.multiple
        ? <CertificateSlotRow key={slot.key} label={slot.label} files={slot.versions} uploaderName={uploaderName} onUpload={onUpload?.(slot)} />
        : <DocumentSlotRow key={slot.key} label={slot.label} versions={slot.versions} uploaderName={uploaderName} onUpload={onUpload?.(slot)} emptyLabel={emptyFor?.(slot)} />)}
    </>
  )
}
