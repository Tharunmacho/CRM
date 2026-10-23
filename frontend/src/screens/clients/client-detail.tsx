import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { ArrowLeft, ChevronRight, History, Pencil, Plus, Trash2 } from 'lucide-react'
import { dateLabel, money } from '@/shared/lib/format'
import { useStore } from '@/shared/state/crm-store'
import { stageByNo } from '@/shared/workflow/stages'
import {
  Badge,
  Def,
  DefList,
  EmptyState,
  PageHeader,
  Panel,
  PanelHeader,
  StageChip,
  StatusPill,
} from '@/shared/ui/display'
import { Button } from '@/shared/ui/primitives'
import { StageModal } from '@/features/workflow/components/stage-modal'
import { DeleteRecordDialog } from '@/shared/components/delete-dialog'
import { ClientDialog } from '@/features/clients/components/client-dialog'
import { CompanyDialog } from '@/features/companies/components/company-dialog'
import { RecordAudit } from '@/features/master-data/components/record-audit'
import { useWorkspaceReady } from '@/features/master-data/use-workspace-ready'

export default function ClientDetailPage() {
  const { id } = useParams()
  const store = useStore()
  const snapshot = useWorkspaceReady(id ? `client:${id}` : null)
  const navigate = useNavigate()
  const client = store.clients.find((c) => c.id === id)
  const [openApp, setOpenApp] = useState<string | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [editing, setEditing] = useState(false)
  const [history, setHistory] = useState(false)
  const [addingCompany, setAddingCompany] = useState(false)

  if (!snapshot.ready) return <Panel><p role={snapshot.error ? 'alert' : 'status'} className="text-sm text-ink-600">{snapshot.error || 'Loading the client relationship…'}</p>{snapshot.error && <Button className="mt-3" onClick={snapshot.retry}>Retry client</Button>}</Panel>
  if (!client) {
    return (
      <EmptyState
        title="That client does not exist"
        action={<Button onClick={() => navigate('/clients')}>Back to Clients</Button>}
      />
    )
  }

  const companies = store.companies.filter((co) => co.clientId === client.id)
  const companyIds = companies.map((co) => co.id)
  const orders = store.orders.filter((o) => companyIds.includes(o.companyId))

  return (
    <>
      <Link to="/clients" className="mb-3 inline-flex items-center gap-1.5 text-xs text-ink-500 hover:text-ink">
        <ArrowLeft className="h-3.5 w-3.5" />
        Clients
      </Link>

      <PageHeader
        title={client.name}
        description={[client.city, client.country].filter(Boolean).join(', ') || 'No location recorded'}
        actions={
          <>
            <StatusPill status={client.status} />
            <Button icon={<History className="h-4 w-4" />} onClick={() => setHistory(true)}>Activity</Button>
            <Button icon={<Pencil className="h-4 w-4" />} onClick={() => setEditing(true)}>Edit client</Button>
            {store.can('delete_records') && (
              <Button variant="dangerGhost" icon={<Trash2 className="h-4 w-4" />} onClick={() => setDeleting(true)}>
                Delete client
              </Button>
            )}
          </>
        }
      />

      <Panel className="mb-4">
        <DefList cols={3}>
          <Def label="Email">{client.email ?? '—'}</Def>
          <Def label="Phone" ident>{client.phone ?? '—'}</Def>
          <Def label="WhatsApp" ident>{client.whatsapp ?? '—'}</Def>
          <Def label="Companies">
            <span className="tnum">{companies.length}</span>
          </Def>
          <Def label="Open orders">
            <span className="tnum">{orders.filter((o) => o.status === 'open').length}</span>
          </Def>
          <Def label="Deployed so far">
            <span className="tnum">
              {store.applications.filter(
                (a) => a.status === 'deployed' && orders.some((o) => o.id === a.orderId),
              ).length}
            </span>
          </Def>
        </DefList>
        {client.notes && (
          <p className="mt-4 border-t border-rule-soft pt-3 text-sm leading-relaxed text-ink-600">
            {client.notes}
          </p>
        )}
      </Panel>

      <Panel className="mb-4" flush>
        <PanelHeader title="Employers & hiring" meta="Choose an employer to view its recruitment orders. Each employer keeps its own contacts and agreement."
          action={<Button icon={<Plus className="h-4 w-4" />} disabled={client.status !== 'active'} onClick={() => setAddingCompany(true)}>Add employer</Button>} />
        {companies.length === 0 && <EmptyState title="Add the first employer" description={client.status === 'active' ? 'Create the employer company, then add a recruitment order with the role, openings and terms.' : 'This client is inactive. Reactivate the client before adding an employer.'} action={client.status === 'active' ? <Button variant="primary" onClick={() => setAddingCompany(true)}>Add employer</Button> : undefined} />}
        <div className="divide-y divide-rule-soft">
          {companies.map((company) => <div key={company.id} className="px-4 py-4">
            <div className="mb-4 flex flex-wrap items-center justify-between gap-3"><div><p className="text-base font-semibold text-ink">{company.name}</p><p className="mt-1 text-sm text-ink-500">{[company.city, company.country].filter(Boolean).join(', ') || 'Location not recorded'}</p></div><div className="flex flex-wrap items-center gap-2"><StatusPill status={company.status} /><Button onClick={() => navigate(`/companies?client=${encodeURIComponent(client.id)}`)}>Company details</Button><Button variant="primary" onClick={() => navigate(`/jobs?company=${encodeURIComponent(company.id)}`)}>View hiring orders</Button></div></div>
            <DefList cols={3}>
              <Def label="Contact person">{company.contactPerson || '—'}</Def>
              <Def label="Phone" ident>{company.phone || '—'}</Def>
              <Def label="WhatsApp" ident>{company.whatsapp || '—'}</Def>
            </DefList>
          </div>)}
        </div>
      </Panel>

      {orders.length === 0 && companies.length > 0 ? (
        <Panel>
          <EmptyState
            title="No orders for this client yet"
            description="Create an order under a designation in Jobs / Positions."
            action={<Button variant="primary" onClick={() => navigate('/jobs')}>Go to Jobs</Button>}
          />
        </Panel>
      ) : orders.length > 0 ? (
        <div className="space-y-4">
          {orders.map((o) => {
            const apps = store.applications.filter((a) => a.orderId === o.id)
            const deployed = apps.filter((a) => a.status === 'deployed').length
            return (
              <Panel key={o.id} flush>
                <PanelHeader
                  title={`${store.designation(o.designationId)?.title ?? 'Order'} · ${store.company(o.companyId)?.name ?? ''}`}
                  meta={[o.ref, [o.city, o.country].filter(Boolean).join(', '), money(o.salary, o.currency), o.deadline ? `closes ${dateLabel(o.deadline)}` : ''].filter(Boolean).join(' · ')}
                  action={
                    <>
                      <span className="text-micro text-ink-500 tnum">
                        {deployed}/{o.openings} deployed
                      </span>
                      <StatusPill status={o.status} />
                      <Button size="sm" variant="ghost" onClick={() => navigate(`/orders/${o.id}`)} icon={<ChevronRight className="h-3.5 w-3.5" />}>
                        Open
                      </Button>
                    </>
                  }
                />
                {apps.length === 0 ? (
                  <p className="px-4 py-4 text-sm text-ink-400">No candidates matched to this order yet.</p>
                ) : (
                  <ul>
                    {apps.map((a) => {
                      const cand = store.candidate(a.candidateId)
                      if (!cand) return null
                      return (
                        <li key={a.id}>
                          <button
                            onClick={() => setOpenApp(a.id)}
                            className="flex w-full items-center gap-3 border-b border-rule-soft px-4 py-2.5 text-left transition-colors last:border-0 hover:bg-field/70"
                          >
                            <StageChip
                              no={a.currentStage}
                              state={a.status === 'closed' ? 'closed' : a.status === 'deployed' ? 'done' : 'current'}
                            />
                            <div className="min-w-0 flex-1">
                              <p className="truncate text-sm font-medium text-ink">{cand.name}</p>
                              <p className="truncate text-micro text-ink-400">
                                {stageByNo(a.currentStage).title} · {store.user(cand.ownerId)?.name}
                              </p>
                            </div>
                            <StatusPill status={a.status} />
                          </button>
                        </li>
                      )
                    })}
                  </ul>
                )}
              </Panel>
            )
          })}
        </div>
      ) : null}

      <StageModal applicationId={openApp} open={!!openApp} onClose={() => setOpenApp(null)} />
      {editing && <ClientDialog client={client} onClose={() => setEditing(false)} />}
      {addingCompany && <CompanyDialog clientId={client.id} onClose={() => setAddingCompany(false)} />}
      {history && <RecordAudit kind="client" id={client.id} onClose={() => setHistory(false)} />}
      <DeleteRecordDialog
        kind="client"
        id={client.id}
        open={deleting}
        onClose={() => setDeleting(false)}
        onDeleted={() => navigate('/clients')}
        onEdit={() => setEditing(true)}
      />
    </>
  )
}
