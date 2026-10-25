import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ArrowRight, Building2, Download, FileWarning, MessageCircle, Plus, Search, Target, Users, X, Zap } from 'lucide-react'
import { useStore } from '@/shared/state/crm-store'
import type { Client } from '@/shared/model/types'
import { DataTable, type Column } from '@/shared/ui/table'
import { EmptyState, PageHeader, Panel, StatusPill } from '@/shared/ui/display'
import { KpiStrip, RecordIdentity } from '@/shared/ui/workspace'
import { Button, SearchInput, Select } from '@/shared/ui/primitives'
import { Tabs } from '@/shared/ui/overlay'
import { SnapshotState } from '@/shared/ui/page-loading'
import { ClientDialog } from '@/features/clients/components/client-dialog'
import {
  AgreementPill, Avatar, CopyButton, DemandCell, Place, ViewToggle, demandByClient, downloadCsv, useElementWidth, useSavedView,
  type Demand,
} from '@/features/clients/components/parts'
import { useWorkspaceReady } from '@/features/master-data/use-workspace-ready'
import '@/styles/clients.css'

type Sort = 'recent' | 'name' | 'demand'

const CARD_PAGE = 12

/**
 * The directory reads company-first, as the PRD describes a client: the employer, its
 * industry, contact person, location, agreement and hiring demand on one row. The contact
 * person is the client record; the employers behind it are its companies.
 */
export default function ClientsPage() {
  const store = useStore()
  const snapshot = useWorkspaceReady('clients')
  const navigate = useNavigate()
  const [query, setQuery] = useState('')
  const [adding, setAdding] = useState(false)
  const [tab, setTab] = useState<'all' | 'active' | 'inactive'>('all')
  const [hiring, setHiring] = useState(false)
  const [noAgreement, setNoAgreement] = useState(false)
  const [country, setCountry] = useState('')
  const [industry, setIndustry] = useState('')
  const [sort, setSort] = useState<Sort>('recent')
  const [savedView, setView] = useSavedView('crm-clients-view')
  const [shown, setShown] = useState(CARD_PAGE)
  // The table needs about 1020px for its columns; with less room the same records read as cards.
  const [panelRef, panelWidth] = useElementWidth()
  const narrow = panelWidth > 0 && panelWidth < 1020
  const view = narrow ? 'cards' : savedView

  const companiesByClient = useMemo(() => {
    const grouped = new Map<string, typeof store.companies>()
    for (const company of store.companies) {
      const list = grouped.get(company.clientId)
      if (list) list.push(company)
      else grouped.set(company.clientId, [company])
    }
    return grouped
  }, [store.companies])
  const companiesOf = (client: Client) => companiesByClient.get(client.id) ?? []

  // Open orders, their openings and what is already deployed, per client.
  const demand = useMemo(() => demandByClient(store.companies, store.orders, store.applications), [store.companies, store.orders, store.applications])

  const agreementOf = (client: Client) => {
    const own = companiesOf(client)
    return { total: own.length, filed: own.filter((company) => company.agreement).length }
  }
  const lacksAgreement = (client: Client) => { const { total, filed } = agreementOf(client); return total > 0 && filed < total }
  const headline = (client: Client) => companiesOf(client)[0]?.name ?? client.name
  const title = (client: Client) => { const own = companiesOf(client); return own.length > 1 ? `${headline(client)} +${own.length - 1}` : headline(client) }
  const sectorsOf = (client: Client) => [...new Set(companiesOf(client).map((company) => company.industry?.trim()).filter(Boolean) as string[])]
  const placeOf = (client: Client) => {
    const home = companiesOf(client)[0]
    return { country: client.country || home?.country, city: client.city || home?.city }
  }

  const countries = useMemo(() => [...new Set([
    ...store.clients.map((client) => client.country?.trim()),
    ...store.companies.map((company) => company.country?.trim()),
  ].filter(Boolean) as string[])].sort(), [store.clients, store.companies])
  const industries = useMemo(() => [...new Set(store.companies.map((company) => company.industry?.trim()).filter(Boolean) as string[])].sort(), [store.companies])
  const hasFilters = Boolean(query.trim() || hiring || noAgreement || country || industry || tab !== 'all')
  const clear = () => { setQuery(''); setHiring(false); setNoAgreement(false); setCountry(''); setIndustry(''); setTab('all'); setShown(CARD_PAGE) }

  const rows = useMemo(() => {
    const q = query.trim().toLowerCase()
    const matched = store.clients.filter((c) => {
      const own = companiesByClient.get(c.id) ?? []
      const filed = own.filter((company) => company.agreement).length
      return (tab === 'all' || c.status === tab)
        && (!hiring || (demand.get(c.id)?.orders ?? 0) > 0)
        && (!noAgreement || (own.length > 0 && filed < own.length))
        && (!country || c.country?.trim() === country || own.some((co) => co.country?.trim() === country))
        && (!industry || own.some((co) => co.industry?.trim() === industry))
        && (!q
          || c.name.toLowerCase().includes(q)
          || (c.email ?? '').toLowerCase().includes(q)
          || (c.phone ?? '').includes(q)
          || (c.city ?? '').toLowerCase().includes(q)
          || (c.country ?? '').toLowerCase().includes(q)
          || own.some((co) => [co.name, co.industry, co.contactPerson, co.contactEmail, co.city].some((value) => (value ?? '').toLowerCase().includes(q))))
    })
    const label = (c: Client) => (companiesByClient.get(c.id)?.[0]?.name ?? c.name)
    if (sort === 'name') return [...matched].sort((a, b) => label(a).localeCompare(label(b)))
    if (sort === 'demand') return [...matched].sort((a, b) => (demand.get(b.id)?.toFill ?? 0) - (demand.get(a.id)?.toFill ?? 0) || label(a).localeCompare(label(b)))
    // The directory is stored oldest first, so the newest clients come last.
    return [...matched].reverse()
  }, [store.clients, companiesByClient, query, tab, hiring, noAgreement, country, industry, sort, demand])

  if (!snapshot.ready) return <SnapshotState error={snapshot.error} onRetry={snapshot.retry} retryLabel="Try again" label="Loading clients" />

  const idleCaption = (c: Client) => { const count = companiesOf(c).length; return `${count} ${count === 1 ? 'company' : 'companies'}` }

  const columns: Column<Client>[] = [
    {
      key: 'name',
      header: 'Company',
      width: '22.5%',
      render: (c) => {
        const sectors = sectorsOf(c)
        const detail = sectors.length ? sectors.slice(0, 2).join(' · ') : companiesOf(c).length ? 'No industry added' : 'No company added yet'
        return <RecordIdentity name={title(c)} detail={<span title={detail}>{detail}</span>} />
      },
    },
    {
      key: 'contact',
      header: 'Contact person',
      width: '22%',
      render: (c) => (
        <div className="cl-cell">
          <b title={c.name}>{c.name}</b>
          {c.phone && <span className="cl-line ident">{c.whatsapp && <MessageCircle size={12} className="cl-wa" aria-label="Has WhatsApp" />}<a href={`tel:${c.phone}`} title={c.phone} onClick={(event) => event.stopPropagation()}>{c.phone}</a></span>}
          {c.email && <span className="cl-line"><a href={`mailto:${c.email}`} title={c.email} onClick={(event) => event.stopPropagation()}>{c.email}</a><CopyButton value={c.email} label={`Copy email of ${c.name}`} /></span>}
        </div>
      ),
    },
    {
      key: 'location',
      header: 'Location',
      width: '12%',
      render: (c) => { const place = placeOf(c); return <Place country={place.country} city={place.city} empty="—" stacked /> },
    },
    {
      key: 'agreement',
      header: 'Agreement',
      width: '13.5%',
      render: (c) => { const { total, filed } = agreementOf(c); return <AgreementPill filed={filed} total={total} /> },
    },
    {
      key: 'demand',
      header: 'Open orders',
      width: '12%',
      render: (c) => <DemandCell demand={demand.get(c.id)} empty={idleCaption(c)} />,
    },
    { key: 'status', header: 'Status', width: '8%', render: (c) => <StatusPill status={c.status} /> },
    {
      key: 'open',
      header: 'Open',
      actions: true,
      render: (c) => <span className="cl-open" aria-label={`Open ${title(c)}`}>Open<ArrowRight size={14} aria-hidden="true" /></span>,
    },
  ]

  const totals = [...demand.values()].reduce((sum: Demand, entry) => ({ orders: sum.orders + entry.orders, openings: sum.openings + entry.openings, deployed: sum.deployed + entry.deployed, toFill: sum.toFill + entry.toFill }), { orders: 0, openings: 0, deployed: 0, toFill: 0 })
  const activeCount = store.clients.filter((client) => client.status === 'active').length
  const missing = store.clients.filter(lacksAgreement).length

  const exportCsv = () => downloadCsv(
    `clients-${new Date().toISOString().slice(0, 10)}.csv`,
    ['Company', 'Industry', 'Contact person', 'Email', 'Phone', 'WhatsApp', 'Country', 'City', 'Agreement', 'Open orders', 'Openings left', 'Status'],
    rows.map((c) => {
      const place = placeOf(c)
      const { total, filed } = agreementOf(c)
      const entry = demand.get(c.id)
      return [title(c), sectorsOf(c).join('; '), c.name, c.email, c.phone, c.whatsapp, place.country, place.city, !total ? '' : filed === total ? 'Uploaded' : filed ? `${filed} of ${total} uploaded` : 'Missing', entry?.orders ?? 0, entry?.toFill ?? 0, c.status]
    }),
  )

  const emptyState = (
    <EmptyState
      icon={hasFilters ? <Search size={20} strokeWidth={1.6} /> : <Building2 size={20} strokeWidth={1.6} />}
      title={hasFilters ? 'No clients found' : 'No clients yet'}
      description={hasFilters ? 'Try a different name, email, phone number, industry or place.' : 'Add your first client with its company and contact person. Then you can create orders for it.'}
      action={hasFilters ? <Button onClick={clear}>Clear filters</Button> : <Button variant="primary" onClick={() => setAdding(true)}>New client</Button>}
    />
  )

  return (
    <div className="clients-v2">
      <PageHeader
        title="Clients"
        eyebrow="Your clients"
        description="Clients you hire for, with their contact people, companies and open orders."
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

      <KpiStrip
        items={[
          { label: 'Clients', value: store.clients.length, detail: `${activeCount} active · ${store.companies.length} ${store.companies.length === 1 ? 'company' : 'companies'}`, icon: Users },
          { label: 'Hiring now', value: demand.size, detail: `${totals.orders} open ${totals.orders === 1 ? 'order' : 'orders'}`, icon: Zap, tone: 'amber', active: hiring, onSelect: () => { setHiring((value) => !value); setShown(CARD_PAGE) } },
          { label: 'Openings left', value: totals.toFill, detail: totals.deployed ? `${totals.deployed} of ${totals.openings} already deployed` : 'Jobs still to fill', icon: Target, tone: 'green' },
          { label: 'Agreement missing', value: missing, detail: missing ? 'Get a signed agreement' : 'Every client has an agreement', icon: FileWarning, tone: missing ? 'red' : 'green', active: noAgreement, onSelect: () => { setNoAgreement((value) => !value); setShown(CARD_PAGE) } },
        ]}
      />

      <div ref={panelRef} className="cl-measure">
      <Panel flush>
        <Tabs tabs={[
          { key: 'all', label: 'All clients' },
          { key: 'active', label: 'Active' },
          { key: 'inactive', label: 'Inactive' },
        ]} active={tab} onChange={(key) => { setTab(key as typeof tab); setShown(CARD_PAGE) }} />
        <div className="cl-toolbar">
          <SearchInput value={query} onChange={(value) => { setQuery(value); setShown(CARD_PAGE) }} placeholder="Search company or contact" />
          <Select aria-label="Filter by industry" value={industry} onChange={(event) => { setIndustry(event.target.value); setShown(CARD_PAGE) }}>
            <option value="">All industries</option>
            {industries.map((name) => <option key={name} value={name}>{name}</option>)}
          </Select>
          <Select aria-label="Filter by country" value={country} onChange={(event) => { setCountry(event.target.value); setShown(CARD_PAGE) }}>
            <option value="">All countries</option>
            {countries.map((name) => <option key={name} value={name}>{name}</option>)}
          </Select>
          <Select aria-label="Sort clients" value={sort} onChange={(event) => setSort(event.target.value as Sort)}>
            <option value="recent">Recently added</option>
            <option value="name">Company name A to Z</option>
            <option value="demand">Most openings left</option>
          </Select>
          {!narrow && <ViewToggle view={savedView} onChange={setView} />}
        </div>
        <div className="cl-resultbar">
          <div className="cl-resultbar-main">
            <span role="status">Showing {rows.length} of {store.clients.length} clients</span>
            {hiring && <button type="button" className="cl-chip" onClick={() => setHiring(false)} aria-label="Remove the hiring now filter">Hiring now<X size={12} aria-hidden="true" /></button>}
            {noAgreement && <button type="button" className="cl-chip" onClick={() => setNoAgreement(false)} aria-label="Remove the agreement missing filter">Agreement missing<X size={12} aria-hidden="true" /></button>}
          </div>
          <div className="cl-resultbar-actions">
            {hasFilters && <Button size="sm" variant="ghost" onClick={clear}>Clear filters</Button>}
            {rows.length > 0 && <Button size="sm" variant="secondary" icon={<Download className="h-3.5 w-3.5" />} onClick={exportCsv} title="Download the clients shown here as a spreadsheet (CSV) file">Download CSV</Button>}
          </div>
        </div>

        {view === 'list' ? (
          <DataTable
            compact
            stackBelow={false}
            resetPageKey={`${query}:${tab}:${hiring}:${noAgreement}:${country}:${industry}:${sort}`}
            rows={rows}
            columns={columns}
            rowKey={(c) => c.id}
            onRowClick={(c) => navigate(`/clients/${c.id}`)}
            empty={emptyState}
          />
        ) : rows.length === 0 ? emptyState : (
          <>
            <div className="cl-grid">
              {rows.slice(0, shown).map((c) => {
                const { total, filed } = agreementOf(c)
                const place = placeOf(c)
                const sectors = sectorsOf(c)
                return (
                  <article key={c.id} className="cl-card">
                    <header>
                      <Avatar name={title(c)} size="lg" />
                      <div className="cl-card-title">
                        <h3><button type="button" onClick={() => navigate(`/clients/${c.id}`)} title={title(c)}>{title(c)}</button></h3>
                        <p title={sectors.join(' · ')}>{sectors.length ? sectors.slice(0, 2).join(' · ') : companiesOf(c).length ? 'No industry added' : 'No company added yet'}</p>
                      </div>
                      <StatusPill status={c.status} />
                    </header>
                    <dl className="cl-facts">
                      <div><dt>Contact</dt><dd title={c.name}>{c.name}</dd></div>
                      <div><dt>Phone</dt><dd>{c.phone ? <a className="ident" href={`tel:${c.phone}`}>{c.phone}</a> : <span className="cl-quiet">Not added</span>}</dd></div>
                      <div><dt>Email</dt><dd>{c.email ? <><a href={`mailto:${c.email}`} title={c.email}>{c.email}</a><CopyButton value={c.email} label={`Copy email of ${c.name}`} /></> : <span className="cl-quiet">Not added</span>}</dd></div>
                      <div><dt>Location</dt><dd><Place country={place.country} city={place.city} /></dd></div>
                      <div><dt>Agreement</dt><dd><AgreementPill filed={filed} total={total} /></dd></div>
                      <div><dt>Open orders</dt><dd><DemandCell demand={demand.get(c.id)} empty={idleCaption(c)} /></dd></div>
                    </dl>
                    <footer>
                      <Button size="sm" variant="secondary" icon={<ArrowRight className="h-3.5 w-3.5" />} onClick={() => navigate(`/clients/${c.id}`)}>Open client</Button>
                    </footer>
                  </article>
                )
              })}
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

      {adding && <ClientDialog onClose={() => setAdding(false)} />}
    </div>
  )
}
