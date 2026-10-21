import { useMemo, useState } from 'react'
import { AlertTriangle } from 'lucide-react'
import { dateLabel } from '@/shared/lib/format'
import { useStore } from '@/shared/state/crm-store'
import { Dialog } from '@/shared/ui/overlay'
import { Button, SearchInput } from '@/shared/ui/primitives'
import { Badge, EmptyState } from '@/shared/ui/display'
import { cn } from '@/shared/lib/cn'

/** Map one candidate to an open order. A candidate works one order at a time. */
export function MapToOrderDialog({
  open,
  onClose,
  candidateId,
}: {
  open: boolean
  onClose: () => void
  candidateId: string
}) {
  const store = useStore()
  const [query, setQuery] = useState('')
  const [picked, setPicked] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [mapped, setMapped] = useState(false)
  const [error, setError] = useState('')

  const cand = store.candidate(candidateId)
  const existing = store.activeApplicationFor(candidateId)
  const candidateDeployed = cand?.status === 'deployed' || store.applicationsFor(candidateId).some((application) => application.status === 'deployed')

  const orders = useMemo(() => {
    const q = query.trim().toLowerCase()
    return store.orders
      .filter((o) => o.status === 'open')
      .filter((o) => {
        if (!q) return true
        const des = store.designation(o.designationId)?.title ?? ''
        const cl = store.company(o.companyId)?.name ?? ''
        return (
          des.toLowerCase().includes(q) ||
          cl.toLowerCase().includes(q) ||
          o.ref.toLowerCase().includes(q) ||
          o.country.toLowerCase().includes(q)
        )
      })
  }, [store.orders, store.companies, store.designations, query])

  if (!cand) return null
  const close = () => { if (!saving) onClose() }
  const save = async () => {
    if (saving || (!mapped && (!picked || existing || candidateDeployed))) return
    setSaving(true); setError('')
    try {
      if (mapped) {
        await store.refresh({ keepCurrent: true })
      } else {
        const result = await store.mapCandidate(candidateId, picked!)
        if (!store.isCurrentSession() || !result) return
        setMapped(true)
        if (!result.refreshed) {
          setError('The candidate is mapped. Refresh the workspace to see the application; retrying will not map them again.')
          return
        }
      }
      if (store.isCurrentSession()) onClose()
    } catch (cause) {
      if (store.isCurrentSession()) setError(cause instanceof Error ? cause.message : 'The candidate could not be mapped. Please retry.')
    } finally { setSaving(false) }
  }

  return (
    <Dialog
      open={open}
      onClose={close}
      width="lg"
      title={`Map ${cand.name} to an order`}
      description="Mapping creates the application and raises the stage 1 prescreening task."
      footer={
        <>
          <Button variant="ghost" disabled={saving} onClick={close}>
            {mapped ? 'Close' : 'Cancel'}
          </Button>
          <Button
            variant="primary"
            disabled={saving || (!mapped && (!picked || !!existing || candidateDeployed))}
            onClick={() => void save()}
          >
            {saving ? mapped ? 'Refreshing…' : 'Mapping…' : mapped ? 'Refresh mapped candidate' : 'Map and start stage 1'}
          </Button>
        </>
      }
    >
      {existing && !mapped && (
        <p className="mb-3 flex items-start gap-2 rounded-control border border-amber-100 bg-amber-50 px-3 py-2.5 text-xs text-amber-700">
          <AlertTriangle className="mt-px h-3.5 w-3.5 shrink-0" />
          This candidate is already on {store.order(existing.orderId)?.ref}. Unmap them from that
          order first — the open tasks are cancelled and a fresh application starts at stage 1.
        </p>
      )}
      {candidateDeployed && <p role="status" className="mb-3 text-sm text-amber-700">Deployed candidates cannot be mapped to another order.</p>}
      {error && <p role="alert" className="mb-3 rounded-control bg-stamp-50 p-3 text-sm text-stamp-700">{error}</p>}

      <fieldset disabled={saving || mapped} className="min-w-0">
      <SearchInput
        value={query}
        onChange={setQuery}
        placeholder="Designation, client, country, or order reference"
      />

      <ul className="scrollbar-thin mt-3 max-h-[22rem] divide-y divide-rule-soft overflow-y-auto rounded-control border border-rule-soft">
        {orders.map((o) => {
          const des = store.designation(o.designationId)
          const matched = store.applications.filter((a) => a.orderId === o.id && a.status === 'active').length
          const deployed = store.applications.filter((a) => a.orderId === o.id && a.status === 'deployed').length
          const fits = cand.designations.includes(des?.title ?? '')
          return (
            <li key={o.id}>
              <button
                onClick={() => setPicked(o.id)}
                disabled={!!existing || candidateDeployed || saving || mapped}
                className={cn(
                  'flex w-full items-center gap-3 px-3 py-2.5 text-left transition-colors',
                  picked === o.id ? 'bg-navy-50' : 'hover:bg-field/70',
                  existing && 'cursor-not-allowed opacity-50',
                )}
              >
                <span className="min-w-0 flex-1">
                  <span className="flex items-center gap-2">
                    <span className="truncate text-sm font-medium text-ink">{des?.title}</span>
                    {fits && <Badge tone="navy">Matches profile</Badge>}
                  </span>
                  <span className="mt-0.5 block truncate text-micro text-ink-400">
                    {store.company(o.companyId)?.name} · {o.city}, {o.country} · closes{' '}
                    {dateLabel(o.deadline)}
                  </span>
                </span>
                <span className="shrink-0 text-right text-micro text-ink-500 tnum">
                  <span className="block">{o.ref}</span>
                  <span className="block">
                    {deployed}/{o.openings} filled · {matched} in process
                  </span>
                </span>
              </button>
            </li>
          )
        })}
        {orders.length === 0 && (
          <EmptyState title="No open orders match" description="Try a different search, or create an order under a designation." />
        )}
      </ul>
      </fieldset>
    </Dialog>
  )
}
