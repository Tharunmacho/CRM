import { useRef, useState } from 'react'
import { Camera, Loader2 } from 'lucide-react'
import { fileHref } from '@/shared/api/client'
import { documentFileError } from '@/shared/lib/document-files'
import type { DocumentKind, FormDocument } from '../types'

const PHOTO_TYPES = /^image\//
const PHOTO_NAME = /\.(jpe?g|png|webp|heic)$/i

/** The passport-size photo tile of the Personal information card: shows the photo and an Upload / Replace button. */
export function PhotoTile({ photo, canWork, busy, upload }: { photo: FormDocument | null; canWork: boolean; busy: boolean; upload: (kind: DocumentKind, file: File) => Promise<void> }) {
  const input = useRef<HTMLInputElement>(null)
  const [error, setError] = useState('')
  const [sending, setSending] = useState(false)
  const working = busy || sending
  const pick = async (file: File | undefined) => {
    if (!file) return
    const invalid = documentFileError(file)
    if (invalid) { setError(invalid); return }
    if (!PHOTO_TYPES.test(file.type) && !PHOTO_NAME.test(file.name)) { setError('Choose a JPG, PNG, WebP or HEIC photo.'); return }
    setError(''); setSending(true)
    try { await upload('photo', file) } catch (cause) { setError(cause instanceof Error ? cause.message : 'The photo was not uploaded.') } finally { setSending(false) }
  }
  return (
    <div className="aff-photo" data-field="documents.photo">
      {/* With a photo the picture is the button too: clicking the frame opens the same file picker as Replace photo. */}
      <button type="button" className="aff-photo-frame" data-has-photo={photo ? 'true' : 'false'} disabled={!canWork || working} tabIndex={-1} aria-hidden="true" onClick={() => input.current?.click()}>
        {photo?.url ? <img src={fileHref(photo.url)} alt="" /> : <><Camera aria-hidden="true" /><span>Photo</span></>}
        {working && <span className="aff-photo-busy"><Loader2 aria-hidden="true" />Uploading…</span>}
      </button>
      {photo?.url && <span className="sr-only">Candidate photo uploaded</span>}
      {working && <span className="sr-only" role="status">Uploading the photo</span>}
      {canWork && (
        <>
          <button type="button" className="aff-photo-btn" disabled={working} onClick={() => input.current?.click()}>{photo ? 'Replace photo' : 'Upload photo'}</button>
          <input ref={input} type="file" accept="image/jpeg,image/png,image/webp,image/heic,.jpg,.jpeg,.png,.webp,.heic" className="sr-only" tabIndex={-1} aria-label="Choose a photo"
            onChange={(event) => { void pick(event.currentTarget.files?.[0]); event.currentTarget.value = '' }} />
        </>
      )}
      {error && <p className="aff-note is-error" role="alert">{error}</p>}
    </div>
  )
}
