import { useMemo, useState } from 'react'
import { AlertTriangle } from 'lucide-react'
import { dateLabel } from '@/shared/lib/format'
import { useStore } from '@/shared/state/crm-store'
import { Dialog } from '@/shared/ui/overlay'
import { Button, SearchInput } from '@/shared/ui/primitives'
import { Badge, EmptyState, StatusPill } from '@/shared/ui/display'
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

  const cand = store.candidate(candidateId)
  const existing = store.activeApplicationFor(candidateId)

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
  }, [store.orders, query])

  if (!cand) return null

  return (
    <Dialog
      open={open}
      onClose={onClose}
      width="lg"
      title={`Map ${cand.name} to an order`}
      description="Mapping creates the application and raises the stage 1 prescreening task."
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant="primary"
            disabled={!picked || !!existing}
            onClick={() => {
              if (!picked) return
              void store.mapCandidate(candidateId, picked)
              setPicked(null)
              onClose()
            }}
          >
            Map and start stage 1
          </Button>
        </>
      }
    >
      {existing && (
        <p className="mb-3 flex items-start gap-2 rounded-control border border-amber-100 bg-amber-50 px-3 py-2.5 text-xs text-amber-700">
          <AlertTriangle className="mt-px h-3.5 w-3.5 shrink-0" />
          This candidate is already on {store.order(existing.orderId)?.ref}. Unmap them from that
          order first — the open tasks are cancelled and a fresh application starts at stage 1.
        </p>
      )}

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
                disabled={!!existing}
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
    </Dialog>
  )
}
