import { useMemo, useState } from 'react'
import { AlertTriangle, PhoneCall } from 'lucide-react'
import { dateLabel } from '@/shared/lib/format'
import { useStore } from '@/shared/state/crm-store'
import { Dialog } from '@/shared/ui/overlay'
import { Button, SearchInput } from '@/shared/ui/primitives'
import { Badge, EmptyState } from '@/shared/ui/display'
import { cn } from '@/shared/lib/cn'
import { useWorkspaceReady } from '@/features/master-data/use-workspace-ready'
import { orderProfileEvidence } from '@/features/jobs/order-profile-evidence'
import { openInitialCallTask } from '../initial-call'
import { CallDialog } from './call-dialog'

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
  const snapshot = useWorkspaceReady(open ? `map:${candidateId}` : null)
  const [query, setQuery] = useState('')
  const [picked, setPicked] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [mapped, setMapped] = useState(false)
  const [error, setError] = useState('')
  const [calling, setCalling] = useState(false)

  const cand = store.candidate(candidateId)
  const owner = cand?.ownerId ? store.user(cand.ownerId) : undefined
  const existing = store.activeApplicationFor(candidateId)
  const firstCallOpen = !!openInitialCallTask(store.tasks, candidateId)
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
          (o.country ?? '').toLowerCase().includes(q)
        )
      })
  }, [store.orders, store.companies, store.clients, store.designations, query])

  if (!snapshot.ready || !cand) return <Dialog open={open} onClose={onClose} title="Match candidate to an order"><p role={snapshot.error ? 'alert' : 'status'} className="text-sm text-ink-600">{snapshot.error || (snapshot.ready ? 'This candidate is no longer available.' : 'Loading orders…')}</p>{(snapshot.error || snapshot.ready) && <Button className="mt-4" onClick={snapshot.retry}>Try again</Button>}</Dialog>
  const eligible = owner?.active && !existing && !candidateDeployed
  const selectedOrder = orders.find(order => order.id === picked)
  const close = () => { if (!saving) onClose() }
  const save = async () => {
    if (saving || (!mapped && (!selectedOrder || !eligible))) return
    setSaving(true); setError('')
    try {
      if (mapped) {
        await store.refresh({ keepCurrent: true })
      } else {
        const result = await store.mapCandidate(candidateId, picked!)
        if (!store.isCurrentSession() || !result) return
        setMapped(true)
        if (!result.refreshed) {
          setError('The candidate is matched. Refresh to see it. You do not need to match again.')
          return
        }
      }
      if (store.isCurrentSession()) onClose()
    } catch (cause) {
      if (store.isCurrentSession()) setError(cause instanceof Error ? cause.message : 'We could not match the candidate. Please try again.')
    } finally { setSaving(false) }
  }

  return (
    <>
    <Dialog
      open={open && !calling}
      onClose={close}
      width="lg"
      title={`Match ${cand.name} to an order`}
      description="Matching starts stage 1 (prescreening) and creates the prescreening task."
      footer={
        <>
          <Button variant="ghost" disabled={saving} onClick={close}>
            {mapped ? 'Close' : 'Cancel'}
          </Button>
          <Button
            variant="primary"
            disabled={saving || (!mapped && (!selectedOrder || !eligible))}
            onClick={() => void save()}
          >
            {saving ? mapped ? 'Refreshing…' : 'Matching…' : mapped ? 'Refresh' : firstCallOpen ? 'Match anyway' : 'Match and start stage 1'}
          </Button>
        </>
      }
    >
      {existing && !mapped && (
        <p className="mb-3 flex items-start gap-2 rounded-control border border-amber-100 bg-amber-50 px-3 py-2.5 text-xs text-amber-700">
          <AlertTriangle className="mt-px h-3.5 w-3.5 shrink-0" />
          This candidate is already on {store.order(existing.orderId)?.ref}. Remove them from that
          order first. Its open tasks will be cancelled, and the new order starts at stage 1.
        </p>
      )}
      {firstCallOpen && !mapped && !candidateDeployed && (
        <div role="note" className="mb-3 flex flex-wrap items-start gap-2 rounded-control border border-amber-100 bg-amber-50 px-3 py-2.5 text-xs text-amber-700">
          <AlertTriangle className="mt-px h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          <span className="min-w-0 flex-1">This candidate&apos;s first call has not been recorded. Matching will close the first-call task. Record the call first if you spoke to them.</span>
          <Button size="sm" icon={<PhoneCall />} disabled={saving} onClick={() => setCalling(true)}>Record call first</Button>
        </div>
      )}
      {candidateDeployed && <p role="status" className="mb-3 text-sm text-amber-700">Deployed candidates cannot be matched to another order.</p>}
      {!owner?.active && <p role="status" className="mb-4 rounded-control bg-amber-50 p-4 text-sm text-amber-800">This candidate needs an active recruiter before you can match them to an order. Ask an admin or manager to assign one from the candidate profile or the Staff Directory.</p>}
      {error && <p role="alert" className="mb-3 rounded-control bg-stamp-50 p-3 text-sm text-stamp-700">{error}</p>}
      <div className="mb-4 rounded-panel border border-rule bg-field/50 p-4"><p className="text-sm font-semibold">Check the job requirements</p><p className="mt-1 text-sm text-ink-500">Use the saved skills and experience to decide. A matching job title alone does not mean the candidate is suitable. Matching starts prescreening and closes any initial call task that is still open.</p></div>

      <fieldset disabled={saving || mapped} className="min-w-0">
      <SearchInput
        value={query}
        onChange={(value) => { setQuery(value); setPicked(null) }}
        placeholder="Job title, employer, country or order number"
      />

      <ul className="scrollbar-thin mt-3 max-h-[22rem] divide-y divide-rule-soft overflow-y-auto rounded-control border border-rule-soft">
        {orders.map((o) => {
          const des = store.designation(o.designationId)
          const matched = store.applications.filter((a) => a.orderId === o.id && a.status === 'active').length
          const deployed = store.applications.filter((a) => a.orderId === o.id && a.status === 'deployed').length
          const evidence = des ? orderProfileEvidence(cand, des) : null
          return (
            <li key={o.id}>
              <button
                onClick={() => setPicked(o.id)}
                disabled={!eligible || saving || mapped || !des}
                aria-pressed={picked === o.id}
                className={cn(
                  'flex w-full flex-wrap items-start gap-3 px-4 py-4 text-left transition-colors',
                  picked === o.id ? 'bg-navy-50' : 'hover:bg-field/70',
                  existing && 'cursor-not-allowed opacity-50',
                )}
              >
                <span className="min-w-0 flex-1">
                  <span className="flex items-center gap-2">
                    <span className="truncate text-sm font-medium text-ink">{des?.title}</span>
                    {evidence?.designationMatches && <Badge tone="navy">Job title matches</Badge>}
                  </span>
                  {evidence && <span className="mt-3 block text-sm text-ink-600"><span className="block">Experience: {cand.totalExperience} years · {des!.minExperience} required{evidence.experienceMeets ? ' · Meets the requirement' : ' · Below the requirement'}</span><span className="mt-1 block">Skills saved: {evidence.requiredSkills.length ? `${evidence.matchedSkills.length} of ${evidence.requiredSkills.length} exact matches` : 'No skills required'}</span>{evidence.missingSkills.length > 0 && <span className="mt-1 block text-amber-700">Please check: {evidence.missingSkills.join(', ')}</span>}</span>}
                  <span className="mt-0.5 block truncate text-micro text-ink-400">
                    {[store.company(o.companyId)?.name, [o.city, o.country].filter(Boolean).join(', '), o.deadline ? `closes ${dateLabel(o.deadline)}` : ''].filter(Boolean).join(' · ')}
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
          <EmptyState title="No open orders match" description="Try a different search, or create an order on the Jobs page." />
        )}
      </ul>
      {selectedOrder && <p role="status" className="mt-4 rounded-control bg-navy-50 p-3 text-sm text-navy-800">Selected: {store.designation(selectedOrder.designationId)?.title} at {store.company(selectedOrder.companyId)?.name} · {selectedOrder.ref}</p>}
      </fieldset>
    </Dialog>
    <CallDialog open={open && calling} onClose={() => setCalling(false)} candidateId={candidateId} />
    </>
  )
}
