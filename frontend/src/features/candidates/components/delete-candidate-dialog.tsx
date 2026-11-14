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
  // The admin's explicit extra confirmation for a candidate who is still on an order.
  const [withdraw, setWithdraw] = useState(false)
  useEffect(() => { setError(''); setWithdraw(false) }, [candidateId, open])

  // A candidate from a bulk import can always be taken back out, with its job and stage history.
  const imported = store.candidate(candidateId)?.sourceDetail === 'Bulk import'
  const blocker = imported ? null : deleteCandidateBlocker(
    store.applications
      .filter((application) => application.candidateId === candidateId)
      .map((application) => ({ status: application.status, orderRef: store.order(application.orderId)?.ref })),
  )
  // On an order only (not deployed): allowed after the extra confirmation, which also takes them off the order(s).
  const withdrawing = !!blocker?.canWithdraw && withdraw
  const stopped = !!blocker && !withdrawing
  const orders = blocker?.orderRefs.length ? `order ${blocker.orderRefs.join(', ')}` : 'the order'
  const close = () => { if (!busy) onClose() }
  const remove = () => {
    if (busy || stopped) return
    setBusy(true); setError('')
    // The store takes the candidate out of every list at once and sends the delete; if the server refuses, it puts
    // the candidate back and says why in a message, so there is nothing to wait for here. Leave for the Data Bank now.
    store.deleteCandidate(candidateId, withdrawing ? { withdrawFromOrders: true } : undefined).catch(() => { /* already shown by the store */ })
    onClose()
    onDeleted()
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
          <Button variant="danger" onClick={remove} disabled={busy || stopped}>
            {busy ? 'Deleting…' : withdrawing ? 'Remove from order and delete' : 'Delete candidate'}
          </Button>
        </>
      }
    >
      <DialogMark />
      {blocker ? (
        <div role="note" className="rounded-control border border-amber-100 bg-amber-50 px-4 py-3 text-sm text-amber-800">
          <p className="flex items-center gap-2 font-medium">
            <AlertTriangle className="h-4 w-4 shrink-0 text-amber-600" aria-hidden="true" />
            {blocker.canWithdraw ? 'This candidate is on an order' : 'This candidate cannot be deleted'}
          </p>
          <p className="mt-2">{blocker.message}</p>
          {blocker.canWithdraw && (
            <label className="mt-3 flex items-start gap-3 font-medium text-amber-900">
              <input type="checkbox" className="mt-0.5 h-4 w-4 shrink-0" checked={withdraw} disabled={busy}
                onChange={(event) => setWithdraw(event.target.checked)} />
              <span>Also remove them from {orders} and delete. Their placement, stages and documents are deleted with them, and this cannot be undone.</span>
            </label>
          )}
        </div>
      ) : (
        <div className="space-y-3 text-sm text-ink-700">
          <p>Deleting {name} also deletes their documents and their history.</p>
          {imported && <p>{name} came from a bulk import: the job they are on and their recorded stages are removed too, and the job's openings are freed.</p>}
          <p className="text-xs text-ink-500">Emails and WhatsApp messages stay in the inbox, and the delete is recorded in the audit log.</p>
        </div>
      )}
      {error && <p role="alert" className="mt-4 rounded-control bg-stamp-50 p-3 text-sm text-stamp-700">{error}</p>}
    </Dialog>
  )
}
