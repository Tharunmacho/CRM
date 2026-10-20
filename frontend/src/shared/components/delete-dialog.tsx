import { useState } from 'react'
import { AlertTriangle } from 'lucide-react'
import { useStore } from '@/shared/state/crm-store'
import { Dialog } from '@/shared/ui/overlay'
import { Button } from '@/shared/ui/primitives'

type Kind = 'client' | 'company' | 'order'

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`

/**
 * Confirms a delete and says exactly what goes with it. It works out the same chain and
 * the same guard as the server — a client takes its companies and their orders, and a
 * candidate still active on, or deployed through, one of those orders stops it — so a
 * delete the server would refuse is explained here before anyone presses the button.
 */
export function DeleteRecordDialog({
  kind,
  id,
  open,
  onClose,
  onDeleted,
}: {
  kind: Kind
  id: string
  open: boolean
  onClose: () => void
  onDeleted?: () => void
}) {
  const store = useStore()
  const [busy, setBusy] = useState(false)

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
  if (history.length > 0) {
    goes.push(`${plural(history.length, 'past candidate mapping', 'past candidate mappings')} (unmapped or rejected) and their stage records`)
  }

  const remove = async () => {
    setBusy(true)
    try {
      if (kind === 'client') await store.deleteClient(id)
      else if (kind === 'company') await store.deleteCompany(id)
      else await store.deleteOrder(id)
      onClose()
      onDeleted?.()
    } catch {
      // The store has already shown the server's reason; the dialog stays open on it.
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={`Delete ${name ?? `this ${kind}`}?`}
      description="This can't be undone."
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="danger" onClick={remove} disabled={busy || blockers.length > 0}>
            {busy ? 'Deleting…' : `Delete ${kind}`}
          </Button>
        </>
      }
    >
      {blockers.length > 0 ? (
        <div className="rounded-control border border-amber-100 bg-amber-50 px-4 py-3 text-sm text-amber-800">
          <p className="flex items-center gap-2 font-medium">
            <AlertTriangle className="h-4 w-4 shrink-0 text-amber-600" />
            This {kind} can't be deleted yet
          </p>
          <ul className="mt-2 list-disc space-y-1 pl-6">
            {blockers.map((a) => {
              const who = store.candidate(a.candidateId)?.name ?? 'A candidate'
              const ref = store.order(a.orderId)?.ref ?? 'an order'
              return (
                <li key={a.id}>
                  {a.status === 'active'
                    ? `${who} is active on ${ref}. Unmap them first.`
                    : `${who} was deployed through ${ref}. Deployed placements are kept on record.`}
                </li>
              )
            })}
          </ul>
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
            <p>Nothing else is attached to this {kind}.</p>
          )}
          {orders.length > 0 && <p className="text-xs text-ink-500">Candidates themselves stay in the Data Bank.</p>}
        </div>
      )}
    </Dialog>
  )
}
