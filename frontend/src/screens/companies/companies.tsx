import { useMemo, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { BriefcaseBusiness, Building2, Globe2, History, Pencil, Plus, ShieldCheck, Trash2 } from 'lucide-react'
import { useStore } from '@/shared/state/crm-store'
import type { Company } from '@/shared/model/types'
import { DataTable, type Column } from '@/shared/ui/table'
import { EmptyState, PageHeader, Panel, PanelHeader, StatusPill } from '@/shared/ui/display'
import { RecordIdentity, SummaryCards } from '@/shared/ui/workspace'
import { Button, IconButton, SearchInput, Select } from '@/shared/ui/primitives'
import { FileLink } from '@/shared/ui/file-drop'
import { CompanyDialog } from '@/features/companies/components/company-dialog'
import { RecordAudit } from '@/features/master-data/components/record-audit'
import { DeleteRecordDialog } from '@/shared/components/delete-dialog'
import { useWorkspaceReady } from '@/features/master-data/use-workspace-ready'

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
  const clientFilter = params.get('client') || 'all'
  const setClientFilter = (value: string) => setParams((previous) => { const next = new URLSearchParams(previous); value === 'all' ? next.delete('client') : next.set('client', value); return next })
  const canAdd = store.clients.some((client) => client.status === 'active')
  const [adding, setAdding] = useState(false)
  const [editing, setEditing] = useState<Company | null>(null)
  const [history, setHistory] = useState<string | null>(null)
  const [deletingId, setDeletingId] = useState<string | null>(null)

  const rows = useMemo(() => {
    const q = query.trim().toLowerCase()
    return store.companies.filter((c) => {
      if (clientFilter !== 'all' && c.clientId !== clientFilter) return false
      if (!q) return true
      return (
        c.name.toLowerCase().includes(q) ||
        (c.industry ?? '').toLowerCase().includes(q) ||
        (c.city ?? '').toLowerCase().includes(q) ||
        (c.country ?? '').toLowerCase().includes(q) ||
        (store.client(c.clientId)?.name ?? '').toLowerCase().includes(q)
      )
    })
  }, [store.companies, store.clients, query, clientFilter])

  if (!snapshot.ready) return <Panel><p role={snapshot.error ? 'alert' : 'status'} className="text-sm text-ink-600">{snapshot.error || 'Loading employer companies…'}</p>{snapshot.error && <Button className="mt-3" onClick={snapshot.retry}>Retry employers</Button>}</Panel>

  const columns: Column<Company>[] = [
    {
      key: 'name',
      header: 'Company',
      sortable: true,
      width: '22%',
      value: (c) => c.name,
      render: (c) => (
        <RecordIdentity name={c.name} detail={c.industry ?? 'Industry not recorded'} icon={Building2} />
      ),
    },
    {
      key: 'client',
      header: 'Client contact',
      sortable: true,
      filterable: true,
      className: 'whitespace-nowrap',
      value: (c) => store.client(c.clientId)?.name ?? '',
      render: (c) => <button type="button" className="text-left text-navy-700 hover:underline" onClick={(event) => { event.stopPropagation(); navigate(`/clients/${c.clientId}`) }}>{store.client(c.clientId)?.name ?? 'Client unavailable'}</button>,
    },
    { key: 'city', header: 'City', sortable: true, filterable: true, value: (c) => c.city ?? '' },
    {
      key: 'country',
      header: 'Country',
      sortable: true,
      filterable: true,
      value: (c) => c.country ?? '',
    },
    {
      key: 'contact',
      header: 'Contact',
      className: 'whitespace-nowrap',
      value: (c) => c.contactPerson ?? '',
    },
    {
      key: 'phone',
      header: 'Phone',
      className: 'whitespace-nowrap',
      value: (c) => c.phone ?? '',
      render: (c) =>
        c.phone ? <span className="ident text-ink-600">{c.phone}</span> : <span className="text-ink-300">—</span>,
    },
    {
      key: 'whatsapp',
      header: 'WhatsApp',
      className: 'whitespace-nowrap ident',
      value: (c) => c.whatsapp || '—',
    },
    {
      key: 'orders',
      header: 'Open orders',
      align: 'right',
      sortable: true,
      value: (c) => store.orders.filter((o) => o.companyId === c.id && o.status === 'open').length,
      render: (c) => (
        <span className="tnum">
          {store.orders.filter((o) => o.companyId === c.id && o.status === 'open').length}
        </span>
      ),
    },
    {
      key: 'agreement',
      header: 'Agreement',
      className: 'whitespace-nowrap',
      value: (c) => (c.agreement ? 'Uploaded' : ''),
      render: (c) =>
        c.agreement ? (
          <FileLink url={c.agreement.url} title={c.agreement.name}>
            View agreement
          </FileLink>
        ) : (
          <span className="text-ink-300">—</span>
        ),
    },
    {
      key: 'status',
      header: 'Status',
      filterable: true,
      value: (c) => c.status,
      render: (c) => <StatusPill status={c.status} />,
    },
    { key: 'edit', header: 'Actions', render: (company) => <div className="flex items-center gap-1" onClick={(event) => event.stopPropagation()}>
      <Button size="sm" onClick={() => navigate(`/jobs?company=${encodeURIComponent(company.id)}`)}>Hiring orders</Button>
      <IconButton label={'Edit ' + company.name} onClick={() => setEditing(company)}><Pencil className="h-4 w-4" /></IconButton>
      <IconButton label={'Activity for ' + company.name} onClick={() => setHistory(company.id)}><History className="h-4 w-4" /></IconButton>
    </div> },
    ...(store.can('delete_records')
      ? [
          {
            key: 'delete',
            header: '',
            align: 'right' as const,
            render: (c: Company) => (
              <button
                onClick={(e) => {
                  // The row itself opens the client; this must not.
                  e.stopPropagation()
                  setDeletingId(c.id)
                }}
                aria-label={`Delete ${c.name}`}
                title="Delete company"
                className="rounded-control p-1.5 text-ink-400 hover:bg-stamp-50 hover:text-stamp-600"
              >
                <Trash2 className="h-3.5 w-3.5" />
              </button>
            ),
          },
        ]
      : []),
  ]

  return (
    <>
      <PageHeader
        title="Companies"
        eyebrow="Employer network"
        description="A clear view of your employers, their contacts, and the opportunities they create."
        actions={
          <>
            <Button
              variant="primary"
              icon={<Plus className="h-4 w-4" />}
              onClick={() => setAdding(true)}
              disabled={!canAdd}
            >
              New company
            </Button>
          </>
        }
      />

      <SummaryCards
        items={[
          {
            label: 'Total companies',
            value: store.companies.length,
            detail: 'In your employer network',
            icon: Building2,
          },
          {
            label: 'Active employers',
            value: store.companies.filter((company) => company.status === 'active').length,
            detail: 'Open for collaboration',
            icon: ShieldCheck,
            tone: 'green',
          },
          {
            label: 'Open orders',
            value: store.orders.filter((order) => order.status === 'open').length,
            detail: 'Recruitment in progress',
            icon: BriefcaseBusiness,
            tone: 'violet',
          },
          {
            label: 'Countries',
            value: new Set(store.companies.map((company) => company.country?.trim()).filter(Boolean))
              .size,
            detail: 'Global hiring destinations',
            icon: Globe2,
            tone: 'amber',
          },
        ]}
      />

      <Panel flush>
        <PanelHeader title="Company directory" meta="Open an employer to view its hiring orders, or edit contacts and agreements here." />
        <div className="workspace-list-toolbar">
          <SearchInput
            value={query}
            onChange={setQuery}
            placeholder="Search companies, industries, or cities..."
            className="max-w-sm flex-1"
          />
          <Select value={clientFilter} onChange={(event) => setClientFilter(event.target.value)} className="w-48" aria-label="Filter by client">
            <option value="all">All clients</option>
            {store.clients.map((client) => <option key={client.id} value={client.id}>{client.name}</option>)}
          </Select>
          <span className="ml-auto text-xs text-ink-400 tnum">{rows.length} companies</span>
        </div>
        <DataTable
          rows={rows}
          columns={columns}
          rowKey={(c) => c.id}
          onRowClick={(c) => navigate(`/jobs?company=${encodeURIComponent(c.id)}`)}
          empty={
            <EmptyState
              title={!canAdd ? 'An active client contact is needed' : query || clientFilter !== 'all' ? 'No employers match these filters' : 'No companies yet'}
              description={
                !canAdd
                  ? 'Add a client contact or reactivate an existing contact before creating an employer.'
                  : query || clientFilter !== 'all' ? 'Clear the search and client filter to see the rest of your employer network.' : 'Add the employer a job order will be placed for.'
              }
              action={
                !canAdd ? (
                  <Button variant="primary" onClick={() => navigate('/clients')}>
                    Go to Clients
                  </Button>
                ) : query || clientFilter !== 'all' ? <Button onClick={() => { setQuery(''); setClientFilter('all') }}>Clear filters</Button> : (
                  <Button variant="primary" onClick={() => setAdding(true)}>
                    New company
                  </Button>
                )
              }
            />
          }
        />
      </Panel>

      {(adding || editing) && <CompanyDialog company={editing ?? undefined} clientId={store.client(clientFilter)?.status === 'active' ? clientFilter : undefined} onClose={() => { setAdding(false); setEditing(null) }} />}
      {history && <RecordAudit kind="company" id={history} onClose={() => setHistory(null)} />}
      <DeleteRecordDialog
        kind="company"
        id={deletingId ?? ''}
        open={!!deletingId}
        onClose={() => setDeletingId(null)}
        onEdit={() => setEditing(store.company(deletingId ?? '') ?? null)}
      />
    </>
  )
}
