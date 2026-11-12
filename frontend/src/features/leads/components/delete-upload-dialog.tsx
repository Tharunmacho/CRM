import { useEffect, useRef, useState } from 'react'
import { AlertTriangle, Loader2, Trash2 } from 'lucide-react'
import { ApiError } from '@/shared/api/client'
import { Dialog } from '@/shared/ui/overlay'
import { Button, Field, Input } from '@/shared/ui/primitives'
import { leadApi, type UploadDeletionPreview, type UploadDeletionResult } from '../lead-api'
import type { LeadBatch } from '../lead-model'
import '@/styles/lead-upload-delete.css'

/** A preview identifies the exact upload and current leads; the server repeats every check when deleting. */
export function DeleteUploadDialog({ batch, onClose, onDeleted, onRefresh }: {
  batch: LeadBatch
  onClose: () => void
  onDeleted: (result: UploadDeletionResult) => void
  onRefresh: () => void
}) {
  const [preview, setPreview] = useState<UploadDeletionPreview | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [confirmation, setConfirmation] = useState('')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [uncertain, setUncertain] = useState(false)
  const [missing, setMissing] = useState(false)
  const [reload, setReload] = useState(0)
  const submitting = useRef(false)

  useEffect(() => {
    const controller = new AbortController()
    setPreview(null); setLoading(true); setError(''); setMissing(false); setConfirmation(''); setUncertain(false)
    leadApi.deletionPreview(batch.id, controller.signal).then((result) => {
      if (!controller.signal.aborted) setPreview(result)
    }).catch((cause) => {
      if (controller.signal.aborted) return
      setMissing(cause instanceof ApiError && cause.status === 404)
      setError(cause instanceof Error ? cause.message : 'The upload could not be checked. Try again.')
    }).finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [batch.id, reload])

  const ready = preview?.canDelete === true && confirmation === 'DELETE' && !loading && !busy
  const close = () => { if (!submitting.current) onClose() }
  const submit = async () => {
    if (!ready || !preview || submitting.current) return
    submitting.current = true; setBusy(true); setError(''); setNotice('')
    let result: UploadDeletionResult
    try { result = await leadApi.permanentlyDeleteBatch(preview, 'DELETE') }
    catch (cause) {
      submitting.current = false; setBusy(false)
      if (cause instanceof ApiError && cause.status === 409) {
        setNotice(`${cause.message} Review the current preview and type DELETE again.`)
        setPreview(null); setConfirmation(''); setReload(value => value + 1)
      } else if (cause instanceof ApiError && cause.status === 404) {
        // Missing is not proof of success, including after a lost response.
        setPreview(null); setConfirmation(''); setMissing(true); setUncertain(false)
        setError('This upload was not found. Refresh uploads to check its current state; deletion was not confirmed.')
      } else if (!(cause instanceof ApiError) || cause.status === 0 || cause.status >= 500) {
        // Preserve the exact preview/token so the server can return its saved receipt on retry.
        setUncertain(true)
        setError('The deletion result could not be confirmed. Retry confirmation to check the same request safely, or close and refresh uploads.')
      } else {
        setPreview(null); setConfirmation(''); setUncertain(false)
        setError(cause.message)
      }
      return
    }
    // Refresh failures must not offer to repeat a successful permanent deletion.
    onDeleted(result)
  }

  return <Dialog open onClose={close} title="Permanently delete upload" width="md"
    footer={<div className="ld-delete-upload-footer">
      <Button disabled={busy} onClick={close}>Cancel</Button>
      <Button variant="danger" disabled={!ready} icon={busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Trash2 className="h-4 w-4" />} onClick={() => void submit()}>
        {busy ? 'Deleting…' : uncertain ? 'Retry confirmation' : 'Permanently delete'}
      </Button>
    </div>}>
    <div className="ld-delete-upload">
      <p className="ld-delete-upload-file"><b>{preview?.fileName || batch.fileName}</b></p>
      {loading && <p role="status">Checking the upload and its current leads…</p>}
      {notice && <p className="ld-delete-upload-warning" role="alert">{notice}</p>}
      {preview && <>
        <dl className="ld-delete-upload-counts">
          <div><dt>Current leads in this upload</dt><dd>{preview.leadCount.toLocaleString()}</dd></div>
          {preview.duplicateRows > 0 && <div><dt>Duplicate rows in original import</dt><dd>{preview.duplicateRows.toLocaleString()}</dd></div>}
          {preview.updatedExistingRows > 0 && <div><dt>Existing rows updated by original import</dt><dd>{preview.updatedExistingRows.toLocaleString()}</dd></div>}
          {(preview.preservedExistingLeadCount ?? 0) > 0 && <div><dt>Existing leads kept from other uploads</dt><dd>{preview.preservedExistingLeadCount!.toLocaleString()}</dd></div>}
        </dl>
        {(preview.duplicateRows > 0 || preview.updatedExistingRows > 0) && <p>Existing leads from other uploads are kept. Any earlier changes this import made to those leads remain.</p>}
        {preview.canDelete ? <>
          <p className="ld-delete-upload-warning"><AlertTriangle size={18} aria-hidden="true" /><span>This permanently removes the upload and its {preview.leadCount.toLocaleString()} current {preview.leadCount === 1 ? 'lead' : 'leads'}, including their lead history, follow-ups and attached files. This cannot be undone.</span></p>
          <Field label="Type DELETE to confirm" hint="Check the file name and current lead count before continuing."><Input autoComplete="off" spellCheck={false} maxLength={6} disabled={busy} value={confirmation} onChange={event => setConfirmation(event.target.value)} /></Field>
        </> : <div className="ld-delete-upload-warning" role="alert"><AlertTriangle size={18} aria-hidden="true" /><div>
          <b>This upload cannot be deleted.</b>
          {preview.protectedCount > 0 && <p>{preview.protectedCount.toLocaleString()} {preview.protectedCount === 1 ? 'lead is' : 'leads are'} protected.</p>}
          <ul>{preview.reasons.map((reason, index) => <li key={`${reason.code}-${index}`}>{reason.message}</li>)}</ul>
          <p>No leads will be partially deleted.</p>
        </div></div>}
      </>}
      {error && <p className="ld-delete-upload-error" role="alert">{error}</p>}
      {!loading && !busy && missing && <Button onClick={onRefresh}>Refresh uploads</Button>}
      {!loading && !busy && !missing && !uncertain && (error || (preview && !preview.canDelete)) && <Button onClick={() => { setNotice(''); setReload(value => value + 1) }}>Check again</Button>}
    </div>
  </Dialog>
}
