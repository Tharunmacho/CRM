import type { DocumentSlot as ServerSlot } from '@/shared/model/types'
import { Fragment } from 'react'
import { dateTimeLabel } from '@/shared/lib/format'
import { newestFileVersions } from '@/shared/lib/document-files'
import { DocumentExtractionAction } from '@/shared/ui/document-extraction-action'
import { DocumentSlotRow } from '@/shared/ui/file-drop'
import { useStore } from '@/shared/state/crm-store'
import { profileDocuments, type ProfileDocument } from '../profile-documents-model'
import { CertificateSlotRow } from './certificate-slot-row'

export type DocumentSlot = ProfileDocument

/**
 * Every document slot of a candidate in the shared order and with the shared labels: single-file slots as the shared document
 * row (newest version, history, replace), "Other documents" as a multi-file list where each added file is kept. The caller
 * supplies the wrapper (the profile uses `.cp-docs`), so the profile and the stage modal show the same rows.
 */
export function DocumentList({ documents, uploaderName, onUpload, emptyFor, onExtract }: {
  documents: ServerSlot[]
  uploaderName: (id: string) => string
  /** Provide to let this viewer upload; `key` and `label` identify the slot. Return nothing for slots that cannot be uploaded to. */
  onUpload?: (slot: DocumentSlot) => ((file: File) => Promise<void>) | undefined
  emptyFor?: (slot: DocumentSlot) => string | undefined
  onExtract?: (attachmentIds: string[]) => Promise<void>
}) {
  const invoices = useStore().can('view_invoices')
  return (
    <>
      {profileDocuments(documents, { invoices }).map((slot) => {
        const marked = slot.manualStatus?.uploaded === true && !slot.versions.some((file) => file.url)
        const emptyLabel = marked ? 'Marked manually · No file attached' : emptyFor?.(slot)
        return <Fragment key={slot.key}>{slot.multiple
          ? <CertificateSlotRow label={slot.label} files={slot.versions} uploaderName={uploaderName} onUpload={onUpload?.(slot)} emptyLabel={emptyLabel} />
          : <DocumentSlotRow label={slot.label} versions={slot.versions} uploaderName={uploaderName} onUpload={onUpload?.(slot)} emptyLabel={emptyLabel} />}
          {!slot.multiple && newestFileVersions(slot.versions)[0]?.identityMismatch && <p className="document-identity-error" role="alert">Wrong document. No details were filled. Upload the correct document.</p>}
          {marked && <p className="cp-doc-manual-note">{slot.manualStatus?.by.name || 'Former team member'}{slot.manualStatus?.at ? ` · ${dateTimeLabel(slot.manualStatus.at)}` : ''}{slot.manualStatus?.note ? ` · ${slot.manualStatus.note}` : ''}</p>}
          <DocumentExtractionAction files={slot.versions} onExtract={onExtract} />
        </Fragment>
      })}
    </>
  )
}
