import { useRef, useState } from 'react'
import { Camera, Loader2 } from 'lucide-react'
import { fileHref } from '@/shared/api/client'
import { documentFileError } from '@/shared/lib/document-files'
import type { ApplicationFormData, FormDocument } from '../types'


/** The paper form's header: logo, company, form title, and the passport-size photo box in the corner. */
export function Letterhead({
  photo,
  canUpload,
  onPhoto,
  reference,
  header,
}: {
  header: ApplicationFormData['header']
  photo: FormDocument | null
  canUpload: boolean
  onPhoto: (file: File) => Promise<void>
  reference: string
}) {
  const input = useRef<HTMLInputElement>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const pick = async (file: File | undefined) => {
    if (!file) return
    const invalid = documentFileError(file)
    if (invalid) { setError(invalid); return }
    if (!/^image\//.test(file.type) && !/\.(jpe?g|png|webp|heic)$/i.test(file.name)) { setError('Choose a JPG, PNG, WebP or HEIC photo.'); return }
    setError(''); setBusy(true)
    try { await onPhoto(file) } catch (cause) { setError(cause instanceof Error ? cause.message : 'The photo was not uploaded.') } finally { setBusy(false) }
  }
  return (
    <header className="af-letterhead">
      <div className="af-brand">
        <img className="af-logo is-light" src="/brand/care-india-logo-tight.png" alt="Care India Overseas" width={768} height={400} decoding="async" draggable={false} />
        <img className="af-logo is-dark" src="/brand/care-india-logo-tight-dark.png" alt="" width={768} height={400} decoding="async" draggable={false} />
      </div>
      <div className="af-heading">
        <p className="af-company">{header.company}</p>
        <h1 className="af-form-title">{header.title}</h1>
        {reference && <p className="af-ref">Ref. <span className="tnum">{reference}</span></p>}
      </div>
      <div className="af-photo-wrap">
        <div className="af-photo" data-has-photo={photo ? 'true' : 'false'}>
          {photo ? <img src={fileHref(photo.url)} alt="Passport-size photo" /> : <span className="af-photo-hint">PASSPORT<br />SIZE<br />PHOTO</span>}
          {busy && <span className="af-photo-busy" role="status"><Loader2 aria-hidden="true" />Uploading…</span>}
          {canUpload && (
            <>
            <button type="button" className="af-photo-btn" disabled={busy} onClick={() => input.current?.click()}>
              <Camera aria-hidden="true" />{photo ? 'Replace photo' : 'Upload photo'}
            </button>
            <input ref={input} type="file" accept="image/jpeg,image/png,image/webp,image/heic,.jpg,.jpeg,.png,.webp,.heic" className="sr-only" tabIndex={-1} aria-label="Choose a passport-size photo" onChange={(event) => { void pick(event.currentTarget.files?.[0]); event.currentTarget.value = '' }} />
            </>
          )}
        </div>
        {error && <p className="af-note is-error" role="alert">{error}</p>}
      </div>
      <p className="af-address">
        <span>{header.address}</span>
        <span>Ph: {header.phone} | E-Mail: {header.email}</span>
      </p>
    </header>
  )
}
