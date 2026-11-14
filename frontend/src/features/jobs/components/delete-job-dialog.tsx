import { useEffect, useState } from 'react'
import { AlertTriangle } from 'lucide-react'
import { useStore } from '@/shared/state/crm-store'
import { Dialog } from '@/shared/ui/overlay'
import { Button } from '@/shared/ui/primitives'
import { DialogMark } from '@/features/master-data/components/dialog-mark'
import { orderDeleteBlocker, serverAsksToConfirm } from '@/features/jobs/order-delete'

/** Admin only. Same look as the candidate delete dialog; a job with candidates needs the admin's extra confirmation. */
export function DeleteJobDialog({
  orderId,
  open,
  onClose,
  onDeleted,
  onEdit,
}: {
  orderId: string
  open: boolean
  onClose: () => void
  onDeleted?: () => void
  onEdit?: () => void
}) {
  const store = useStore()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  // The admin's explicit confirmation to remove the candidates on the job together with it.
  const [withdraw, setWithdraw] = useState(false)
  // The server can know of candidates this page has not loaded yet; its refusal then brings the checkbox up too.
  const [serverConfirm, setServerConfirm] = useState(false)
  useEffect(() => { setError(''); setWithdraw(false); setServerConfirm(false) }, [orderId, open])

  const order = store.order(orderId)
  const name = `${store.orderLabel(orderId)} (${order?.ref ?? ''})`
  const known = orderDeleteBlocker(
    store.applications.filter((application) => application.orderId === orderId),
    order?.ref ?? 'this job',
    (id) => store.candidate(id)?.name,
  )
  const blocker = known ?? (serverConfirm ? { message: 'Candidates are on this job.', canWithdraw: true, candidates: 0, active: 0 } : null)
  const withdrawing = !!blocker?.canWithdraw && withdraw
  const stopped = !!blocker && !withdrawing
  const close = () => { if (!busy) onClose() }
  const remove = async () => {
    if (busy || stopped) return
    setBusy(true); setError('')
    try {
      await store.deleteOrder(orderId, withdrawing ? { withdrawApplications: true } : undefined)
      onClose()
      onDeleted?.()
    } catch (cause) {
      if (serverAsksToConfirm(cause)) setServerConfirm(true)
      setError(cause instanceof Error ? cause.message : 'We could not delete this job. Please try again.')
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
          {onEdit && blocker && !blocker.canWithdraw && <Button disabled={busy} onClick={() => { onClose(); onEdit() }}>Change status instead</Button>}
          <Button variant="danger" onClick={remove} disabled={busy || stopped}>
            {busy ? 'Deleting…' : withdrawing ? 'Remove candidates and delete job' : 'Delete job'}
          </Button>
        </>
      }
    >
      <DialogMark />
      {blocker ? (
        <div role="note" className="rounded-control border border-amber-100 bg-amber-50 px-4 py-3 text-sm text-amber-800">
          <p className="flex items-center gap-2 font-medium">
            <AlertTriangle className="h-4 w-4 shrink-0 text-amber-600" aria-hidden="true" />
            {blocker.canWithdraw ? 'This job has candidates on it' : 'This job cannot be deleted'}
          </p>
          <p className="mt-2">{blocker.message}</p>
          {blocker.canWithdraw && (
            <label className="mt-3 flex items-start gap-3 font-medium text-amber-900">
              <input type="checkbox" className="mt-0.5 h-4 w-4 shrink-0" checked={withdraw} disabled={busy}
                onChange={(event) => setWithdraw(event.target.checked)} />
              <span>Also remove the candidates from this job and delete it. Their applications, stage history and stage documents on this job are deleted, active candidates go back to the Data Bank, and this cannot be undone.</span>
            </label>
          )}
        </div>
      ) : (
        <div className="space-y-3 text-sm text-ink-700">
          <p>No candidates are on this job. Deleting it removes the job and its notices.</p>
          <p className="text-xs text-ink-500">The delete is recorded in the audit log.</p>
        </div>
      )}
      {error && <p role="alert" className="mt-4 rounded-control bg-stamp-50 p-3 text-sm text-stamp-700">{error}</p>}
    </Dialog>
  )
}
