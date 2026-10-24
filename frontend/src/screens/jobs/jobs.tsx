import { useEffect, useMemo, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { BriefcaseBusiness, Download, FilterX, History, Layers, Pencil, Plus, Search, Target, Trash2, UserCheck, Users } from 'lucide-react'
import { cn } from '@/shared/lib/cn'
import { money } from '@/shared/lib/format'
import { useStore } from '@/shared/state/crm-store'
import type { Order } from '@/shared/model/types'
import { Badge, EmptyState, PageHeader, Panel, PanelHeader } from '@/shared/ui/display'
import { Button, SearchInput, Select } from '@/shared/ui/primitives'
import { SummaryCards } from '@/shared/ui/workspace'
import { DataTable, type Column } from '@/shared/ui/table'
import { DeleteRecordDialog } from '@/shared/components/delete-dialog'
import { DesignationDialog, OrderDialog, RemoveDesignationDialog } from '@/features/jobs/components/job-dialogs'
import { OrderCard } from '@/features/jobs/components/order-card'
import { Avatar, Place } from '@/features/jobs/components/identity'
import { DueChip, LayoutToggle, OrderRowActions, OrderStatusText, useElementWidth, useMinWidth, useStoredLayout, type OrderActionHandlers } from '@/features/jobs/components/order-parts'
import { isOverdue, orderStatusKey, toCsv } from '@/features/jobs/order-meta'
import { useOrderActions } from '@/features/jobs/use-order-actions'
import { isDeployed, isMatched } from '@/features/jobs/order-counts'
import { RecordAudit } from '@/features/master-data/components/record-audit'
import { useWorkspaceReady } from '@/features/master-data/use-workspace-ready'
import { SnapshotState } from '@/shared/ui/page-loading'
import '@/styles/jobs.css'

type StatusKey = 'all' | 'open' | 'in_progress' | 'on_hold' | 'filled' | 'closed' | 'overdue'
type View = 'orders' | 'designations'

const ALL_STATUSES: [StatusKey, string][] = [['all', 'All orders'], ['open', 'Open'], ['in_progress', 'In progress'], ['on_hold', 'On hold'], ['filled', 'Filled'], ['closed', 'Closed'], ['overdue', 'Overdue']]
const DESIGNATION_STATUSES: [StatusKey, string][] = [['all', 'All'], ['open', 'Open'], ['on_hold', 'On hold'], ['filled', 'Filled'], ['closed', 'Closed'], ['overdue', 'Overdue']]
const CARD_PAGE = 12
const STATUS_HINT: Partial<Record<StatusKey, string>> = {
  open: 'Orders that are still looking for candidates',
  in_progress: 'Open orders that have candidates in the stages',
  on_hold: 'Matching is paused',
  filled: 'Every opening is deployed',
  closed: 'Work on the order is finished',
  overdue: 'Open orders that are past their deadline',
}

/**
 * Two levels, as the PRD describes: a designation is the job title, and an order is a
 * specific opening for one client under it. The landing view is the designation catalogue
 * with its orders; orders are created inside a designation. "All orders" is a cross-designation
 * overview for tracking, with the same quick actions as each designation's own list.
 */
export default function JobsPage() {
  const store = useStore()
  const snapshot = useWorkspaceReady('jobs')
  const navigate = useNavigate()
  const actions = useOrderActions()
  const [params, setParams] = useSearchParams()
  const companyId = params.get('company') || ''
  const requestedView = params.get('view')
  const view: View = requestedView === 'orders' || (!requestedView && companyId) ? 'orders' : 'designations'
  const scopedCompany = store.company(companyId)
  const setParam = (key: string, value: string) => setParams((previous) => { const next = new URLSearchParams(previous); value ? next.set(key, value) : next.delete(key); return next })
  const eligibleCompanies = store.companies.filter((company) => company.status === 'active' && store.client(company.clientId)?.status === 'active')
  const canCreateOrder = eligibleCompanies.length > 0
  const [layout, setLayout] = useStoredLayout('crm-jobs-layout', typeof window !== 'undefined' && window.innerWidth < 700 ? 'grid' : 'list')
  const [host, setHost] = useState<HTMLElement | null>(null)
  const tableWidth = useElementWidth(host)
  const wideScreen = useMinWidth(1280)
  const cardsOnly = !wideScreen || (tableWidth > 0 && tableWidth < (view === 'orders' ? 940 : 700))
  const listMode = layout === 'list' && !cardsOnly
  const [query, setQuery] = useState('')
  const [railQuery, setRailQuery] = useState('')
  const [status, setStatus] = useState<StatusKey>('all')
  const [designationStatus, setDesignationStatus] = useState<StatusKey>('all')
  const [country, setCountry] = useState('')
  const [designationFilter, setDesignationFilter] = useState('')
  const [cardLimit, setCardLimit] = useState(CARD_PAGE)
  const [activeId, setActiveId] = useState(params.get('designation') || store.designations[0]?.id || '')
  const [addingDesignation, setAddingDesignation] = useState(false)
  const [addingOrder, setAddingOrder] = useState(false)
  const [editing, setEditing] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [history, setHistory] = useState(false)
  const [editingOrder, setEditingOrder] = useState<Order | null>(null)
  const [deletingOrder, setDeletingOrder] = useState<Order | null>(null)
  useEffect(() => {
    if (!store.designations.some((designation) => designation.id === activeId)) setActiveId(store.designations[0]?.id ?? '')
  }, [store.designations, activeId])
  useEffect(() => { setCardLimit(CARD_PAGE) }, [status, country, designationFilter, query, companyId, layout])

  const active = store.designations.find((d) => d.id === activeId)
  const scopedOrders = useMemo(() => store.orders.filter((o) => !companyId || o.companyId === companyId), [store.orders, companyId])
  const activeCount = (order: Order) => store.applications.filter((a) => a.orderId === order.id && isMatched(a)).length
  const deployedCount = (order: Order) => store.applications.filter((a) => a.orderId === order.id && isDeployed(a)).length
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
    const q = railQuery.trim().toLowerCase()
    return store.designations.filter((d) => !q || d.title.toLowerCase().includes(q) || (d.category ?? '').toLowerCase().includes(q))
  }, [store.designations, railQuery])
  const designationOrders = scopedOrders.filter((o) => o.designationId === activeId)
  const visibleDesignationOrders = designationOrders.filter((o) => matchesStatus(o, designationStatus))
  const designationApps = useMemo(() => {
    const ids = new Set(designationOrders.map((o) => o.id))
    return store.applications.filter((a) => ids.has(a.orderId))
  }, [designationOrders, store.applications])

  if (!snapshot.ready) return <SnapshotState error={snapshot.error} onRetry={snapshot.retry} retryLabel="Try again" label="Loading orders" />

  const openSeats = (list: Order[]) => list.filter((o) => o.status === 'open').reduce((total, o) => total + o.openings, 0)
  const inScope = (appOrderId: string) => scopedOrders.some((o) => o.id === appOrderId)
  const inProcessTotal = store.applications.filter((a) => isMatched(a) && inScope(a.orderId)).length
  const deployedTotal = store.applications.filter((a) => isDeployed(a) && inScope(a.orderId)).length
  const filtersActive = status !== 'all' || !!country || !!designationFilter || !!query.trim()
  const resetFilters = () => { setStatus('all'); setCountry(''); setDesignationFilter(''); setQuery('') }
  const showOrders = (key: StatusKey) => { setStatus(key); setParam('view', 'orders') }
  const toggleStatusCard = (key: StatusKey) => (view === 'orders' && status === key ? setStatus('all') : showOrders(key))

  const handlers: OrderActionHandlers = {
    onOpen: (order) => navigate(`/orders/${order.id}`),
    onEdit: (order) => setEditingOrder(order),
    onSetStatus: (order, next) => void actions.setStatus(order, next),
    onDelete: store.can('delete_records') ? (order) => setDeletingOrder(order) : undefined,
  }

  const exportCsv = () => {
    const csv = toCsv(
      ['Order ref', 'Job title', 'Company', 'Country', 'City', 'Status', 'Openings', 'Deployed', 'In progress', 'Salary', 'Currency', 'Contract period', 'Interview date', 'Deadline'],
      rows.map((o) => [o.ref, store.designation(o.designationId)?.title, store.company(o.companyId)?.name, o.country, o.city, orderStatusKey(o), o.openings, deployedCount(o), activeCount(o), o.salary, o.currency, o.contractPeriod, o.interviewDate, o.deadline]),
    )
    const url = URL.createObjectURL(new Blob(['﻿', csv], { type: 'text/csv;charset=utf-8' }))
    const link = document.createElement('a')
    link.href = url; link.download = `orders-${new Date().toISOString().slice(0, 10)}.csv`
    document.body.appendChild(link); link.click(); link.remove()
    window.setTimeout(() => URL.revokeObjectURL(url), 1000)
  }

  // Every column is a real column inside a fixed-layout table whose widths add up to 100%, so the table
  // fits its card at 1280px and wider with no inner horizontal scroll. Narrower windows get order cards.
  const jobCol: Column<Order> = {
    key: 'job',
    header: 'Job title',
    sortable: true,
    width: '20%',
    value: (o) => store.designation(o.designationId)?.title ?? '',
    render: (o) => {
      const title = store.designation(o.designationId)?.title ?? 'Unknown job title'
      return (
        <div className="jv-cell">
          <p className="jv-cell-title" title={title}>{title}</p>
          <p className="jv-cell-sub ident" title={o.ref}>{o.ref}</p>
        </div>
      )
    },
  }
  const clientCol = (width: string): Column<Order> => ({
    key: 'client',
    header: 'Company',
    sortable: true,
    width,
    value: (o) => store.company(o.companyId)?.name ?? '',
    render: (o) => {
      const name = store.company(o.companyId)?.name ?? 'Unknown company'
      return (
        <div className="jv-person">
          <Avatar name={name} />
          <div className="jv-cell">
            <p className="jv-cell-title" title={name}>{name}</p>
            <p className="jv-cell-sub" title={[o.city, o.country].filter(Boolean).join(', ')}><Place city={o.city} country={o.country} /></p>
          </div>
        </div>
      )
    },
  })
  const statusCol = (width: string): Column<Order> => ({ key: 'status', header: 'Status', sortable: true, width, value: (o) => orderStatusKey(o), render: (o) => <OrderStatusText order={o} /> })
  const salaryCol = (width: string): Column<Order> => ({
    key: 'salary',
    header: 'Salary',
    sortable: true,
    width,
    value: (o) => o.salary,
    render: (o) => o.salary ? <span className="tnum jv-money" title={`${money(o.salary, o.currency)} per month`}>{money(o.salary, o.currency)}</span> : <span className="text-ink-400">Not set</span>,
  })
  const filledCol = (width: string): Column<Order> => ({
    key: 'filled',
    header: 'Openings',
    sortable: true,
    width,
    value: (o) => deployedCount(o),
    render: (o) => {
      const done = deployedCount(o)
      const inProcess = activeCount(o)
      return (
        <div className="jv-openings" title={`${done} deployed of ${o.openings} · ${inProcess} matched`}>
          <p className="tnum"><strong>{done}</strong><span> / {o.openings}</span>{inProcess > 0 && <span className="jv-active-note"> · {inProcess} matched</span>}</p>
          <div className="jv-meter"><span style={{ width: `${Math.min(100, o.openings ? (done / o.openings) * 100 : 0)}%` }} /></div>
        </div>
      )
    },
  })
  const deadlineCol = (width: string): Column<Order> => ({
    key: 'deadline',
    header: 'Deadline',
    sortable: true,
    width,
    value: (o) => o.deadline,
    render: (o) => <DueChip order={o} short />,
  })
  const actionsCol = (width: string): Column<Order> => ({
    key: 'actions',
    header: '',
    width,
    align: 'right',
    render: (o) => <OrderRowActions order={o} handlers={handlers} name={`order ${o.ref}`} compact />,
  })
  const allOrderColumns = [jobCol, clientCol('22%'), statusCol('10%'), filledCol('14%'), salaryCol('11%'), deadlineCol('14%'), actionsCol('9%')]
  const designationOrderColumns = [clientCol('26%'), statusCol('13%'), filledCol('17%'), salaryCol('14%'), deadlineCol('17%'), actionsCol('13%')]

  const statusChips = (items: [StatusKey, string][], value: StatusKey, onChange: (key: StatusKey) => void, label: string) => (
    <div className="jv-chips-row" role="group" aria-label={label}>
      {items.map(([key, text]) => <button key={key} type="button" title={STATUS_HINT[key]} className={cn('jv-chip', value === key && 'is-on')} aria-pressed={value === key} onClick={() => onChange(key)}>{text}</button>)}
    </div>
  )

  return (
    <div className="jv">
      <PageHeader
        title="Jobs / Positions"
        description="Each job title holds the orders from clients and the candidates matched to them."
        actions={<Button variant="primary" icon={<Plus className="h-4 w-4" />} onClick={() => setAddingDesignation(true)}>New job title</Button>}
      />

      <SummaryCards
        items={[
          { label: 'Job titles', value: store.designations.length, detail: 'In your list', icon: Layers },
          { label: 'Open orders', value: scopedOrders.filter((o) => o.status === 'open').length, detail: `${scopedOrders.length} orders in total`, icon: BriefcaseBusiness, onSelect: () => toggleStatusCard('open'), active: view === 'orders' && status === 'open' },
          { label: 'Openings left', value: openSeats(scopedOrders), detail: 'Jobs still to fill', icon: Target },
          { label: 'In progress', value: inProcessTotal, detail: 'Candidates in the stages', icon: Users, onSelect: () => toggleStatusCard('in_progress'), active: view === 'orders' && status === 'in_progress' },
          { label: 'Deployed', value: deployedTotal, detail: 'Placed with clients', icon: UserCheck },
        ]}
      />

      <div className="jv-views" role="tablist" aria-label="Jobs view">
        {([['designations', 'Job titles & orders'], ['orders', 'All orders']] as const).map(([key, label]) => (
          <button key={key} type="button" role="tab" aria-selected={view === key} className={cn(view === key && 'is-active')} onClick={() => setParam('view', key)}>{label}</button>
        ))}
      </div>

      {companyId && (
        <div className="jv-scope">
          <span>Showing orders for <strong>{scopedCompany?.name ?? 'a company we could not find'}</strong></span>
          <Button size="sm" onClick={() => setParam('company', '')}>Show all companies</Button>
        </div>
      )}

      {view === 'orders' ? (
        <Panel flush className="jv-orders">
          <div ref={setHost} className="jv-measure" aria-hidden="true" />
          <PanelHeader
            title="All orders"
            meta="Orders from every client, for every job title. Open one to match candidates."
            action={
              <>
                <SearchInput value={query} onChange={setQuery} placeholder="Search job title, client or skills" className="jv-search" />
                {!cardsOnly && <LayoutToggle value={layout} onChange={setLayout} />}
                <Button icon={<Download className="h-4 w-4" />} disabled={rows.length === 0} onClick={exportCsv}>Download CSV</Button>
              </>
            }
          />
          <div className="jv-filters">
            <div className="jv-filters-head"><span className="jv-filters-label">Filters</span>{statusChips(ALL_STATUSES, status, setStatus, 'Order status')}</div>
            <div className="jv-selects">
              <Select aria-label="Filter by company" value={companyId} onChange={(event) => setParam('company', event.target.value)}>
                <option value="">All companies</option>
                {companyId && !scopedCompany && <option value={companyId}>Company not found</option>}
                {store.companies.map((company) => <option key={company.id} value={company.id}>{company.name}{company.status !== 'active' ? ' (inactive)' : ''}</option>)}
              </Select>
              <Select aria-label="Filter by country" value={country} onChange={(event) => setCountry(event.target.value)}>
                <option value="">All countries</option>
                {countries.map((value) => <option key={value} value={value}>{value}</option>)}
              </Select>
              <Select aria-label="Filter by job title" value={designationFilter} onChange={(event) => setDesignationFilter(event.target.value)}>
                <option value="">All job titles</option>
                {store.designations.map((item) => <option key={item.id} value={item.id}>{item.title}</option>)}
              </Select>
            </div>
          </div>
          {rows.length === 0 ? (
            <EmptyState
              icon={scopedOrders.length === 0 ? <BriefcaseBusiness size={30} strokeWidth={1.4} /> : <Search size={30} strokeWidth={1.4} />}
              title={scopedOrders.length === 0 ? 'No orders yet' : 'No orders found'}
              description={scopedOrders.length === 0 ? 'Orders are created inside a job title. Open a job title and choose New order.' : 'Try a different status, company or search word. Or clear the filters to see all orders.'}
              action={scopedOrders.length === 0
                ? <Button variant="primary" onClick={() => setParam('view', 'designations')}>Go to job titles</Button>
                : filtersActive ? <Button icon={<FilterX className="h-4 w-4" />} onClick={resetFilters}>Clear filters</Button> : undefined}
            />
          ) : listMode ? (
            <DataTable
              rows={rows}
              columns={allOrderColumns}
              rowKey={(o) => o.id}
              onRowClick={(o) => navigate(`/orders/${o.id}`)}
              pageSize={15}
              resetPageKey={`${status}:${country}:${designationFilter}:${query}:${companyId}`}
            />
          ) : (
            <>
              <div className="jv-order-grid">
                {rows.slice(0, cardLimit).map((order) => <OrderCard key={order.id} order={order} handlers={handlers} showDesignation />)}
              </div>
              {rows.length > cardLimit && <div className="jv-more"><Button size="sm" onClick={() => setCardLimit((value) => value + CARD_PAGE)}>Show more orders</Button></div>}
            </>
          )}
        </Panel>
      ) : (
        <div className="jv-layout">
          <div className="jv-rail-wrap">
            <Panel flush className="jv-rail">
              <PanelHeader title="Job titles" meta={`${store.designations.length} in your list`} action={<Button icon={<Plus className="h-4 w-4" />} onClick={() => setAddingDesignation(true)}>New</Button>} />
              <div className="jv-rail-search">
                <SearchInput value={railQuery} onChange={setRailQuery} placeholder="Find a job title" />
              </div>
              <ul className="scrollbar-thin jv-rail-list">
                {designations.map((d) => {
                  const count = scopedOrders.filter((o) => o.designationId === d.id && o.status === 'open').length
                  return (
                    <li key={d.id}>
                      <button type="button" onClick={() => setActiveId(d.id)} aria-pressed={d.id === activeId} className={cn('jv-option', d.id === activeId && 'is-active')}>
                        <span className="jv-option-icon"><BriefcaseBusiness size={17} strokeWidth={1.6} /></span>
                        <span className="jv-option-copy">
                          <span className="jv-option-title" title={d.title}>{d.title}</span>
                          <span className="jv-option-sub" title={d.category || "General"}>{d.category || 'General'}</span>
                        </span>
                        {count > 0 && <span className="jv-option-open"><i aria-hidden="true" />{count} open</span>}
                      </button>
                    </li>
                  )
                })}
                {designations.length === 0 && (
                  <li><EmptyState title={store.designations.length ? 'No job titles found' : 'No job titles yet'} description={store.designations.length ? 'Try a different search.' : 'Add the first job title. Then create orders for it.'} /></li>
                )}
              </ul>
            </Panel>
          </div>

          <div className="jv-main">
            {active ? (
              <>
                <Panel flush className="jv-detail">
                  <div className="jv-detail-head">
                    <span className="jv-icon-tile" aria-hidden="true"><BriefcaseBusiness size={22} strokeWidth={1.6} /></span>
                    <div className="jv-detail-title">
                      <p className="jv-eyebrow">{active.category || 'General'}</p>
                      <h2>{active.title}</h2>
                      <p>{active.minExperience > 0 ? `Minimum ${active.minExperience} ${active.minExperience === 1 ? 'year' : 'years'} of experience` : 'Any experience'}</p>
                    </div>
                    <div className="jv-detail-actions">
                      <Button icon={<Pencil className="h-4 w-4" />} onClick={() => setEditing(true)}>Edit</Button>
                      <Button icon={<History className="h-4 w-4" />} onClick={() => setHistory(true)}>History</Button>
                      {store.can('delete_records') && <Button className="jv-danger" icon={<Trash2 className="h-4 w-4" />} onClick={() => setDeleting(true)}>Delete</Button>}
                    </div>
                  </div>
                  <dl className="jv-stat-row">
                    <div><dt>Open orders</dt><dd className="tnum">{designationOrders.filter((o) => o.status === 'open').length}</dd></div>
                    <div><dt>Openings left</dt><dd className="tnum">{openSeats(designationOrders)}</dd></div>
                    <div><dt>In progress</dt><dd className="tnum">{designationApps.filter(isMatched).length}</dd></div>
                    <div><dt>Deployed</dt><dd className="tnum">{designationApps.filter(isDeployed).length}</dd></div>
                  </dl>
                  <dl className="jv-facts">
                    <div><dt>Description</dt><dd>{active.description || <span className="text-ink-400">No description added</span>}</dd></div>
                    <div><dt>Required skills</dt><dd>{active.requiredSkills.length ? <span className="flex flex-wrap gap-1.5">{active.requiredSkills.map((s) => <Badge key={s} tone="neutral" className="jv-skill">{s}</Badge>)}</span> : <span className="text-ink-400">None added</span>}</dd></div>
                  </dl>
                </Panel>

                <Panel flush className="jv-orders-panel">
                  <div ref={setHost} className="jv-measure" aria-hidden="true" />
                  <PanelHeader
                    title="Orders for this job title"
                    meta={`Each order is one client's opening for ${active.title}`}
                    action={
                      <>
                        {!cardsOnly && <LayoutToggle value={layout} onChange={setLayout} />}
                        <Button variant="primary" disabled={!canCreateOrder} icon={<Plus className="h-4 w-4" />} onClick={() => setAddingOrder(true)}>New order</Button>
                      </>
                    }
                  />
                  {!canCreateOrder && <div className="jv-notice"><p>You need an active client and company before you can create an order.</p><Button size="sm" onClick={() => navigate('/clients')}>Manage clients</Button></div>}
                  {designationOrders.length > 0 && (
                    <div className="jv-filters is-compact">
                      {statusChips(DESIGNATION_STATUSES, designationStatus, setDesignationStatus, 'Order status')}
                    </div>
                  )}
                  {designationOrders.length === 0 ? (
                    <EmptyState
                      icon={<BriefcaseBusiness size={30} strokeWidth={1.4} />}
                      title={`No ${active.title} orders yet`}
                      description={scopedCompany ? `No ${active.title} orders for ${scopedCompany.name}.` : 'An order is one client\'s opening: how many people, where, salary, contract, interview date and deadline.'}
                      action={canCreateOrder ? <Button variant="primary" icon={<Plus className="h-4 w-4" />} onClick={() => setAddingOrder(true)}>New order</Button> : undefined}
                    />
                  ) : visibleDesignationOrders.length === 0 ? (
                    <EmptyState icon={<Search size={30} strokeWidth={1.4} />} title="No orders in this status" description="Pick another status to see the other orders for this job title." action={<Button onClick={() => setDesignationStatus('all')}>Show all orders</Button>} />
                  ) : listMode ? (
                    <DataTable
                      rows={visibleDesignationOrders}
                      columns={designationOrderColumns}
                      rowKey={(o) => o.id}
                      onRowClick={(o) => navigate(`/orders/${o.id}`)}
                      pageSize={8}
                      resetPageKey={`${activeId}:${designationStatus}`}
                    />
                  ) : (
                    <>
                      <div className="jv-order-grid">
                        {visibleDesignationOrders.slice(0, cardLimit).map((order) => <OrderCard key={order.id} order={order} handlers={handlers} />)}
                      </div>
                      {visibleDesignationOrders.length > cardLimit && <div className="jv-more"><Button size="sm" onClick={() => setCardLimit((value) => value + CARD_PAGE)}>Show more orders</Button></div>}
                    </>
                  )}
                </Panel>
              </>
            ) : (
              <Panel>
                <EmptyState icon={<BriefcaseBusiness size={30} strokeWidth={1.4} />} title={store.designations.length ? 'Pick a job title' : 'Add your first job title'} description="A job title sets the skills and experience used to match candidates. Orders from clients are created inside it." action={store.designations.length === 0 ? <Button variant="primary" onClick={() => setAddingDesignation(true)}>New job title</Button> : undefined} />
              </Panel>
            )}
          </div>
        </div>
      )}

      {(addingDesignation || (editing && active)) && <DesignationDialog designation={editing ? active : undefined} onClose={() => { setAddingDesignation(false); setEditing(false) }} onSaved={(id) => { setActiveId(id); setParam('view', 'designations') }} />}
      {addingOrder && active && <OrderDialog onClose={() => setAddingOrder(false)} designation={active} companyId={companyId || undefined} onSaved={(id) => navigate(`/orders/${id}`)} />}
      {editingOrder && <OrderDialog order={editingOrder} onClose={() => setEditingOrder(null)} />}
      {active && deleting && <RemoveDesignationDialog designation={active} onClose={() => setDeleting(false)} onDeleted={() => setActiveId('')} />}
      {active && history && <RecordAudit kind="designation" id={active.id} onClose={() => setHistory(false)} />}
      {deletingOrder && (
        <DeleteRecordDialog
          kind="order"
          id={deletingOrder.id}
          open
          onClose={() => setDeletingOrder(null)}
          onEdit={() => { const target = deletingOrder; setDeletingOrder(null); setEditingOrder(target) }}
        />
      )}
    </div>
  )
}
