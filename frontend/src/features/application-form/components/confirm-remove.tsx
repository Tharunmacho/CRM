import { useState } from 'react'
import { Trash2 } from 'lucide-react'
import { Button } from '@/shared/ui/primitives'
import { Dialog } from '@/shared/ui/overlay'

/**
 * The small "Remove this?" confirmation of the application form (the site's Dialog, not a browser prompt). `onConfirm` does the work
 * and throws the server's sentence when it fails; the dialog stays open and shows it, so nothing is lost silently.
 */
export function ConfirmRemoveDialog({ title, description, detail, confirmLabel = 'Remove', cancelLabel = 'Keep it', onClose, onConfirm }: {
  title: string
  description: string
  /** One plain line under the description, for example the file name. */
  detail?: string
  confirmLabel?: string
  cancelLabel?: string
  onClose: () => void
  onConfirm: () => Promise<void>
}) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const go = async () => {
    if (busy) return
    setBusy(true); setError('')
    try { await onConfirm(); onClose() }
    catch (cause) { setError(cause instanceof Error && cause.message ? cause.message : 'This could not be removed. Try again.'); setBusy(false) }
  }
  return (
    <Dialog open onClose={() => { if (!busy) onClose() }} title={title} description={description} width="sm"
      footer={<><Button variant="secondary" disabled={busy} onClick={onClose}>{cancelLabel}</Button><Button variant="danger" icon={<Trash2 className="h-4 w-4" />} disabled={busy} onClick={() => void go()}>{busy ? 'Removing…' : confirmLabel}</Button></>}>
      {detail && <p className="text-sm text-ink-600 break-words">{detail}</p>}
      {error && <p role="alert" className="mt-3 text-sm text-stamp-700">{error}</p>}
    </Dialog>
  )
}
