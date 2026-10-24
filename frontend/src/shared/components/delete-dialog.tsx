import { useEffect, useState } from 'react'
import { AlertTriangle } from 'lucide-react'
import { useStore } from '@/shared/state/crm-store'
import { Dialog } from '@/shared/ui/overlay'
import { Button } from '@/shared/ui/primitives'
import { DialogMark } from '@/features/master-data/components/dialog-mark'

type Kind = 'client' | 'company' | 'order'

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`

/**
 * Warn about retained history visible in the snapshot. The server also checks stored
 * documents that are not included in the snapshot before allowing deletion.
 */
export function DeleteRecordDialog({
  kind,
  id,
  open,
  onClose,
  onDeleted,
  onEdit,
}: {
  kind: Kind
  id: string
  open: boolean
  onClose: () => void
  onDeleted?: () => void
  onEdit?: () => void
}) {
  const store = useStore()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  useEffect(() => { setError('') }, [kind, id, open])

  const companies =
    kind === 'client'
      ? store.companies.filter((c) => c.clientId === id)
      : kind === 'company'
        ? store.companies.filter((c) => c.id === id)
        : []
  const companyIds = new Set(companies.map((c) => c.id))
  const orders =
    kind === 'order' ? store.orders.filter((o) => o.id === id) : store.orders.filter((o) => companyIds.has(o.companyId))
  const orderIds = new Set(orders.map((o) => o.id))
  const apps = store.applications.filter((a) => orderIds.has(a.orderId))
  const blockers = apps.filter((a) => a.status === 'active' || a.status === 'deployed')
  const history = apps.filter((a) => a.status === 'closed')
  const agreements = companies.filter((company) => company.agreement)
  const retained = apps.length > 0 || agreements.length > 0
  const close = () => { if (!busy) onClose() }

  const name =
    kind === 'client'
      ? store.client(id)?.name
      : kind === 'company'
        ? store.company(id)?.name
        : `${store.orderLabel(id)} (${store.order(id)?.ref ?? ''})`

  const goes: string[] = []
  if (kind === 'client' && companies.length > 0) {
    goes.push(`${plural(companies.length, 'company', 'companies')}: ${companies.map((c) => c.name).join(', ')}`)
  }
  if (kind !== 'order' && orders.length > 0) {
    goes.push(`${plural(orders.length, 'order', 'orders')}: ${orders.map((o) => o.ref).join(', ')}`)
  }
  const remove = async () => {
    if (busy || retained) return
    setBusy(true); setError('')
    try {
      if (kind === 'client') await store.deleteClient(id)
      else if (kind === 'company') await store.deleteCompany(id)
      else await store.deleteOrder(id)
      onClose()
      onDeleted?.()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'We could not delete this. Please try again.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog
      open={open}
      onClose={close}
      title={`Delete ${name ?? `this ${kind}`}?`}
      description="This cannot be undone."
      footer={
        <>
          <Button variant="ghost" disabled={busy} onClick={close}>
            Cancel
          </Button>
          {onEdit && <Button disabled={busy} onClick={() => { onClose(); onEdit() }}>Change status instead</Button>}
          <Button variant="danger" onClick={remove} disabled={busy || retained}>
            {busy ? 'Deleting…' : `Delete ${kind}`}
          </Button>
        </>
      }
    >
      <DialogMark />
      {retained ? (
        <div className="rounded-control border border-amber-100 bg-amber-50 px-4 py-3 text-sm text-amber-800">
          <p className="flex items-center gap-2 font-medium">
            <AlertTriangle className="h-4 w-4 shrink-0 text-amber-600" />
            This {kind} has records we must keep
          </p>
          <ul className="mt-2 list-disc space-y-1 pl-6">
            {blockers.map((a) => {
              const who = store.candidate(a.candidateId)?.name ?? 'A candidate'
              const ref = store.order(a.orderId)?.ref ?? 'an order'
              return (
                <li key={a.id}>
                  {a.status === 'active'
                    ? `${who} is still active on ${ref}. Their history is kept.`
                    : `${who} was deployed through ${ref}. Deployed candidates stay on record.`}
                </li>
              )
            })}
            {history.length > 0 && <li>{plural(history.length, 'closed candidate application', 'closed candidate applications')} and their history are kept.</li>}
            {agreements.length > 0 && <li>{plural(agreements.length, 'company has an agreement', 'companies have agreements')} that is kept.</li>}
          </ul>
          <p className="mt-3">{kind === 'order' ? 'Set the order to On hold while candidates are active, or Closed when the work is done.' : 'Set the status to Inactive to keep this record and its history.'}</p>
        </div>
      ) : (
        <div className="space-y-3 text-sm text-ink-700">
          {goes.length > 0 ? (
            <>
              <p>Deleting this {kind} also deletes:</p>
              <ul className="list-disc space-y-1 pl-5">
                {goes.map((g) => (
                  <li key={g}>{g}</li>
                ))}
              </ul>
            </>
          ) : (
            <p>Nothing else is linked to this {kind}. We also check saved documents before deleting.</p>
          )}
          {orders.length > 0 && <p className="text-xs text-ink-500">Candidates themselves stay in the Data Bank.</p>}
        </div>
      )}
      {error && <p role="alert" className="mt-4 rounded-control bg-stamp-50 p-3 text-sm text-stamp-700">{error}</p>}
    </Dialog>
  )
}
