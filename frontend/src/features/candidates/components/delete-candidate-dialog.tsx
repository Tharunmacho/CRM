import { useEffect, useState } from 'react'
import { AlertTriangle } from 'lucide-react'
import { useStore } from '@/shared/state/crm-store'
import { Dialog } from '@/shared/ui/overlay'
import { Button } from '@/shared/ui/primitives'
import { DialogMark } from '@/features/master-data/components/dialog-mark'
import { deleteCandidateBlocker } from '@/features/candidates/profile-actions'

/** Admin only. Same look and wording as the client, company and order delete dialog. */
export function DeleteCandidateDialog({
  candidateId,
  name,
  open,
  onClose,
  onDeleted,
}: {
  candidateId: string
  name: string
  open: boolean
  onClose: () => void
  onDeleted: () => void
}) {
  const store = useStore()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  useEffect(() => { setError('') }, [candidateId, open])

  const blocker = deleteCandidateBlocker(
    store.applications
      .filter((application) => application.candidateId === candidateId)
      .map((application) => ({ status: application.status, orderRef: store.order(application.orderId)?.ref })),
  )
  const close = () => { if (!busy) onClose() }
  const remove = async () => {
    if (busy || blocker) return
    setBusy(true); setError('')
    try {
      await store.deleteCandidate(candidateId)
      onClose()
      onDeleted()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'We could not delete this candidate. Please try again.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog
      open={open}
      onClose={close}
      title={`Delete ${name}?`}
      description="This cannot be undone."
      footer={
        <>
          <Button variant="ghost" disabled={busy} onClick={close}>Cancel</Button>
          <Button variant="danger" onClick={remove} disabled={busy || !!blocker}>
            {busy ? 'Deleting…' : 'Delete candidate'}
          </Button>
        </>
      }
    >
      <DialogMark />
      {blocker ? (
        <div role="note" className="rounded-control border border-amber-100 bg-amber-50 px-4 py-3 text-sm text-amber-800">
          <p className="flex items-center gap-2 font-medium">
            <AlertTriangle className="h-4 w-4 shrink-0 text-amber-600" aria-hidden="true" />
            This candidate cannot be deleted
          </p>
          <p className="mt-2">{blocker}</p>
        </div>
      ) : (
        <div className="space-y-3 text-sm text-ink-700">
          <p>Deleting {name} also deletes their documents and their history.</p>
          <p className="text-xs text-ink-500">Emails and WhatsApp messages stay in the inbox, and the delete is recorded in the audit log.</p>
        </div>
      )}
      {error && <p role="alert" className="mt-4 rounded-control bg-stamp-50 p-3 text-sm text-stamp-700">{error}</p>}
    </Dialog>
  )
}
