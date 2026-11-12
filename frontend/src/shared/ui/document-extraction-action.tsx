import { useRef, useState } from 'react'
import { Loader2, ScanText } from 'lucide-react'
import '@/styles/document-upload-queue.css'

type StoredReadableFile = { id?: string; attachmentId?: string; url?: string; extractionPending?: boolean; identityMismatch?: boolean; extraction?: { identityMismatch?: boolean } | null }

/** Stored files are read only after the staff member says all their pages are ready. */
export function DocumentExtractionAction({ files, disabled = false, onExtract }: { files: readonly StoredReadableFile[]; disabled?: boolean; onExtract?: (attachmentIds: string[]) => Promise<void> }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const sending = useRef(false)
  const ids = files.filter((file) => file.url && file.extractionPending && !file.identityMismatch && !file.extraction?.identityMismatch).map((file) => file.attachmentId || file.id || '').filter(Boolean)
  if (!onExtract || !ids.length) return null
  const extract = async () => {
    if (disabled || sending.current) return
    sending.current = true; setBusy(true); setError('')
    try { await onExtract(ids) }
    catch (cause) { setError(cause instanceof Error ? cause.message : 'Reading could not be started. Your uploaded files are saved.') }
    finally { sending.current = false; setBusy(false) }
  }
  return <div className="document-extraction-action">
    <p>{ids.length} {ids.length === 1 ? 'file is' : 'files are'} ready. Add all pages, then extract details.</p>
    <button type="button" disabled={disabled || busy} onClick={() => void extract()}>{busy ? <Loader2 className="animate-spin" aria-hidden="true" /> : <ScanText aria-hidden="true" />}{busy ? 'Starting reading…' : 'Extract details'}</button>
    {error && <p className="is-error" role="alert">{error}</p>}
  </div>
}
