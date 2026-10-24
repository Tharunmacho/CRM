import { useRef, useState } from 'react'
import { Download, FileText, Image as ImageIcon, LoaderCircle, Plus } from 'lucide-react'
import { dateTimeLabel } from '@/shared/lib/format'
import { DOCUMENT_ACCEPT, documentFileError, fileDownloadPath, newestFileVersions } from '@/shared/lib/document-files'
import { fileHref } from '@/shared/api/client'
import type { FileVersion } from '@/shared/model/types'
import { FileLink } from '@/shared/ui/file-drop'

/** Document slots that hold several files side by side (a candidate usually has more than one certificate). */
export const MULTI_DOCUMENT_KEYS = ['education', 'experience']

/**
 * The candidate's certificates, every file on its own row (newest first) with View and Download.
 * Each file added is kept, so "Add certificate" never replaces an earlier one.
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
  const [busy, setBusy] = useState(false)
  const [errors, setErrors] = useState<string[]>([])
  const list = newestFileVersions(files)

  const take = async (picked: File[]) => {
    if (!onUpload || busy || picked.length === 0) return
    setBusy(true); setErrors([])
    const problems: string[] = []
    for (const file of picked) {
      const invalid = documentFileError(file)
      if (invalid) { problems.push(`${file.name}: ${invalid}`); continue }
      try { await onUpload(file) }
      catch (error) { problems.push(`${file.name}: ${error instanceof Error ? error.message : 'We could not upload this file. Please try again.'}`) }
    }
    setErrors(problems)
    setBusy(false)
  }

  return (
    <div className="cp-cert" aria-busy={busy}>
      <div className="cp-cert-head">
        <p className="cp-cert-title">{label}{list.length > 0 && <span className="cp-cert-count tnum">{list.length} {list.length === 1 ? 'file' : 'files'}</span>}</p>
        {onUpload && (
          <>
            <button type="button" className="cp-cert-add" disabled={busy} onClick={() => input.current?.click()}>
              {busy ? <LoaderCircle size={15} className="cp-spin" aria-hidden /> : <Plus size={15} aria-hidden />}
              {busy ? 'Uploading…' : list.length > 0 ? 'Add more' : 'Add files'}
            </button>
            <input ref={input} type="file" multiple accept={DOCUMENT_ACCEPT} aria-label={`Choose ${label}`} className="sr-only" tabIndex={-1} disabled={busy}
              onChange={(event) => { const picked = [...(event.currentTarget.files ?? [])]; event.currentTarget.value = ''; void take(picked) }} />
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
                  <p className="cp-cert-name"><FileLink url={file.url} title={`Open ${file.name}`}>{file.name}</FileLink></p>
                  <p className="cp-cert-meta">{file.sizeKb} KB · {uploaderName?.(file.uploadedBy) || file.uploadedBy || 'Unknown'} · {dateTimeLabel(file.uploadedAt)}</p>
                </div>
                <div className="cp-cert-acts">
                  <FileLink url={file.url} className="cp-cert-link">View</FileLink>
                  <a href={fileHref(fileDownloadPath(file.url))} className="cp-cert-link is-icon" title={`Download ${file.name}`} aria-label={`Download ${file.name}`} onClick={(event) => event.stopPropagation()}><Download size={16} aria-hidden /></a>
                </div>
              </li>
            )
          })}
        </ul>
      )}
      {errors.length > 0 && <ul role="alert" className="cp-cert-errors">{errors.map((message) => <li key={message}>{message}</li>)}</ul>}
    </div>
  )
}
