import { useMemo, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { ArrowRight, BriefcaseBusiness, Building2, Download, FileText, Globe2, History, MessageCircle, Pencil, Plus, Search, ShieldCheck, Trash2 } from 'lucide-react'
import { useStore } from '@/shared/state/crm-store'
import type { Company } from '@/shared/model/types'
import { DataTable, type Column } from '@/shared/ui/table'
import { EmptyState, PageHeader, Panel, StatusPill } from '@/shared/ui/display'
import { RecordIdentity, SummaryCards } from '@/shared/ui/workspace'
import { Button, IconButton, SearchInput, Select } from '@/shared/ui/primitives'
import { Tabs } from '@/shared/ui/overlay'
import { FileLink } from '@/shared/ui/file-drop'
import { CompanyDialog } from '@/features/companies/components/company-dialog'
import { CompanyDetail } from '@/features/companies/components/company-detail'
import { RecordAudit } from '@/features/master-data/components/record-audit'
import { DeleteRecordDialog } from '@/shared/components/delete-dialog'
import { useWorkspaceReady } from '@/features/master-data/use-workspace-ready'
import { SnapshotState } from '@/shared/ui/page-loading'
import {
  Avatar, CopyButton, Place, RowMenu, ViewToggle, downloadCsv, useElementWidth, useSavedView, type MenuEntry,
} from '@/features/clients/components/parts'
import '@/styles/clients.css'

const CARD_PAGE = 12
const digits = (value: string) => value.replace(/[^\d]/g, '')

/**
 * Companies are the employers orders are placed for. Each belongs to a client, so one
 * person can hold several businesses.
 */
export default function CompaniesPage() {
  const store = useStore()
  const snapshot = useWorkspaceReady('companies')
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()
  const [query, setQuery] = useState('')
  const [tab, setTab] = useState<'all' | 'active' | 'inactive'>('all')
  const [country, setCountry] = useState('')
  const [savedView, setView] = useSavedView('crm-companies-view')
  const [shown, setShown] = useState(CARD_PAGE)
  // Eleven columns need about 1100px; with less room the same records read as cards.
  const [panelRef, panelWidth] = useElementWidth()
  const narrow = panelWidth > 0 && panelWidth < 1100
  const view = narrow ? 'cards' : savedView
  const clientFilter = params.get('client') || 'all'
  const setClientFilter = (value: string) => { setShown(CARD_PAGE); setParams((previous) => { const next = new URLSearchParams(previous); value === 'all' ? next.delete('client') : next.set('client', value); return next }) }
  const canAdd = store.clients.some((client) => client.status === 'active')
  const [adding, setAdding] = useState(false)
  const [editing, setEditing] = useState<Company | null>(null)
  const [detailId, setDetailId] = useState<string | null>(null)
  const [history, setHistory] = useState<string | null>(null)
  const [deletingId, setDeletingId] = useState<string | null>(null)

  const openByCompany = useMemo(() => {
    const counts = new Map<string, number>()
    for (const order of store.orders) if (order.status === 'open') counts.set(order.companyId, (counts.get(order.companyId) ?? 0) + 1)
    return counts
  }, [store.orders])

  const countries = useMemo(() => [...new Set(store.companies.map((company) => company.country?.trim()).filter(Boolean) as string[])].sort(), [store.companies])

  const rows = useMemo(() => {
    const q = query.trim().toLowerCase()
    return store.companies.filter((c) => {
      if (clientFilter !== 'all' && c.clientId !== clientFilter) return false
      if (tab !== 'all' && c.status !== tab) return false
      if (country && c.country?.trim() !== country) return false
      if (!q) return true
      return (
        c.name.toLowerCase().includes(q) ||
        (c.industry ?? '').toLowerCase().includes(q) ||
        (c.city ?? '').toLowerCase().includes(q) ||
        (c.country ?? '').toLowerCase().includes(q) ||
        (c.contactPerson ?? '').toLowerCase().includes(q) ||
        (c.contactEmail ?? '').toLowerCase().includes(q) ||
        (store.client(c.clientId)?.name ?? '').toLowerCase().includes(q)
      )
    })
  }, [store.companies, store.clients, query, clientFilter, tab, country])

  if (!snapshot.ready) return <SnapshotState error={snapshot.error} onRetry={snapshot.retry} retryLabel="Try again" label="Loading companies" />

  const hasFilters = Boolean(query.trim() || clientFilter !== 'all' || tab !== 'all' || country)
  const clear = () => { setQuery(''); setTab('all'); setCountry(''); setShown(CARD_PAGE); setClientFilter('all') }
  const ordersOf = (company: Company) => `/jobs?view=orders&company=${encodeURIComponent(company.id)}`
  const clientName = (company: Company) => store.client(company.clientId)?.name ?? 'Client not found'
  const menuFor = (company: Company): MenuEntry[] => [
    { label: 'Open details', icon: <Building2 size={15} />, onSelect: () => setDetailId(company.id) },
    { label: 'View orders', icon: <BriefcaseBusiness size={15} />, onSelect: () => navigate(ordersOf(company)) },
    { label: 'History', icon: <History size={15} />, onSelect: () => setHistory(company.id) },
    ...(store.can('delete_records') ? [{ label: 'Delete company', icon: <Trash2 size={15} />, danger: true, onSelect: () => setDeletingId(company.id) }] : []),
  ]
  const agreementCell = (company: Company) => company.agreement
    ? <FileLink url={company.agreement.url} title={`${company.agreement.name} (opens in a new tab)`} className="no-underline hover:no-underline"><span className="cl-pill is-ok"><FileText size={13} aria-hidden="true" />Uploaded</span></FileLink>
    : <span className="cl-pill" title="No agreement uploaded yet">Missing</span>

  const columns: Column<Company>[] = [
    {
      key: 'name', header: 'Company', width: '15%', sortable: true, value: (c) => c.name,
      render: (c) => <RecordIdentity name={c.name} detail={c.industry ?? 'Industry not added'} />,
    },
    {
      key: 'client', header: 'Client contact', width: '8.8%', sortable: true, filterable: true, value: (c) => store.client(c.clientId)?.name ?? '',
      render: (c) => <button type="button" className="cl-link" onClick={(event) => { event.stopPropagation(); navigate(`/clients/${c.clientId}`) }} title={`Open client ${clientName(c)}`}>{clientName(c)}</button>,
    },
    { key: 'city', header: 'City', width: '6%', sortable: true, value: (c) => c.city ?? '' },
    {
      key: 'country', header: 'Country', width: '8.8%', sortable: true, filterable: true, value: (c) => c.country ?? '',
      render: (c) => c.country ? <Place country={c.country} /> : <span className="cl-quiet">—</span>,
    },
    {
      key: 'contact', header: 'Contact', width: '10.4%', value: (c) => c.contactPerson ?? '',
      render: (c) => (c.contactPerson || c.contactEmail)
        ? (
          <div className="cl-cell">
            <b title={c.contactPerson}>{c.contactPerson || '—'}</b>
            {c.contactEmail && <span className="cl-line"><a href={`mailto:${c.contactEmail}`} title={c.contactEmail} onClick={(event) => event.stopPropagation()}>{c.contactEmail}</a></span>}
          </div>
        )
        : <span className="cl-quiet">—</span>,
    },
    {
      key: 'phone', header: 'Phone', width: '10%', value: (c) => c.phone ?? '',
      render: (c) => c.phone ? <div className="cl-tel"><a href={`tel:${c.phone}`} onClick={(event) => event.stopPropagation()}>{c.phone}</a></div> : <span className="cl-quiet">—</span>,
    },
    {
      key: 'whatsapp', header: 'WhatsApp', width: '10%', value: (c) => c.whatsapp ?? '',
      render: (c) => c.whatsapp ? <div className="cl-tel"><a href={`https://wa.me/${digits(c.whatsapp)}`} target="_blank" rel="noreferrer" title="Open WhatsApp" onClick={(event) => event.stopPropagation()}>{c.whatsapp}</a></div> : <span className="cl-quiet">—</span>,
    },
    {
      key: 'orders', header: 'Open orders', width: '5.2%', align: 'right', sortable: true, value: (c) => openByCompany.get(c.id) ?? 0,
      render: (c) => <span className="tnum">{openByCompany.get(c.id) ?? 0}</span>,
    },
    {
      key: 'agreement', header: 'Agreement', width: '9.4%', value: (c) => (c.agreement ? 'Uploaded' : 'Missing'),
      render: agreementCell,
    },
    { key: 'status', header: 'Status', width: '7.6%', value: (c) => c.status, render: (c) => <StatusPill status={c.status} /> },
    {
      key: 'actions', header: 'Actions', actions: true,
      render: (c) => (
        <span className="cl-row-actions" onClick={(event) => event.stopPropagation()}>
          <IconButton label={`Edit ${c.name}`} onClick={() => setEditing(c)}><Pencil className="h-4 w-4" /></IconButton>
          <RowMenu label={`More actions for ${c.name}`} items={menuFor(c)} />
        </span>
      ),
    },
  ]

  const exportCsv = () => downloadCsv(
    `companies-${new Date().toISOString().slice(0, 10)}.csv`,
    ['Company', 'Industry', 'Client contact', 'City', 'Country', 'Contact person', 'Contact email', 'Phone', 'WhatsApp', 'Open orders', 'Agreement', 'Status'],
    rows.map((c) => [c.name, c.industry, clientName(c), c.city, c.country, c.contactPerson, c.contactEmail, c.phone, c.whatsapp, openByCompany.get(c.id) ?? 0, c.agreement ? 'Uploaded' : 'Missing', c.status]),
  )

  const emptyState = (
    <EmptyState
      icon={hasFilters && canAdd ? <Search size={20} strokeWidth={1.6} /> : <Building2 size={20} strokeWidth={1.6} />}
      title={!canAdd ? 'You need an active client first' : hasFilters ? 'No companies found' : 'No companies yet'}
      description={
        !canAdd
          ? 'Add a client, or make an existing client active, before adding a company.'
          : hasFilters ? 'Clear the search and filters to see all companies.' : 'Add the company you will create orders for.'
      }
      action={
        !canAdd ? (
          <Button variant="primary" onClick={() => navigate('/clients')}>Go to Clients</Button>
        ) : hasFilters ? <Button onClick={clear}>Clear filters</Button> : (
          <Button variant="primary" onClick={() => setAdding(true)}>New company</Button>
        )
      }
    />
  )

  const detail = detailId ? store.company(detailId) : undefined
  const openOrdersTotal = store.orders.filter((order) => order.status === 'open').length
  const countryCount = new Set(store.companies.map((company) => company.country?.trim()).filter(Boolean)).size

  return (
    <div className="companies-v2">
      <PageHeader
        title="Companies"
        eyebrow="Companies you hire for"
        description="The companies you hire for, their contacts and their open orders."
        actions={
          <Button variant="primary" icon={<Plus className="h-4 w-4" />} onClick={() => setAdding(true)} disabled={!canAdd}>
            New company
          </Button>
        }
      />

      <SummaryCards
        items={[
          { label: 'Total companies', value: store.companies.length, detail: 'All companies', icon: Building2 },
          { label: 'Active companies', value: store.companies.filter((company) => company.status === 'active').length, detail: 'Ready for new orders', icon: ShieldCheck, tone: 'green' },
          { label: 'Open orders', value: openOrdersTotal, detail: 'Still hiring', icon: BriefcaseBusiness, tone: 'amber' },
          { label: 'Countries', value: countryCount, detail: 'Where your companies are', icon: Globe2 },
        ]}
      />

      <div ref={panelRef} className="cl-measure">
      <Panel flush>
        <Tabs tabs={[
          { key: 'all', label: 'All companies' },
          { key: 'active', label: 'Active' },
          { key: 'inactive', label: 'Inactive' },
        ]} active={tab} onChange={(key) => { setTab(key as typeof tab); setShown(CARD_PAGE) }} />
        <div className="cl-toolbar is-companies">
          <SearchInput value={query} onChange={(value) => { setQuery(value); setShown(CARD_PAGE) }} placeholder="Search company, industry, contact or city" />
          <Select value={clientFilter} onChange={(event) => setClientFilter(event.target.value)} aria-label="Filter by client">
            <option value="all">All clients</option>
            {store.clients.map((client) => <option key={client.id} value={client.id}>{client.name}</option>)}
          </Select>
          <Select value={country} onChange={(event) => { setCountry(event.target.value); setShown(CARD_PAGE) }} aria-label="Filter by country">
            <option value="">All countries</option>
            {countries.map((name) => <option key={name} value={name}>{name}</option>)}
          </Select>
          {!narrow && <ViewToggle view={savedView} onChange={setView} />}
        </div>
        <div className="cl-resultbar">
          <div className="cl-resultbar-main"><span role="status">Showing {rows.length} of {store.companies.length} companies</span></div>
          <div className="cl-resultbar-actions">
            {hasFilters && <Button size="sm" variant="ghost" onClick={clear}>Clear filters</Button>}
            {rows.length > 0 && <Button size="sm" variant="secondary" icon={<Download className="h-3.5 w-3.5" />} onClick={exportCsv} title="Download the companies shown here as a spreadsheet (CSV) file">Download CSV</Button>}
          </div>
        </div>

        {view === 'list' ? (
          <DataTable
            compact
            stackBelow={false}
            resetPageKey={`${query}:${clientFilter}:${tab}:${country}`}
            rows={rows}
            columns={columns}
            rowKey={(c) => c.id}
            onRowClick={(c) => setDetailId(c.id)}
            empty={emptyState}
          />
        ) : rows.length === 0 ? emptyState : (
          <>
            <div className="cl-grid">
              {rows.slice(0, shown).map((c) => (
                <article key={c.id} className="cl-card">
                  <header>
                    <Avatar name={c.name} size="lg" />
                    <div className="cl-card-title">
                      <h3><button type="button" onClick={() => setDetailId(c.id)} title={`Open details of ${c.name}`}>{c.name}</button></h3>
                      <p title={c.industry ?? undefined}>{c.industry ?? 'Industry not added'}</p>
                    </div>
                    <StatusPill status={c.status} />
                  </header>
                  <dl className="cl-facts">
                    <div><dt>Client</dt><dd><button type="button" className="cl-link" onClick={() => navigate(`/clients/${c.clientId}`)}>{clientName(c)}</button></dd></div>
                    <div><dt>Location</dt><dd><Place country={c.country} city={c.city} /></dd></div>
                    <div><dt>Contact</dt><dd title={c.contactPerson ?? undefined}>{c.contactPerson || <span className="cl-quiet">Not added</span>}</dd></div>
                    <div><dt>Phone</dt><dd>{c.phone ? <a className="cl-tel" href={`tel:${c.phone}`}>{c.phone}</a> : <span className="cl-quiet">Not added</span>}</dd></div>
                    <div><dt>WhatsApp</dt><dd>{c.whatsapp ? <><a className="cl-tel" href={`https://wa.me/${digits(c.whatsapp)}`} target="_blank" rel="noreferrer"><MessageCircle size={13} className="cl-wa" aria-hidden="true" />{c.whatsapp}</a><CopyButton value={c.whatsapp} label={`Copy WhatsApp number of ${c.name}`} /></> : <span className="cl-quiet">Not added</span>}</dd></div>
                    <div><dt>Agreement</dt><dd>{agreementCell(c)}</dd></div>
                    <div><dt>Open orders</dt><dd className="tnum">{openByCompany.get(c.id) ?? 0}</dd></div>
                  </dl>
                  <footer>
                    <Button size="sm" variant="secondary" icon={<Pencil className="h-3.5 w-3.5" />} onClick={() => setEditing(c)}>Edit</Button>
                    <Button size="sm" variant="secondary" icon={<ArrowRight className="h-3.5 w-3.5" />} onClick={() => navigate(ordersOf(c))}>View orders</Button>
                    <RowMenu label={`More actions for ${c.name}`} items={menuFor(c)} />
                  </footer>
                </article>
              ))}
            </div>
            {rows.length > shown && (
              <div className="cl-more">
                <span className="tnum">Showing {shown} of {rows.length}</span>
                <Button size="sm" onClick={() => setShown((value) => value + CARD_PAGE)}>Show more</Button>
              </div>
            )}
          </>
        )}
      </Panel>
      </div>

      {detail && !editing && !deletingId && !history && (
        <CompanyDetail
          company={detail}
          onClose={() => setDetailId(null)}
          onEdit={() => { setDetailId(null); setEditing(detail) }}
          onHistory={() => { setDetailId(null); setHistory(detail.id) }}
          onDelete={store.can('delete_records') ? () => { setDetailId(null); setDeletingId(detail.id) } : undefined}
          onOrders={() => navigate(ordersOf(detail))}
        />
      )}
      {(adding || editing) && <CompanyDialog company={editing ?? undefined} clientId={store.client(clientFilter)?.status === 'active' ? clientFilter : undefined} onClose={() => { setAdding(false); setEditing(null) }} />}
      {history && <RecordAudit kind="company" id={history} onClose={() => setHistory(null)} />}
      <DeleteRecordDialog
        kind="company"
        id={deletingId ?? ''}
        open={!!deletingId}
        onClose={() => setDeletingId(null)}
        onEdit={() => setEditing(store.company(deletingId ?? '') ?? null)}
      />
    </div>
  )
}
