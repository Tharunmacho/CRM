import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Building2, Eye, Plus, Target, UserCheck, Users, Zap } from 'lucide-react'
import { useStore } from '@/shared/state/crm-store'
import type { Client } from '@/shared/model/types'
import { DataTable, type Column } from '@/shared/ui/table'
import { EmptyState, PageHeader, Panel, StatusPill } from '@/shared/ui/display'
import { RecordIdentity, SummaryCards } from '@/shared/ui/workspace'
import { Button, IconButton, SearchInput, Select } from '@/shared/ui/primitives'
import { Tabs } from '@/shared/ui/overlay'
import { ClientDialog } from '@/features/clients/components/client-dialog'
import { useWorkspaceReady } from '@/features/master-data/use-workspace-ready'
import '@/styles/clients.css'

type Sort = 'recent' | 'name' | 'demand'

/**
 * Clients are people. The businesses they own are companies, and an order is placed
 * for a company — so one client can appear behind several employers.
 */
export default function ClientsPage() {
  const store = useStore()
  const snapshot = useWorkspaceReady('clients')
  const navigate = useNavigate()
  const [query, setQuery] = useState('')
  const [adding, setAdding] = useState(false)
  const [tab, setTab] = useState<'all' | 'active' | 'inactive'>('all')
  const [hiring, setHiring] = useState(false)
  const [country, setCountry] = useState('')
  const [sort, setSort] = useState<Sort>('recent')

  // Open orders and seats per client, through the companies each client owns.
  const demand = useMemo(() => {
    const owner = new Map(store.companies.map((company) => [company.id, company.clientId]))
    const byClient = new Map<string, { orders: number; seats: number }>()
    for (const order of store.orders) {
      if (order.status !== 'open') continue
      const clientId = owner.get(order.companyId)
      if (!clientId) continue
      const entry = byClient.get(clientId) ?? { orders: 0, seats: 0 }
      entry.orders += 1
      entry.seats += order.openings
      byClient.set(clientId, entry)
    }
    return byClient
  }, [store.companies, store.orders])
  const sectors = (client: Client) => [...new Set(store.companies.filter((company) => company.clientId === client.id).map((company) => company.industry?.trim()).filter(Boolean) as string[])]
  const companyCount = (client: Client) => store.companies.filter((company) => company.clientId === client.id).length
  const countries = useMemo(() => [...new Set(store.clients.map((client) => client.country?.trim()).filter(Boolean) as string[])].sort(), [store.clients])
  const hasFilters = Boolean(query.trim() || hiring || country || tab !== 'all')
  const clear = () => { setQuery(''); setHiring(false); setCountry(''); setTab('all') }

  const rows = useMemo(() => {
    const q = query.trim().toLowerCase()
    const matched = store.clients.filter((c) =>
      (tab === 'all' || c.status === tab)
      && (!hiring || (demand.get(c.id)?.orders ?? 0) > 0)
      && (!country || c.country?.trim() === country)
      && (!q
        || c.name.toLowerCase().includes(q)
        || (c.email ?? '').toLowerCase().includes(q)
        || (c.phone ?? '').includes(q)
        || (c.city ?? '').toLowerCase().includes(q)
        || (c.country ?? '').toLowerCase().includes(q)
        || store.companies.some((co) => co.clientId === c.id && [co.name, co.industry, co.contactPerson, co.contactEmail].some((value) => (value ?? '').toLowerCase().includes(q)))))
    if (sort === 'name') return [...matched].sort((a, b) => a.name.localeCompare(b.name))
    if (sort === 'demand') return [...matched].sort((a, b) => (demand.get(b.id)?.seats ?? 0) - (demand.get(a.id)?.seats ?? 0) || a.name.localeCompare(b.name))
    // The directory is stored oldest first, so the newest clients come last.
    return [...matched].reverse()
  }, [store.clients, store.companies, query, tab, hiring, country, sort, demand])

  if (!snapshot.ready) return <Panel><p role={snapshot.error ? 'alert' : 'status'} className="text-sm text-ink-600">{snapshot.error || 'Loading client contacts…'}</p>{snapshot.error && <Button className="mt-3" onClick={snapshot.retry}>Retry clients</Button>}</Panel>

  const columns: Column<Client>[] = [
    {
      key: 'name',
      header: 'Client',
      width: '26%',
      render: (c) => {
        const industries = sectors(c)
        return <RecordIdentity name={c.name} detail={industries.length ? industries.slice(0, 2).join(' · ') : 'No sector recorded'} />
      },
    },
    {
      key: 'contact',
      header: 'Contact',
      width: '26%',
      render: (c) => (
        <div className="clients-cell">
          {c.phone ? <span className="primary ident whitespace-nowrap">{c.phone}</span> : <span className="text-ink-300">—</span>}
          {c.email && <span className="secondary" title={c.email}>{c.email}</span>}
          {c.whatsapp && c.whatsapp !== c.phone && <span className="secondary">WhatsApp · {c.whatsapp}</span>}
        </div>
      ),
    },
    {
      key: 'location',
      header: 'Location',
      width: '16%',
      render: (c) => (
        <div className="clients-cell">
          {c.country ? <span className="primary">{c.country}</span> : <span className="text-ink-300">—</span>}
          {c.city && <span className="secondary">{c.city}</span>}
        </div>
      ),
    },
    {
      key: 'demand',
      header: 'Open orders',
      align: 'right',
      width: '14%',
      render: (c) => {
        const entry = demand.get(c.id)
        const companies = companyCount(c)
        return (
          <span className="clients-demand">
            <span className="primary tnum">{entry?.orders ?? 0} {(entry?.orders ?? 0) === 1 ? 'order' : 'orders'}</span>
            <span className="secondary tnum">{entry ? `${entry.seats} open seats` : `${companies} ${companies === 1 ? 'company' : 'companies'}`}</span>
          </span>
        )
      },
    },
    { key: 'status', header: 'Status', width: '10%', render: (c) => <StatusPill status={c.status} dot /> },
    {
      key: 'actions',
      header: '',
      width: '56px',
      render: (c) => <div className="flex justify-end" onClick={(event) => event.stopPropagation()}><IconButton label={`Open ${c.name}`} onClick={() => navigate(`/clients/${c.id}`)}><Eye className="h-4 w-4" /></IconButton></div>,
    },
  ]

  const openOrders = [...demand.values()].reduce((total, entry) => total + entry.orders, 0)
  const openSeats = [...demand.values()].reduce((total, entry) => total + entry.seats, 0)

  return (
    <>
      <PageHeader
        title="Clients"
        eyebrow="Client relationships"
        description="Clients who submit hiring requirements, with their employers and open orders."
        actions={
          <>
            <Button variant="secondary" icon={<Building2 className="h-4 w-4" />} onClick={() => navigate('/companies')}>
              Companies
            </Button>
            <Button variant="primary" icon={<Plus className="h-4 w-4" />} onClick={() => setAdding(true)}>
              New client
            </Button>
          </>
        }
      />

      <SummaryCards
        items={[
          { label: 'Clients', value: store.clients.length, detail: `${store.clients.filter((client) => client.status === 'active').length} active`, icon: Users },
          { label: 'Hiring now', value: demand.size, detail: 'Clients with open orders', icon: Zap, tone: 'amber', active: hiring, onSelect: () => setHiring((value) => !value) },
          { label: 'Live job orders', value: openOrders, detail: 'Open orders awaiting candidates', icon: UserCheck, tone: 'violet' },
          { label: 'Open seats', value: openSeats, detail: 'Positions still to be filled', icon: Target, tone: 'green' },
        ]}
      />

      <Panel flush>
        <Tabs tabs={[
          { key: 'all', label: 'Everyone', count: store.clients.length },
          { key: 'active', label: 'Active', count: store.clients.filter((client) => client.status === 'active').length },
          { key: 'inactive', label: 'Inactive', count: store.clients.filter((client) => client.status === 'inactive').length },
        ]} active={tab} onChange={(key) => setTab(key as typeof tab)} />
        <div className="clients-filters">
          <SearchInput value={query} onChange={setQuery} placeholder="Search name, contact, email, sector…" />
          <button type="button" className="clients-chip" aria-pressed={hiring} onClick={() => setHiring((value) => !value)}><Zap size={15} />Hiring now</button>
          <Select aria-label="Filter by country" value={country} onChange={(event) => setCountry(event.target.value)}>
            <option value="">All countries</option>
            {countries.map((name) => <option key={name} value={name}>{name}</option>)}
          </Select>
          <Select aria-label="Sort clients" value={sort} onChange={(event) => setSort(event.target.value as Sort)}>
            <option value="recent">Recently added</option>
            <option value="name">Name A–Z</option>
            <option value="demand">Most open seats</option>
          </Select>
        </div>
        <div className="clients-count" role="status">
          <span>{rows.length} of {store.clients.length} clients</span>
          {hasFilters && <Button size="sm" variant="ghost" onClick={clear}>Clear filters</Button>}
        </div>
        <DataTable
          resetPageKey={`${query}:${tab}:${hiring}:${country}:${sort}`}
          rows={rows}
          columns={columns}
          rowKey={(c) => c.id}
          onRowClick={(c) => navigate(`/clients/${c.id}`)}
          empty={
            <EmptyState
              title={hasFilters ? 'No client contacts match' : 'No clients yet'}
              description={hasFilters ? 'Try another name, email, phone number, sector or location.' : 'Add the client contact, then the employer companies and their recruitment orders.'}
              action={hasFilters ? <Button onClick={clear}>Clear filters</Button> : <Button variant="primary" onClick={() => setAdding(true)}>New client</Button>}
            />
          }
        />
      </Panel>

      {adding && <ClientDialog onClose={() => setAdding(false)} />}
    </>
  )
}
