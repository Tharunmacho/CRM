import { useRef, useState } from 'react'
import { Loader2, ShieldAlert } from 'lucide-react'
import { slotReadState } from '../form-model'
import type { DocumentSlot } from '../types'
import '@/styles/application-form-extract.css'

/** Said when the form could not even ask for the reading: the files are saved, and Read again asks once more. */
export const READ_NOT_STARTED_TEXT = 'Reading did not start. Your file is saved. Choose Read again, or fill the details here.'

/**
 * The state of the stored files of one slot that nobody has read yet. Nobody has to start the reading: the form does it by itself, so
 * while that happens the slot says "Reading documents…" in the same blue box as a reading in progress. Only when the request itself
 * failed (`error`, the server's sentence, kept under Details) does it become a calm red box with a Read again button. Nothing at all for
 * a slot that has no unread file, for a viewer who cannot work the candidate, or for a server that does not read by itself (`enabled`).
 */
export function SlotReadingNote({ slot, enabled, error, onRead }: {
  slot: DocumentSlot | undefined
  enabled: boolean
  error: string
  onRead?: (attachmentIds: string[]) => Promise<void>
}) {
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState('')
  const sending = useRef(false)
  const unread = enabled ? slotReadState(slot).unread : []
  if (!unread.length) return null
  if (!error) {
    return (
      <div className="af-extract-line">
        <div className="af-extract is-running" role="status"><Loader2 aria-hidden="true" /><span>Reading documents…</span></div>
      </div>
    )
  }
  const again = async () => {
    if (!onRead || sending.current) return
    sending.current = true; setBusy(true); setProblem('')
    try { await onRead(unread) }
    catch (cause) { setProblem(cause instanceof Error ? cause.message : 'Reading could not be started. Your files are saved.') }
    finally { sending.current = false; setBusy(false) }
  }
  return (
    <div className="af-extract-line">
      <div className="af-extract is-failed" role="alert">
        <ShieldAlert aria-hidden="true" /><span>{READ_NOT_STARTED_TEXT}</span>
        {onRead && <button type="button" className="af-docbtn" disabled={busy} onClick={() => void again()}>{busy ? 'Starting…' : 'Read again'}</button>}
        <details className="af-extract-details">
          <summary>Details</summary>
          <ul><li>{problem || error}</li></ul>
        </details>
      </div>
    </div>
  )
}
