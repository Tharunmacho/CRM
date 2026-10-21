import { useEffect, useState } from 'react'
import { AlertTriangle } from 'lucide-react'
import { useStore } from '@/shared/state/crm-store'
import { Dialog } from '@/shared/ui/overlay'
import { Button } from '@/shared/ui/primitives'

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
      setError(cause instanceof Error ? cause.message : 'This record could not be deleted. Please retry.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog
      open={open}
      onClose={close}
      title={`Delete ${name ?? `this ${kind}`}?`}
      description="This can't be undone."
      footer={
        <>
          <Button variant="ghost" disabled={busy} onClick={close}>
            Cancel
          </Button>
          {onEdit && <Button disabled={busy} onClick={() => { onClose(); onEdit() }}>Edit status instead</Button>}
          <Button variant="danger" onClick={remove} disabled={busy || retained}>
            {busy ? 'Deleting…' : `Delete ${kind}`}
          </Button>
        </>
      }
    >
      {retained ? (
        <div className="rounded-control border border-amber-100 bg-amber-50 px-4 py-3 text-sm text-amber-800">
          <p className="flex items-center gap-2 font-medium">
            <AlertTriangle className="h-4 w-4 shrink-0 text-amber-600" />
            This {kind} has records to retain
          </p>
          <ul className="mt-2 list-disc space-y-1 pl-6">
            {blockers.map((a) => {
              const who = store.candidate(a.candidateId)?.name ?? 'A candidate'
              const ref = store.order(a.orderId)?.ref ?? 'an order'
              return (
                <li key={a.id}>
                  {a.status === 'active'
                    ? `${who} is active on ${ref}. The application history is retained.`
                    : `${who} was deployed through ${ref}. Deployed placements are kept on record.`}
                </li>
              )
            })}
            {history.length > 0 && <li>{plural(history.length, 'closed candidate application', 'closed candidate applications')} and their stage history are retained.</li>}
            {agreements.length > 0 && <li>{plural(agreements.length, 'company has an agreement', 'companies have agreements')} to retain.</li>}
          </ul>
          <p className="mt-3">{kind === 'order' ? 'Use On hold while candidates are active, or Closed when work is finished.' : 'Set the status to Inactive to keep this record and its history.'}</p>
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
            <p>No related companies, orders or application history are shown for this {kind}. The server also checks stored documents before deleting.</p>
          )}
          {orders.length > 0 && <p className="text-xs text-ink-500">Candidates themselves stay in the Data Bank.</p>}
        </div>
      )}
      {error && <p role="alert" className="mt-4 rounded-control bg-stamp-50 p-3 text-sm text-stamp-700">{error}</p>}
    </Dialog>
  )
}
