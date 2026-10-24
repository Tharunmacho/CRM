import { useMemo, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import {
  ArrowLeft, BriefcaseBusiness, Building2, History, Mail, MapPin, MessageCircle,
  Pencil, Phone, Plus, Target, Trash2, UserCheck, UserRound, Users,
} from 'lucide-react'
import { ago, dateLabel, money } from '@/shared/lib/format'
import { useStore } from '@/shared/state/crm-store'
import { EmptyState, Panel, PanelHeader, StatusPill } from '@/shared/ui/display'
import { KpiStrip } from '@/shared/ui/workspace'
import { Button, Select } from '@/shared/ui/primitives'
import { FileLink } from '@/shared/ui/file-drop'
import { SnapshotState } from '@/shared/ui/page-loading'
import { StageModal } from '@/features/workflow/components/stage-modal'
import { DeleteRecordDialog } from '@/shared/components/delete-dialog'
import { ClientDialog } from '@/features/clients/components/client-dialog'
import { CompanyDialog } from '@/features/companies/components/company-dialog'
import { OrderDialog } from '@/features/jobs/components/job-dialogs'
import { RecordAudit } from '@/features/master-data/components/record-audit'
import { useWorkspaceReady } from '@/features/master-data/use-workspace-ready'
import { orderCounts } from '@/features/jobs/order-counts'
import { AgreementPill, Avatar, CopyButton, Flag, Place, Progress, StageMini } from '@/features/clients/components/parts'
import type { Company } from '@/shared/model/types'
import '@/styles/clients.css'

const digits = (value: string) => value.replace(/[^\d]/g, '')

function Detail({ label, children, className }: { label: string; children: React.ReactNode; className?: string }) {
  return <div className={className ? `cd-detail ${className}` : 'cd-detail'}><dt>{label}</dt><dd>{children}</dd></div>
}

const empty = <span className="cd-none">Not added</span>

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
  const [editingCompany, setEditingCompany] = useState<Company | null>(null)
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

  const placedOn = (orderId: string) => orderCounts(appsByOrder.get(orderId) ?? []).deployed
  const openOrders = orders.filter((order) => order.status === 'open')
  const openSeats = openOrders.reduce((total, order) => total + Math.max(order.openings - placedOn(order.id), 0), 0)
  const allApps = orders.flatMap((order) => appsByOrder.get(order.id) ?? [])
  const inProcess = orderCounts(allApps).matched
  const deployed = orderCounts(allApps).deployed
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

      <KpiStrip
        items={[
          { label: 'Companies', value: companies.length, detail: `${companies.filter((co) => co.status === 'active').length} active`, icon: Building2 },
          { label: 'Open orders', value: openOrders.length, detail: `${orders.length} orders in total`, icon: BriefcaseBusiness, tone: 'amber' },
          { label: 'Openings left', value: openSeats, detail: 'Jobs still to fill', icon: Target },
          { label: 'In progress', value: inProcess, detail: 'Candidates in the stages', icon: Users },
          { label: 'Deployed', value: deployed, detail: 'Placed with this client', icon: UserCheck, tone: 'green' },
        ]}
      />

      <div className="cd-layout">
        <aside>
          <Panel>
            <div className="cd-person">
              <Avatar name={client.name} size="lg" />
              <div><p className="cd-person-name">{client.name}</p><p className="cd-person-role">Contact person</p></div>
            </div>
            <ul className="cd-contact">
              <li><Mail size={16} aria-hidden="true" />{client.email ? <><a href={`mailto:${client.email}`}>{client.email}</a><CopyButton value={client.email} label="Copy email address" /></> : empty}</li>
              <li><Phone size={16} aria-hidden="true" />{client.phone ? <><a href={`tel:${client.phone}`} className="ident">{client.phone}</a><CopyButton value={client.phone} label="Copy phone number" /></> : empty}</li>
              <li><MessageCircle size={16} aria-hidden="true" />{client.whatsapp ? <a href={`https://wa.me/${digits(client.whatsapp)}`} target="_blank" rel="noreferrer" className="ident" title="Open WhatsApp">{client.whatsapp}</a> : empty}</li>
              <li><MapPin size={16} aria-hidden="true" />{location ? <Place country={client.country} city={client.city} /> : empty}</li>
            </ul>
            {companies.length > 0 && (
              <div className="cd-block">
                <h3>Agreements</h3>
                <ul className="cd-agreements">
                  {companies.map((company) => (
                    <li key={company.id}>
                      <b>{company.name}</b>
                      {company.agreement
                        ? <FileLink url={company.agreement.url} className="cd-file">{company.agreement.name}</FileLink>
                        : <button type="button" className="cd-link" onClick={() => setEditingCompany(company)}>No agreement yet · Upload</button>}
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {client.notes && <div className="cd-block"><h3>Notes</h3><p className="cd-notes">{client.notes}</p></div>}
          </Panel>
        </aside>

        <section>
          <Panel flush>
            <PanelHeader title="Companies" meta="Contact, address, agreement and orders"
              action={<Button icon={<Plus className="h-4 w-4" />} disabled={!canWork} onClick={() => setAddingCompany(true)}>Add company</Button>} />
            {companies.length === 0 && <EmptyState title="Add the first company" description={canWork ? 'Add the company this client hires for. Then create an order with the job title, number of openings and terms.' : 'This client is inactive. Make the client active before adding a company.'} action={canWork ? <Button variant="primary" onClick={() => setAddingCompany(true)}>Add company</Button> : undefined} />}
            <div className="cd-employers">
              {companies.map((company) => {
                const own = orders.filter((order) => order.companyId === company.id)
                const open = own.filter((order) => order.status === 'open')
                const openings = open.reduce((total, order) => total + order.openings, 0)
                const placed = open.reduce((total, order) => total + Math.min(placedOn(order.id), order.openings), 0)
                return (
                  <article key={company.id} className="cd-employer">
                    <header>
                      <div className="cd-employer-title">
                        <h3>{company.name}</h3>
                        <p>
                          {company.industry && <span>{company.industry}</span>}
                          {(company.city || company.country) ? <Place country={company.country} city={company.city} /> : !company.industry && <span>Industry and place not added</span>}
                        </p>
                      </div>
                      <div className="cd-employer-badges">
                        <AgreementPill filed={company.agreement ? 1 : 0} total={1} short />
                        <StatusPill status={company.status} />
                      </div>
                    </header>
                    <dl className="cd-details">
                      <Detail label="Contact person">{company.contactPerson || empty}</Detail>
                      <Detail label="Contact email">{company.contactEmail ? <a href={`mailto:${company.contactEmail}`}>{company.contactEmail}</a> : empty}</Detail>
                      <Detail label="Phone">{company.phone ? <a href={`tel:${company.phone}`} className="ident">{company.phone}</a> : empty}</Detail>
                      <Detail label="WhatsApp">{company.whatsapp ? <a href={`https://wa.me/${digits(company.whatsapp)}`} target="_blank" rel="noreferrer" className="ident">{company.whatsapp}</a> : empty}</Detail>
                      <Detail label="Agreement" className="cd-span2">
                        {company.agreement
                          ? <FileLink url={company.agreement.url} className="cd-file">{company.agreement.name}</FileLink>
                          : <button type="button" className="cd-link" onClick={() => setEditingCompany(company)}>No agreement yet · Upload</button>}
                      </Detail>
                      <Detail label="Address" className="cd-wide">{company.address || empty}</Detail>
                    </dl>
                    {company.notes && <p className="cd-employer-notes">{company.notes}</p>}
                    <footer>
                      <div className="cd-employer-stats">
                        <span><b className="tnum">{open.length}</b> open {open.length === 1 ? 'order' : 'orders'}{open.length > 0 && <> · <b className="tnum">{placed}</b> of <b className="tnum">{openings}</b> openings deployed</>}</span>
                        {open.length > 0 && <Progress done={placed} total={openings} />}
                      </div>
                      <div className="cd-employer-actions">
                        <Button size="sm" icon={<Pencil className="h-3.5 w-3.5" />} onClick={() => setEditingCompany(company)}>Edit</Button>
                        <Button size="sm" onClick={() => navigate(`/jobs?view=orders&company=${encodeURIComponent(company.id)}`)}>View orders</Button>
                        <Button size="sm" variant="primary" icon={<Plus className="h-3.5 w-3.5" />} disabled={!canWork || company.status !== 'active'} onClick={() => setOrderFor(company.id)}>New order</Button>
                      </div>
                    </footer>
                  </article>
                )
              })}
            </div>
          </Panel>
        </section>
      </div>

      <section aria-labelledby="cd-orders-title">
        <div className="cd-orders-head">
          <div><h2 id="cd-orders-title">Orders &amp; candidates</h2><p>All orders for this client and the candidates matched to them.</p></div>
          {companies.length > 1 && (
            <Select aria-label="Filter orders by company" value={employer} onChange={(event) => setEmployer(event.target.value)}>
              <option value="">All companies</option>
              {companies.map((company) => <option key={company.id} value={company.id}>{company.name}</option>)}
            </Select>
          )}
        </div>

        {shownOrders.length === 0 ? (
          <Panel>
            <EmptyState
              title={companies.length ? 'No orders for this client yet' : 'Add a company first'}
              description={companies.length ? 'Create an order for one of the companies: the job title, openings and terms.' : 'Orders are created for a company. Add a company first.'}
              action={companies.length ? <Button variant="primary" disabled={!canWork} onClick={() => setOrderFor(employer || companies.find((co) => co.status === 'active')?.id || companies[0].id)}>New order</Button> : <Button variant="primary" disabled={!canWork} onClick={() => setAddingCompany(true)}>Add company</Button>}
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
                      <h3><button type="button" onClick={() => navigate(`/orders/${order.id}`)}>{designation?.title ?? 'Order'}</button><span>{order.ref}</span></h3>
                      <p className="cd-order-meta">
                        {where && <span>{where.name}</span>}
                        {(order.city || order.country) && <Place country={order.country} city={order.city} />}
                        {order.salary ? <span>{money(order.salary, order.currency)}</span> : null}
                        {order.deadline ? <span>Closes {dateLabel(order.deadline)}</span> : null}
                      </p>
                    </div>
                    <div className="cd-order-side">
                      <StatusPill status={order.status} />
                      <Button size="sm" onClick={() => navigate(`/orders/${order.id}`)}>Open order</Button>
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
                      <p>No candidates matched to this order yet.</p>
                      <Button size="sm" onClick={() => navigate(`/orders/${order.id}`)}>Match candidates</Button>
                    </div>
                  ) : (
                    <table className="cd-cands">
                      <colgroup><col style={{ width: '27%' }} /><col style={{ width: '31%' }} /><col style={{ width: '14%' }} /><col style={{ width: '9%' }} /><col style={{ width: '10%' }} /><col style={{ width: '9%' }} /></colgroup>
                      <thead>
                        <tr><th><span>Candidate</span></th><th><span>Stage</span></th><th><span>Recruiter</span></th><th><span>Updated</span></th><th><span>Status</span></th><th aria-label="Profile" /></tr>
                      </thead>
                      <tbody>
                        {apps.map((application) => {
                          const candidate = store.candidate(application.candidateId)
                          if (!candidate) return null
                          const owner = store.user(application.ownerId ?? candidate.ownerId)
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
                              <td className="c-profile"><Link to={`/candidates/${candidate.id}`} className="cd-profile" aria-label={`Open profile of ${candidate.name}`} onClick={(event) => event.stopPropagation()}>Profile</Link></td>
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
      {editingCompany && <CompanyDialog company={editingCompany} onClose={() => setEditingCompany(null)} />}
      {orderFor && <OrderDialog companyId={orderFor} onClose={() => setOrderFor(null)} onSaved={(orderId) => navigate(`/orders/${orderId}`)} />}
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
