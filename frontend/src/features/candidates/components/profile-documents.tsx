import type { FileVersion } from '@/shared/model/types'
import { DocumentSlotRow } from '@/shared/ui/file-drop'
import { CertificateSlotRow, MULTI_DOCUMENT_KEYS } from './certificate-slot-row'

export interface DocumentSlot { key: string; label: string; versions: FileVersion[] }

/**
 * Every document slot of a candidate: single-file slots as the shared document row (newest version, history,
 * replace), certificate slots as multi-file lists where each added file is kept. The caller supplies the wrapper (the profile
 * uses `.cp-docs`), so the profile and the stage modal show the same rows.
 */
export function DocumentList({ documents, uploaderName, onUpload, emptyFor }: {
  documents: DocumentSlot[]
  uploaderName: (id: string) => string
  /** Provide to let this viewer upload; `key` and `label` identify the slot. Return nothing for slots that cannot be uploaded to. */
  onUpload?: (slot: DocumentSlot) => ((file: File) => Promise<void>) | undefined
  emptyFor?: (slot: DocumentSlot) => string | undefined
}) {
  return (
    <>
      {documents.map((slot) => MULTI_DOCUMENT_KEYS.includes(slot.key)
        ? <CertificateSlotRow key={slot.key} label={slot.label} files={slot.versions} uploaderName={uploaderName} onUpload={onUpload?.(slot)} />
        : <DocumentSlotRow key={slot.key} label={slot.label} versions={slot.versions} uploaderName={uploaderName} onUpload={onUpload?.(slot)} emptyLabel={emptyFor?.(slot)} />)}
    </>
  )
}
