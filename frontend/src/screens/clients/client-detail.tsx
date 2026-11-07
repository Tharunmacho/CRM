import { useMemo, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { ApplicationFormButton } from '@/features/application-form/components/application-form-button'
import { ArrowLeft, History, Pencil, Trash2, UserRound } from 'lucide-react'
import { ago, dateLabel, money } from '@/shared/lib/format'
import { useStore } from '@/shared/state/crm-store'
import { EmptyState, Panel, StatusPill } from '@/shared/ui/display'
import { Button, Select } from '@/shared/ui/primitives'
import { SnapshotState } from '@/shared/ui/page-loading'
import { StageModal } from '@/features/workflow/components/stage-modal'
import { DeleteRecordDialog } from '@/shared/components/delete-dialog'
import { ClientDialog } from '@/features/clients/components/client-dialog'
import { CompanyDialog } from '@/features/companies/components/company-dialog'
import { OrderDialog } from '@/features/jobs/components/job-dialogs'
import { RecordAudit } from '@/features/master-data/components/record-audit'
import { useWorkspaceReady } from '@/features/master-data/use-workspace-ready'
import { orderCounts } from '@/features/jobs/order-counts'
import { AgreementPill, Avatar, Flag, Place, Progress, StageMini } from '@/features/clients/components/parts'
import '@/styles/clients.css'

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
  const [orderFor, setOrderFor] = useState<string | null>(null)
  const [employer, setEmployer] = useState('')

  const companies = useMemo(() => store.companies.filter((co) => co.clientId === id), [store.companies, id])
  const orders = useMemo(() => {
    const ids = new Set(companies.map((co) => co.id))
    const rank = (status: string) => (status === 'open' ? 0 : status === 'on_hold' ? 1 : 2)
    return store.orders
      .filter((order) => ids.has(order.companyId))
      .sort((a, b) => rank(a.status) - rank(b.status) || b.createdAt.localeCompare(a.createdAt))
  }, [store.orders, companies])
  const appsByOrder = useMemo(() => {
    const grouped = new Map<string, typeof store.applications>()
    for (const application of store.applications) {
      const list = grouped.get(application.orderId)
      if (list) list.push(application)
      else grouped.set(application.orderId, [application])
    }
    return grouped
  }, [store.applications])

  if (!snapshot.ready) return <SnapshotState variant="detail" error={snapshot.error} onRetry={snapshot.retry} retryLabel="Try again" label="Loading client" />
  if (!client) {
    return (
      <EmptyState
        title="We could not find this client"
        action={<Button onClick={() => navigate('/clients')}>Back to Clients</Button>}
      />
    )
  }

  const shownOrders = employer ? orders.filter((order) => order.companyId === employer) : orders
  const primary = companies[0]
  const title = primary ? (companies.length > 1 ? `${primary.name} +${companies.length - 1}` : primary.name) : client.name
  const location = [client.city, client.country].filter(Boolean).join(', ')
  const canWork = client.status === 'active'
  const filedCount = companies.filter((company) => company.agreement).length

  return (
    <div className="cd-page">
      <Link to="/clients" className="cd-back">
        <ArrowLeft className="h-3.5 w-3.5" aria-hidden="true" />
        Clients
      </Link>

      <header className="cd-head">
        <Avatar name={title} size="xl" />
        <div className="cd-head-copy">
          <h1>{title}<StatusPill status={client.status} /></h1>
          <p className="cd-sub">
            <span><UserRound size={14} aria-hidden="true" />{client.name}</span>
            {location && <span><Flag country={client.country} />{location}</span>}
            {companies.length > 0 && <span><AgreementPill filed={filedCount} total={companies.length} short /></span>}
          </p>
        </div>
        <div className="cd-head-actions">
          <Button icon={<History className="h-4 w-4" />} onClick={() => setHistory(true)}>History</Button>
          <Button icon={<Pencil className="h-4 w-4" />} onClick={() => setEditing(true)}>Edit client</Button>
          {store.can('delete_records') && (
            <Button variant="dangerGhost" icon={<Trash2 className="h-4 w-4" />} onClick={() => setDeleting(true)}>
              Delete
            </Button>
          )}
        </div>
      </header>

      <section aria-labelledby="cd-orders-title">
        <div className="cd-orders-head">
          <div><h2 id="cd-orders-title">Jobs &amp; candidates</h2><p>All jobs for this client and the candidates matched to them.</p></div>
          {companies.length > 1 && (
            <Select aria-label="Filter jobs by company" value={employer} onChange={(event) => setEmployer(event.target.value)}>
              <option value="">All companies</option>
              {companies.map((company) => <option key={company.id} value={company.id}>{company.name}</option>)}
            </Select>
          )}
        </div>

        {shownOrders.length === 0 ? (
          <Panel>
            <EmptyState
              title={companies.length ? 'No jobs for this client yet' : 'Add a company first'}
              description={companies.length ? 'Create a job for one of the companies: the job title, openings and terms.' : 'Jobs are created for a company. Add a company first.'}
              action={companies.length ? <Button variant="primary" disabled={!canWork} onClick={() => setOrderFor(employer || companies.find((co) => co.status === 'active')?.id || companies[0].id)}>New job</Button> : <Button variant="primary" disabled={!canWork} onClick={() => setAddingCompany(true)}>Add company</Button>}
            />
          </Panel>
        ) : (
          <div className="cd-order-list">
            {shownOrders.map((order) => {
              const apps = appsByOrder.get(order.id) ?? []
              const { matched, selected, deployed: placed } = orderCounts(apps)
              const designation = store.designation(order.designationId)
              const where = store.company(order.companyId)
              return (
                <Panel key={order.id} flush>
                  <header className="cd-order-head">
                    <div className="min-w-0">
                      <h3><button type="button" onClick={() => navigate(`/orders/${order.id}`)}>{designation?.title ?? 'Job'}</button><span>{order.ref}</span></h3>
                      <p className="cd-order-meta">
                        {where && <span>{where.name}</span>}
                        {(order.city || order.country) && <Place country={order.country} city={order.city} />}
                        {order.salary ? <span>{money(order.salary, order.currency)}</span> : null}
                        {order.deadline ? <span>Closes {dateLabel(order.deadline)}</span> : null}
                      </p>
                    </div>
                    <div className="cd-order-side">
                      <StatusPill status={order.status} />
                      <Button size="sm" onClick={() => navigate(`/orders/${order.id}`)}>Open job</Button>
                    </div>
                  </header>
                  <div className="cd-progress">
                    <div><span>Openings</span><strong className="tnum">{order.openings}</strong></div>
                    <div><span>Matched</span><strong className="tnum">{matched}</strong></div>
                    <div title="Candidates past the interview stage"><span>Selected</span><strong className="tnum">{selected}</strong></div>
                    <div><span>Deployed</span><strong className="tnum">{placed}</strong></div>
                    <Progress done={placed} total={order.openings} label={`${placed} of ${order.openings} openings deployed`} />
                  </div>
                  {apps.length === 0 ? (
                    <div className="cd-no-candidates">
                      <p>No candidates matched to this job yet.</p>
                      <Button size="sm" onClick={() => navigate(`/orders/${order.id}`)}>Match candidates</Button>
                    </div>
                  ) : (
                    <table className="cd-cands">
                      <colgroup><col style={{ width: '25%' }} /><col style={{ width: '26%' }} /><col style={{ width: '13%' }} /><col style={{ width: '11%' }} /><col style={{ width: '10%' }} /><col style={{ width: '15%' }} /></colgroup>
                      <thead>
                        <tr><th><span>Candidate</span></th><th><span>Stage</span></th><th><span>Recruiter</span></th><th><span>Updated</span></th><th><span>Status</span></th><th>Actions</th></tr>
                      </thead>
                      <tbody>
                        {apps.map((application) => {
                          const candidate = store.candidate(application.candidateId)
                          if (!candidate) return null
                          const owner = store.user(candidate.ownerId ?? application.ownerId)
                          const state = application.status === 'closed' ? 'closed' : application.status === 'deployed' ? 'deployed' : 'live'
                          return (
                            <tr key={application.id} className={application.status === 'closed' ? 'is-closed' : undefined} onClick={() => setOpenApp(application.id)}>
                              <td className="c-name">
                                <button type="button" className="cd-cand" onClick={(event) => { event.stopPropagation(); setOpenApp(application.id) }} aria-label={`Open stages for ${candidate.name}`}>
                                  <Avatar name={candidate.name} size="sm" />
                                  <span className="cd-cand-text"><b title={candidate.name}>{candidate.name}</b><small title={candidate.phone || candidate.email || undefined}>{candidate.phone || candidate.email || 'No contact added'}</small></span>
                                </button>
                              </td>
                              <td className="c-stage"><StageMini no={application.currentStage} state={state} /></td>
                              <td className="c-owner">{owner ? <span className="cd-owner"><Avatar name={owner.name} size="sm" /><span title={owner.name}>{owner.name.split(' ')[0]}</span></span> : <span className="cd-muted">Not assigned</span>}</td>
                              <td className="c-updated cd-muted">{ago(application.updatedAt)}</td>
                              <td className="c-status"><StatusPill status={application.status} /></td>
                              <td className="c-profile"><span className="cd-profile-actions"><ApplicationFormButton size="sm" iconOnly candidateId={candidate.id} orderId={application.orderId} /><Link to={`/candidates/${candidate.id}`} className="cd-profile" aria-label={`Open profile of ${candidate.name}`} onClick={(event) => event.stopPropagation()}>Profile</Link></span></td>
                            </tr>
                          )
                        })}
                      </tbody>
                    </table>
                  )}
                </Panel>
              )
            })}
          </div>
        )}
      </section>

      <StageModal applicationId={openApp} open={!!openApp} onClose={() => setOpenApp(null)} />
      {editing && <ClientDialog client={client} onClose={() => setEditing(false)} />}
      {addingCompany && <CompanyDialog clientId={client.id} onClose={() => setAddingCompany(false)} />}
      {orderFor && <OrderDialog companyId={orderFor} onClose={() => setOrderFor(null)} />}
      {history && <RecordAudit kind="client" id={client.id} onClose={() => setHistory(false)} />}
      <DeleteRecordDialog
        kind="client"
        id={client.id}
        open={deleting}
        onClose={() => setDeleting(false)}
        onDeleted={() => navigate('/clients')}
        onEdit={() => setEditing(true)}
      />
    </div>
  )
}
