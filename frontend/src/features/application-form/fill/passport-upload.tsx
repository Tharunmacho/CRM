import { useRef, useState } from 'react'
import { Check, Loader2, Upload } from 'lucide-react'
import { cn } from '@/shared/lib/cn'
import { DOCUMENT_ACCEPT, documentFileError } from '@/shared/lib/document-files'
import type { DocumentKind, DocumentSlot } from '../types'

/**
 * The passport document in the header of section 2: a small status chip (Uploaded / Not uploaded) and an Upload passport /
 * Replace button. It is the same `passport` slot as the Passport row of the Documents list, so uploading from either place
 * shows in both; reading the passport then fills these fields.
 */
export function PassportUpload({ slot, canWork, busy, upload }: { slot: DocumentSlot | undefined; canWork: boolean; busy: boolean; upload: (kind: DocumentKind, file: File) => Promise<void> }) {
  const input = useRef<HTMLInputElement>(null)
  const [error, setError] = useState('')
  const [sending, setSending] = useState(false)
  const working = busy || sending
  const uploaded = Boolean(slot?.uploaded)
  const pick = async (file: File | undefined) => {
    if (!file) return
    const invalid = documentFileError(file)
    if (invalid) { setError(invalid); return }
    setError(''); setSending(true)
    try { await upload('passport', file) } catch (cause) { setError(cause instanceof Error ? cause.message : 'The passport was not uploaded.') } finally { setSending(false) }
  }
  return (
    <>
      <span className={cn('aff-doc', working ? 'is-busy' : uploaded ? 'is-done' : 'is-pending')} role="status">
        {working ? <Loader2 aria-hidden="true" /> : uploaded ? <Check aria-hidden="true" /> : null}
        {working ? 'Uploading…' : uploaded ? 'Passport uploaded' : 'Passport not uploaded'}
      </span>
      {canWork && (
        <>
          <button type="button" className="aff-small-btn" disabled={working} onClick={() => input.current?.click()}>
            <Upload aria-hidden="true" />{uploaded ? 'Replace' : 'Upload passport'}
          </button>
          <input ref={input} type="file" accept={DOCUMENT_ACCEPT} className="sr-only" tabIndex={-1} aria-label="Choose the passport file"
            onChange={(event) => { void pick(event.currentTarget.files?.[0]); event.currentTarget.value = '' }} />
        </>
      )}
      {error && <span className="aff-note is-error" role="alert">{error}</span>}
    </>
  )
}
