import { useEffect, useMemo, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { BriefcaseBusiness, ChevronRight, History, Pencil, Plus, Trash2 } from 'lucide-react'
import { cn } from '@/shared/lib/cn'
import { dateLabel, initials, money } from '@/shared/lib/format'
import { useStore } from '@/shared/state/crm-store'
import type { Order } from '@/shared/model/types'
import { Badge, EmptyState, PageHeader, Panel, PanelHeader } from '@/shared/ui/display'
import { Button, SearchInput, Select } from '@/shared/ui/primitives'
import { Tabs } from '@/shared/ui/overlay'
import { DesignationDialog, OrderDialog, RemoveDesignationDialog } from '@/features/jobs/components/job-dialogs'
import { RecordAudit } from '@/features/master-data/components/record-audit'
import { DataTable, type Column } from '@/shared/ui/table'
import { useWorkspaceReady } from '@/features/master-data/use-workspace-ready'
import '@/styles/jobs.css'

type StatusKey = 'all' | 'open' | 'in_progress' | 'on_hold' | 'filled' | 'closed' | 'overdue'
type View = 'orders' | 'designations'

const today = () => new Date().toISOString().slice(0, 10)
const isOverdue = (order: Order) => order.status === 'open' && !!order.deadline && order.deadline.slice(0, 10) < today()
const STATUS_TEXT: Record<string, string> = { open: 'Open', on_hold: 'On hold', filled: 'Filled', closed: 'Closed', overdue: 'Overdue' }

export default function JobsPage() {
  const store = useStore()
  const snapshot = useWorkspaceReady('jobs')
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()
  const companyId = params.get('company') || ''
  const view: View = params.get('view') === 'designations' ? 'designations' : 'orders'
  const scopedCompany = store.company(companyId)
  const setParam = (key: string, value: string) => setParams((previous) => { const next = new URLSearchParams(previous); value ? next.set(key, value) : next.delete(key); return next })
  const eligibleCompanies = store.companies.filter((company) => company.status === 'active' && store.client(company.clientId)?.status === 'active')
  const canCreateOrder = eligibleCompanies.length > 0 && (!companyId || eligibleCompanies.some((company) => company.id === companyId))
  const [query, setQuery] = useState('')
  const [status, setStatus] = useState<StatusKey>('all')
  const [country, setCountry] = useState('')
  const [designationFilter, setDesignationFilter] = useState('')
  const [activeId, setActiveId] = useState(store.designations[0]?.id ?? '')
  const [addingDesignation, setAddingDesignation] = useState(false)
  const [addingOrder, setAddingOrder] = useState(false)
  const [editing, setEditing] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [history, setHistory] = useState(false)
  useEffect(() => {
    if (!store.designations.some((designation) => designation.id === activeId)) setActiveId(store.designations[0]?.id ?? '')
  }, [store.designations, activeId])

  const active = store.designations.find((d) => d.id === activeId)
  const scopedOrders = useMemo(() => store.orders.filter((o) => !companyId || o.companyId === companyId), [store.orders, companyId])
  const activeCount = (order: Order) => store.applications.filter((a) => a.orderId === order.id && a.status === 'active').length
  const deployedCount = (order: Order) => store.applications.filter((a) => a.orderId === order.id && a.status === 'deployed').length
  const matchesStatus = (order: Order, key: StatusKey) => {
    switch (key) {
      case 'all': return true
      case 'open': return order.status === 'open'
      case 'in_progress': return order.status === 'open' && activeCount(order) > 0
      case 'overdue': return isOverdue(order)
      default: return order.status === key
    }
  }
  const countries = useMemo(() => [...new Set(scopedOrders.map((o) => o.country).filter((value): value is string => !!value))].sort(), [scopedOrders])

  const rows = useMemo(() => {
    const q = query.trim().toLowerCase()
    return scopedOrders.filter((order) => {
      if (!matchesStatus(order, status)) return false
      if (country && order.country !== country) return false
      if (designationFilter && order.designationId !== designationFilter) return false
      if (!q) return true
      const designation = store.designation(order.designationId)
      return [designation?.title, designation?.category, order.ref, store.company(order.companyId)?.name, order.country, order.city, ...(designation?.requiredSkills ?? [])]
        .some((value) => (value ?? '').toLowerCase().includes(q))
    })
  }, [scopedOrders, status, country, designationFilter, query, store.applications, store.designations, store.companies])

  const designations = useMemo(() => {
    const q = query.trim().toLowerCase()
    return store.designations.filter((d) => !q || d.title.toLowerCase().includes(q) || (d.category ?? '').toLowerCase().includes(q))
  }, [store.designations, query])
  const designationOrders = scopedOrders.filter((o) => o.designationId === activeId)

  if (!snapshot.ready) return <Panel><p role={snapshot.error ? 'alert' : 'status'} className="text-sm text-ink-600">{snapshot.error || 'Loading hiring orders…'}</p>{snapshot.error && <Button className="mt-3" onClick={snapshot.retry}>Retry hiring orders</Button>}</Panel>

  const tabCount = (key: StatusKey) => scopedOrders.filter((order) => matchesStatus(order, key)).length
  const statusOf = (order: Order) => isOverdue(order) ? 'overdue' : order.status
  const StatusLabel = ({ order }: { order: Order }) => { const key = statusOf(order); return <span className={cn('jobs-status', `is-${key}`)}><i aria-hidden />{STATUS_TEXT[key]}</span> }

  const orderColumns: Column<Order>[] = [
    {
      key: 'job',
      header: 'Job order',
      sortable: true,
      width: '32%',
      value: (o) => store.designation(o.designationId)?.title ?? '',
      render: (o) => {
        const designation = store.designation(o.designationId)
        const skills = designation?.requiredSkills ?? []
        return (
          <div className="min-w-[14rem]">
            <p className="jobs-title">{designation?.title ?? 'Unknown designation'}</p>
            <p className="jobs-sub" title={skills.join(', ')}>
              <span className="ident">{o.ref}</span>{skills.length > 0 && <> · {skills.slice(0, 3).join(' · ')}{skills.length > 3 ? ` +${skills.length - 3}` : ''}</>}
            </p>
          </div>
        )
      },
    },
    {
      key: 'client',
      header: 'Client',
      sortable: true,
      width: '18%',
      value: (o) => store.company(o.companyId)?.name ?? '',
      render: (o) => {
        const name = store.company(o.companyId)?.name ?? 'Unknown company'
        return (
          <div className="flex min-w-[10rem] items-center gap-2.5">
            <span className="jobs-avatar" aria-hidden>{initials(name)}</span>
            <div className="min-w-0">
              <p className="truncate font-medium text-ink">{name}</p>
              <p className="jobs-sub">{[o.city, o.country].filter(Boolean).join(', ') || 'Location not recorded'}</p>
            </div>
          </div>
        )
      },
    },
    { key: 'status', header: 'Status', sortable: true, width: '110px', className: 'whitespace-nowrap', value: (o) => statusOf(o), render: (o) => <StatusLabel order={o} /> },
    {
      key: 'salary',
      header: 'Salary',
      sortable: true,
      className: 'whitespace-nowrap',
      value: (o) => o.salary,
      render: (o) => o.salary ? <span className="tnum">{money(o.salary, o.currency)}</span> : <span className="text-ink-400">Not set</span>,
    },
    {
      key: 'experience',
      header: 'Experience',
      sortable: true,
      className: 'whitespace-nowrap',
      value: (o) => store.designation(o.designationId)?.minExperience ?? 0,
      render: (o) => { const years = store.designation(o.designationId)?.minExperience ?? 0; return <span className="tnum">{years > 0 ? `${years}+ yrs` : 'Any'}</span> },
    },
    {
      key: 'filled',
      header: 'Filled',
      sortable: true,
      width: '120px',
      value: (o) => deployedCount(o),
      render: (o) => {
        const done = deployedCount(o)
        const inProcess = activeCount(o)
        return (
          <div className="min-w-[6.5rem]" title={`${done} deployed of ${o.openings} · ${inProcess} in process`}>
            <p className="tnum text-[13px]"><strong className="font-semibold text-ink">{done}</strong><span className="text-ink-500"> / {o.openings}</span>{inProcess > 0 && <span className="text-ink-400"> · {inProcess} active</span>}</p>
            <div className="jobs-meter"><span style={{ width: `${Math.min(100, o.openings ? (done / o.openings) * 100 : 0)}%` }} /></div>
          </div>
        )
      },
    },
    {
      key: 'deadline',
      header: 'Closes',
      sortable: true,
      className: 'whitespace-nowrap',
      value: (o) => o.deadline,
      render: (o) => <span className={cn(isOverdue(o) ? 'font-medium text-stamp-700' : 'text-ink-500')}>{o.deadline ? dateLabel(o.deadline) : '—'}</span>,
    },
    {
      key: 'open',
      header: '',
      width: '48px',
      render: (o) => <ChevronRight className="ml-auto h-4 w-4 text-ink-300" aria-label={`Open order ${o.ref}`} />,
    },
  ]

  return (
    <div className="jobs-page">
      <PageHeader
        title="Jobs / Positions"
        description="Client requisitions and the candidate matching pipeline."
        actions={
          <>
            <Button variant="secondary" icon={<Plus className="h-4 w-4" />} onClick={() => setAddingDesignation(true)}>
              New designation
            </Button>
            <Button variant="primary" icon={<Plus className="h-4 w-4" />} disabled={!canCreateOrder || store.designations.length === 0} onClick={() => setAddingOrder(true)}>
              New order
            </Button>
          </>
        }
      />

      <div className="jobs-views" role="tablist" aria-label="Jobs view">
        {([['orders', 'Job orders'], ['designations', 'Designations']] as const).map(([key, label]) => (
          <button key={key} role="tab" aria-selected={view === key} className={cn(view === key && 'is-active')} onClick={() => setParam('view', key === 'orders' ? '' : key)}>{label}</button>
        ))}
      </div>

      {!canCreateOrder && <div className="jobs-notice"><p>An active employer and active client contact are needed to create a new order. Existing hiring history remains available.</p><Button size="sm" onClick={() => navigate('/companies')}>Manage employers</Button></div>}

      {view === 'orders' ? (
        <Panel flush className="jobs-orders">
          <div className="jobs-filterbar">
            <SearchInput value={query} onChange={setQuery} placeholder="Search by job title, client or required skills…" className="min-w-0 flex-1" />
            <Select aria-label="Filter hiring orders by employer" value={companyId} onChange={(event) => setParam('company', event.target.value)} className="w-full sm:w-52">
              <option value="">All clients</option>
              {companyId && !scopedCompany && <option value={companyId}>Employer unavailable</option>}
              {store.companies.map((company) => <option key={company.id} value={company.id}>{company.name}{company.status !== 'active' ? ' (inactive)' : ''}</option>)}
            </Select>
            <Select aria-label="Filter by country" value={country} onChange={(event) => setCountry(event.target.value)} className="w-full sm:w-44">
              <option value="">All countries</option>
              {countries.map((value) => <option key={value} value={value}>{value}</option>)}
            </Select>
            <Select aria-label="Filter by designation" value={designationFilter} onChange={(event) => setDesignationFilter(event.target.value)} className="w-full sm:w-48">
              <option value="">All designations</option>
              {store.designations.map((item) => <option key={item.id} value={item.id}>{item.title}</option>)}
            </Select>
          </div>
          <Tabs
            tabs={([['all', 'All orders'], ['open', 'Open'], ['in_progress', 'In progress'], ['on_hold', 'On hold'], ['filled', 'Filled'], ['closed', 'Closed'], ['overdue', 'Overdue']] as const).map(([key, label]) => ({ key, label, count: tabCount(key), tone: key === 'overdue' && tabCount(key) > 0 ? 'stamp' as const : undefined }))}
            active={status}
            onChange={(key) => setStatus(key as StatusKey)}
          />
          {rows.length === 0 ? (
            <EmptyState
              title={scopedOrders.length === 0 ? 'No job orders yet' : 'No orders match these filters'}
              description={scopedOrders.length === 0 ? 'An order records an employer’s openings, salary, country and deadline.' : 'Try another status, client or search.'}
              action={scopedOrders.length === 0 && canCreateOrder && store.designations.length > 0 ? <Button variant="primary" onClick={() => setAddingOrder(true)}>Create the first order</Button> : undefined}
            />
          ) : (
            <DataTable
              rows={rows}
              columns={orderColumns}
              rowKey={(o) => o.id}
              onRowClick={(o) => navigate(`/orders/${o.id}`)}
              pageSize={15}
              resetPageKey={`${status}:${country}:${designationFilter}:${query}:${companyId}`}
            />
          )}
        </Panel>
      ) : (
        <div className="workspace-split jobs-layout">
          <Panel flush className="designation-panel h-fit min-w-0">
            <PanelHeader title="Designations" meta={`${store.designations.length} job categories`} />
            <div className="border-b border-rule-soft p-3">
              <SearchInput value={query} onChange={setQuery} placeholder="Find a designation" />
            </div>
            <ul className="scrollbar-thin max-h-[32rem] overflow-y-auto">
              {designations.map((d) => {
                const count = scopedOrders.filter((o) => o.designationId === d.id && o.status === 'open').length
                return (
                  <li key={d.id}>
                    <button
                      onClick={() => setActiveId(d.id)}
                      aria-pressed={d.id === activeId}
                      className={cn('designation-option', 'flex w-full items-center gap-2 border-b border-rule-soft px-3 py-2.5 text-left transition-colors last:border-0', d.id === activeId ? 'bg-navy-50' : 'hover:bg-field/70')}
                    >
                      <span className="designation-icon"><BriefcaseBusiness size={17} strokeWidth={1.6} /></span>
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-sm font-medium text-ink">{d.title}</span>
                        <span className="block truncate text-micro text-ink-400">{d.category}</span>
                      </span>
                      {count > 0 && <span className="shrink-0 rounded-[3px] bg-field px-1 py-px text-micro text-ink-500 tnum">{count}</span>}
                      <ChevronRight className="h-3.5 w-3.5 shrink-0 text-ink-300" />
                    </button>
                  </li>
                )
              })}
              {designations.length === 0 && <EmptyState title="No designations match" description="Try another search." />}
            </ul>
          </Panel>

          <div className="min-w-0 space-y-4">
            {active ? (
              <>
                <Panel className="designation-detail">
                  <span className="workspace-eyebrow">Selected designation</span>
                  <h2 className="text-h2 font-semibold text-ink">{active.title}</h2>
                  <div className="mt-3 flex flex-wrap gap-2">
                    <Button size="sm" icon={<Pencil className="h-4 w-4" />} onClick={() => setEditing(true)}>Edit designation</Button>
                    <Button size="sm" icon={<History className="h-4 w-4" />} onClick={() => setHistory(true)}>Activity</Button>
                    {store.can('delete_records') && <Button size="sm" variant="dangerGhost" icon={<Trash2 className="h-4 w-4" />} onClick={() => setDeleting(true)}>Delete designation</Button>}
                  </div>
                  <p className="mt-1 max-w-[70ch] text-sm text-ink-500">{active.description}</p>
                  <div className="mt-3 flex flex-wrap items-center gap-1.5">
                    <Badge>{active.category}</Badge>
                    <Badge>Minimum {active.minExperience} years</Badge>
                    {active.requiredSkills.map((s) => <Badge key={s} tone="navy">{s}</Badge>)}
                  </div>
                </Panel>

                <Panel flush>
                  <PanelHeader
                    title="Orders"
                    meta={`${designationOrders.length} under this designation`}
                    action={<Button size="sm" variant="secondary" disabled={!canCreateOrder} icon={<Plus className="h-3.5 w-3.5" />} onClick={() => setAddingOrder(true)}>New order</Button>}
                  />
                  {designationOrders.length === 0 ? (
                    <EmptyState
                      title="No orders for this designation yet"
                      description={scopedCompany ? `No ${active.title} orders for ${scopedCompany.name}. Select another designation or create an order.` : 'An order records an employer’s openings, salary, country and deadline.'}
                      action={canCreateOrder ? <Button variant="primary" onClick={() => setAddingOrder(true)}>Create the first order</Button> : <Button onClick={() => navigate('/companies')}>Manage employers</Button>}
                    />
                  ) : (
                    <DataTable rows={designationOrders} columns={orderColumns} rowKey={(o) => o.id} onRowClick={(o) => navigate(`/orders/${o.id}`)} pageSize={10} />
                  )}
                </Panel>
              </>
            ) : (
              <Panel>
                <EmptyState title={store.designations.length ? 'Pick a designation' : 'Create your first designation'} description="A designation defines the job title, skills and experience used when matching candidates." action={store.designations.length === 0 ? <Button variant="primary" onClick={() => setAddingDesignation(true)}>New designation</Button> : undefined} />
              </Panel>
            )}
          </div>
        </div>
      )}

      {(addingDesignation || (editing && active)) && <DesignationDialog designation={editing ? active : undefined} onClose={() => { setAddingDesignation(false); setEditing(false) }} onSaved={(id) => { setActiveId(id); setParam('view', 'designations') }} />}
      {addingOrder && <OrderDialog onClose={() => setAddingOrder(false)} designation={view === 'designations' ? active : designationFilter ? store.designation(designationFilter) : undefined} companyId={companyId || undefined} onSaved={(id) => navigate(`/orders/${id}`)} />}
      {active && deleting && <RemoveDesignationDialog designation={active} onClose={() => setDeleting(false)} onDeleted={() => setActiveId('')} />}
      {active && history && <RecordAudit kind="designation" id={active.id} onClose={() => setHistory(false)} />}
    </div>
  )
}
