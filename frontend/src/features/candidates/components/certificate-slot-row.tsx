import { useRef, useState } from 'react'
import { Download, FileText, Image as ImageIcon, LoaderCircle, Plus } from 'lucide-react'
import { dateTimeLabel } from '@/shared/lib/format'
import { DOCUMENT_ACCEPT, fileDownloadPath, newestFileVersions } from '@/shared/lib/document-files'
import { fileHref } from '@/shared/api/client'
import type { FileVersion } from '@/shared/model/types'
import { FileLink } from '@/shared/ui/file-drop'
import { DocumentUploadQueue, useDocumentUploadQueue } from '@/shared/ui/document-upload-queue'

/**
 * A slot that keeps several files side by side (Other documents), every file on its own row (newest first) with View and Download.
 * Each file added is kept, so adding never replaces an earlier one.
 */
export function CertificateSlotRow({ label, files, onUpload, uploaderName, emptyLabel = 'Not uploaded' }: {
  label: string
  files: FileVersion[]
  /** Called once per chosen file; omit for a read-only list. */
  onUpload?: (file: File) => void | Promise<void>
  uploaderName?: (id: string) => string
  emptyLabel?: string
}) {
  const input = useRef<HTMLInputElement>(null)
  const [dragging, setDragging] = useState(false)
  const queue = useDocumentUploadQueue(onUpload, { multiple: true })
  const busy = queue.busy
  const list = newestFileVersions(files)

  return (
    <div className={`cp-cert${dragging ? ' is-dragging' : ''}`} aria-busy={busy} onDragOver={(event) => { event.preventDefault(); if (onUpload && !busy) setDragging(true) }} onDragLeave={() => setDragging(false)} onDrop={(event) => { event.preventDefault(); setDragging(false); if (onUpload && !busy) void queue.take(event.dataTransfer.files) }}>
      <div className="cp-cert-head">
        <p className="cp-cert-title">{label}{list.length > 0 && <span className="cp-cert-count tnum">{list.length} {list.length === 1 ? 'file' : 'files'}</span>}</p>
        {onUpload && (
          <>
            <button type="button" className="cp-cert-add" disabled={busy} onClick={() => input.current?.click()}>
              {busy ? <LoaderCircle size={15} className="cp-spin" aria-hidden /> : <Plus size={15} aria-hidden />}
              {busy ? 'Uploading…' : 'Add files'}
            </button>
            <input ref={input} type="file" multiple accept={DOCUMENT_ACCEPT} aria-label={`Choose ${label}`} className="sr-only" tabIndex={-1} disabled={busy}
              onChange={(event) => { const picked = [...(event.currentTarget.files ?? [])]; event.currentTarget.value = ''; void queue.take(picked) }} />
          </>
        )}
      </div>
      {list.length === 0 ? <p className="cp-cert-empty">{emptyLabel}</p> : (
        <ul className="cp-cert-list">
          {list.map((file) => {
            const Icon = /\.(jpe?g|png|webp|heic)$/i.test(file.name) ? ImageIcon : FileText
            return (
              <li key={file.id} className="cp-cert-item">
                <Icon size={16} className="cp-cert-icon" aria-hidden />
                <div className="cp-cert-main">
                  <p className="cp-cert-name">{file.url ? <FileLink url={file.url} title={`Open ${file.name}`}>{file.name}</FileLink> : file.name}</p>
                  <p className="cp-cert-meta">{file.sizeKb} KB · {uploaderName?.(file.uploadedBy) || file.uploadedBy || 'Unknown'} · {dateTimeLabel(file.uploadedAt)}</p>
                  {file.identityMismatch && <p className="document-identity-error" role="alert">Wrong document. No details were filled. Upload the correct file using Add files.</p>}
                </div>
                {file.url && <div className="cp-cert-acts">
                  <FileLink url={file.url} className="cp-cert-link">View</FileLink>
                  <a href={fileHref(fileDownloadPath(file.url))} className="cp-cert-link is-icon" title={`Download ${file.name}`} aria-label={`Download ${file.name}`} onClick={(event) => event.stopPropagation()}><Download size={16} aria-hidden /></a>
                </div>}
              </li>
            )
          })}
        </ul>
      )}
      <DocumentUploadQueue queue={queue} />
    </div>
  )
}
