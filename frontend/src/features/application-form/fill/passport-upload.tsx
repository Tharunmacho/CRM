import { useRef } from 'react'
import { Check, Eye, Loader2, Upload } from 'lucide-react'
import { cn } from '@/shared/lib/cn'
import { DOCUMENT_ACCEPT } from '@/shared/lib/document-files'
import { fileHref } from '@/shared/api/client'
import { DocumentUploadQueue, useDocumentUploadQueue } from '@/shared/ui/document-upload-queue'
import { DocumentExtractionAction } from '@/shared/ui/document-extraction-action'
import { incompleteReadingText } from '@/features/candidates/read-report'
import type { DocumentKind, DocumentSlot } from '../types'

/**
 * The passport document in the header of section 2: a small status chip (Uploaded / Not uploaded) and an Upload passport /
 * Replace button. It is the same `passport` slot as the Passport row of the Documents list, so uploading from either place
 * shows in both; reading the passport then fills these fields.
 */
export function PassportUpload({ slot, canWork, busy, upload, onExtract }: { slot: DocumentSlot | undefined; canWork: boolean; busy: boolean; upload: (kind: DocumentKind, file: File) => Promise<void>; onExtract?: (attachmentIds: string[]) => Promise<void> }) {
  const input = useRef<HTMLInputElement>(null)
  const queue = useDocumentUploadQueue((file) => upload('passport', file), { multiple: true, disabled: busy || !canWork })
  const working = busy || queue.busy
  const files = slot?.files.length ? slot.files : slot?.current ? [slot.current] : []
  const uploaded = files.some((file) => Boolean(file.url))
  const rejectedOnly = uploaded && files.every((file) => file.identityMismatch)
  return (
    <div className="aff-passport-upload" onDragOver={(event) => event.preventDefault()} onDrop={(event) => { event.preventDefault(); if (canWork && !working) void queue.take(event.dataTransfer.files) }}>
      <span className={cn('aff-doc', working ? 'is-busy' : rejectedOnly ? 'is-pending' : uploaded ? 'is-done' : 'is-pending')} role="status">
        {working ? <Loader2 aria-hidden="true" /> : uploaded && !rejectedOnly ? <Check aria-hidden="true" /> : null}
        {working ? 'Uploading…' : rejectedOnly ? 'Wrong document · Add correct passport' : uploaded ? `Uploaded · ${files.length} ${files.length === 1 ? 'file' : 'files'}` : slot?.manualStatus?.uploaded ? 'Passport marked manually' : 'Passport not uploaded'}
      </span>
      {canWork && (
        <>
          <button type="button" className="aff-small-btn" disabled={working} onClick={() => input.current?.click()}>
            <Upload aria-hidden="true" />Add passport files
          </button>
          <input ref={input} type="file" multiple accept={DOCUMENT_ACCEPT} disabled={working} className="sr-only" tabIndex={-1} aria-label="Choose the passport files"
            onChange={(event) => { void queue.take(event.currentTarget.files); event.currentTarget.value = '' }} />
        </>
      )}
      {files.length > 0 && <details className="aff-passport-files"><summary>Passport files ({files.length})</summary><ul>{files.map((file) => <li key={file.attachmentId}>{file.url ? <a href={fileHref(file.url)} target="_blank" rel="noreferrer"><Eye aria-hidden="true" />{file.fileName}</a> : <span>{file.fileName}</span>}{file.extraction && <small role={file.extraction.identityMismatch ? 'alert' : undefined}>{file.extraction.identityMismatch ? 'Wrong document. No details were filled. Upload the correct document.' : incompleteReadingText(file.extraction.coverage) || (file.extraction.status === 'processing' ? 'Reading this file…' : file.extraction.status === 'done' ? 'Reading completed' : file.extraction.message || 'Check this file: reading needs attention.')}</small>}</li>)}</ul></details>}
      <DocumentUploadQueue queue={queue} />
      {canWork && <DocumentExtractionAction files={files} disabled={working} onExtract={onExtract} />}
    </div>
  )
}
