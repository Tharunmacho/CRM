import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { ArrowRight, BriefcaseBusiness, LayoutGrid, Pencil, Plus, Search, Table2, X } from 'lucide-react'
import { useStore } from '@/shared/state/crm-store'
import type { Order } from '@/shared/model/types'
import { Button } from '@/shared/ui/primitives'
import { Panel } from '@/shared/ui/display'
import { DataTable, type Column } from '@/shared/ui/table'
import { Flag, Progress } from '@/features/clients/components/parts'
import { highlightPieces } from '@/features/clients/client-search'
import { OrderDialog } from '@/features/jobs/components/job-dialogs'
import { OrderStatusText } from '@/features/jobs/components/order-parts'
import { orderStatusKey } from '@/features/jobs/order-meta'
import { SearchSelect } from '@/features/jobs/components/search-select'
import { clientItem } from '@/features/jobs/components/client-picker'
import { clientFilterOptions } from '@/features/jobs/job-form'
import { STATUS_FILTERS, hasJobFilters, legacyJobsQuery, matchesJob, openingsText, parseStatusFilter, passesFilters, statusWords, type JobSearchRecord } from '@/features/jobs/job-search'
import { useOrderStats } from '@/features/jobs/use-order-stats'
import { useWorkspaceReady } from '@/features/master-data/use-workspace-ready'
import '@/styles/clients-simple.css'
import '@/styles/jobs-simple.css'

type Layout = 'cards' | 'table'

const LAYOUT_KEY = 'crm-jobs-layout-v2'
const SEARCH_DELAY_MS = 150
const NEW_MARK_MS = 6000

/** The remembered choice. The table is the default (every result list opens as one); storage can be blocked, so access is guarded. */
function readLayout(): Layout {
  try { return localStorage.getItem(LAYOUT_KEY) === 'cards' ? 'cards' : 'table' } catch { return 'table' }
}

interface JobRow {
  order: Order
  title: string
  company: string
  contact: string
  country: string
  city: string
  deployed: number
  /** Raw status and company, for the filters. */
  status: string
  companyId: string
  search: JobSearchRecord
}

/** Text with the matched part of the search lit up. */
function Hi({ text, query }: { text?: string | null; query: string }) {
  if (!text) return null
  return <>{highlightPieces(text, query).map((piece, index) => piece.hit ? <mark key={index} className="cs-mark">{piece.text}</mark> : piece.text)}</>
}

const Quiet = ({ children = 'Not added' }: { children?: string }) => <span className="cl-quiet">{children}</span>

/** Flag in its own colours (a globe when no country is saved), the country in bold and the city under it. */
function Location({ country, city, query }: { country: string; city: string; query: string }) {
  if (!country && !city) return <Quiet />
  const primary = country || city
  return <span className="cl-place is-stacked">
    <Flag country={country} />
    <span className="cl-place-text">
      <b title={primary}><Hi text={primary} query={query} /></b>
      {country && city && <small title={city}><Hi text={city} query={query} /></small>}
    </span>
  </span>
}

/** "3 of 5 filled" over a slim bar. */
function Openings({ row }: { row: JobRow }) {
  return <span className="js-openings" title={`${row.deployed} deployed, ${row.order.openings} openings`}>
    <span className="cs-tnum">{openingsText(row.deployed, row.order.openings)}</span>
    <Progress done={Math.min(row.deployed, row.order.openings)} total={row.order.openings} />
  </span>
}

function JobCards({ rows, query, newId, onOpen, onEdit }: { rows: JobRow[]; query: string; newId: string; onOpen: (o: Order) => void; onEdit?: (o: Order) => void }) {
  return <div className="cs-grid">{rows.map((row) => {
    const o = row.order
    const facts: { label: string; value: ReactNode }[] = [
      { label: 'Client', value: <span className="js-client"><b title={row.company}><Hi text={row.company} query={query} /></b>{row.contact && <small title={row.contact}><Hi text={row.contact} query={query} /></small>}</span> },
      { label: 'Location', value: <Location country={row.country} city={row.city} query={query} /> },
      { label: 'Openings', value: <Openings row={row} /> },
      { label: 'Status', value: <OrderStatusText order={o} /> },
    ]
    return <article key={o.id} className={`cs-card${o.id === newId ? ' js-new' : ''}`}>
      <header>
        <span className="js-chip" aria-hidden="true"><BriefcaseBusiness size={20} strokeWidth={1.7} /></span>
        <div className="cs-title"><h3 title={row.title}><Hi text={row.title} query={query} /></h3><p title={o.ref}><Hi text={o.ref} query={query} /></p></div>
      </header>
      <dl className="cs-facts js-facts">{facts.map((fact) => <div key={fact.label}><dt>{fact.label}</dt><dd>{fact.value}</dd></div>)}</dl>
      <footer>
        <button type="button" className="cl-open cs-wide" onClick={() => onOpen(o)} aria-label={`Open ${row.title} ${o.ref}`}>Open<ArrowRight size={14} aria-hidden="true" /></button>
        {onEdit && <button type="button" className="cl-open cs-wide" onClick={() => onEdit(o)} aria-label={`Edit ${row.title} ${o.ref}`}><Pencil size={13} aria-hidden="true" />Edit</button>}
      </footer>
    </article>
  })}</div>
}

/** The table is the shared DataTable (the Clients page's look and column alignment); on a phone it stacks into labelled rows. */
function JobTable({ rows, query, loading, newId, onOpen, onEdit }: { rows: JobRow[]; query: string; loading: boolean; newId: string; onOpen: (o: Order) => void; onEdit?: (o: Order) => void }) {
  const columns: Column<JobRow>[] = [
    {
      key: 'job', header: 'Job', width: '22%', minWidth: '12rem',
      render: (row) => <div className="cl-cell"><b title={row.title}><Hi text={row.title} query={query} /></b><span className="ident" title={row.order.ref}><Hi text={row.order.ref} query={query} /></span></div>,
    },
    {
      key: 'client', header: 'Client', width: '22%', align: 'left',
      render: (row) => <div className="cl-cell"><b title={row.company}><Hi text={row.company} query={query} /></b>{row.contact && <span title={row.contact}><Hi text={row.contact} query={query} /></span>}</div>,
    },
    { key: 'location', header: 'Location', width: '16%', align: 'left', render: (row) => <Location country={row.country} city={row.city} query={query} /> },
    { key: 'openings', header: 'Openings', width: '15%', align: 'left', render: (row) => <Openings row={row} /> },
    { key: 'status', header: 'Status', width: '9%', align: 'left', render: (row) => <OrderStatusText order={row.order} /> },
    {
      key: 'actions', header: 'Actions', actions: true, minWidth: '10.5rem',
      render: (row) => <span className="cs-row-actions">
        {onEdit && <button type="button" className="cs-icon-act" onClick={(event) => { event.stopPropagation(); onEdit(row.order) }} aria-label={`Edit ${row.title} ${row.order.ref}`} title="Edit"><Pencil size={14} aria-hidden="true" /></button>}
        <button type="button" className="cl-open" onClick={(event) => { event.stopPropagation(); onOpen(row.order) }} aria-label={`Open ${row.title} ${row.order.ref}`}>Open<ArrowRight size={14} aria-hidden="true" /></button>
      </span>,
    },
  ]
  return <Panel flush><DataTable compact loading={loading} resetPageKey={query} rows={rows} columns={columns} rowKey={(row) => row.order.id} rowClassName={(row) => row.order.id === newId ? 'js-new' : undefined} onRowClick={(row) => onOpen(row.order)} /></Panel>
}

function Skeleton({ layout }: { layout: Layout }) {
  if (layout === 'table') return <JobTable rows={[]} query="" loading newId="" onOpen={() => undefined} />
  return <div className="cs-grid" role="status" aria-label="Loading">{[0, 1, 2].map((i) => <div key={i} className="cs-card cs-skel" />)}</div>
}

/**
 * Jobs. Search first: nothing is listed until the person types or presses "Show all". The search text and "show all" live in the
 * address (?q=, ?all=1) so Back from a job returns to the same results. A job is one client's opening for a job title; it is created
 * for a client chosen in the New job dialog.
 */
export default function JobsPage() {
  const store = useStore()
  const snapshot = useWorkspaceReady('jobs')
  const navigate = useNavigate()
  const stats = useOrderStats()
  const [params, setParams] = useSearchParams()
  const query = params.get('q') ?? ''
  const showAll = params.get('all') === '1'
  const filters = { status: parseStatusFilter(params.get('status')), client: params.get('client') ?? '' }
  const filtered = hasJobFilters(filters)
  const [text, setText] = useState(query)
  const [layout, setLayoutState] = useState<Layout>(readLayout)
  const [adding, setAdding] = useState(false)
  const [editing, setEditing] = useState<Order | null>(null)
  const [newId, setNewId] = useState('')
  const searchBox = useRef<HTMLInputElement>(null)
  const canEdit = store.can('manage_records')

  const patch = (changes: Record<string, string | null>) => {
    const next = new URLSearchParams(params)
    for (const [key, value] of Object.entries(changes)) { if (value) next.set(key, value); else next.delete(key) }
    setParams(next, { replace: true })
  }

  // The box follows the address (Back, a link with ?q=), and the address follows the box after a short pause in typing.
  useEffect(() => { setText((current) => current === query ? current : query) }, [query])
  useEffect(() => {
    if (text === query) return undefined
    const timer = window.setTimeout(() => patch({ q: text.trim() ? text : null }), SEARCH_DELAY_MS)
    return () => window.clearTimeout(timer)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [text])

  // Old links (?designation=, ?company=, ?view=orders, ?tab=) came from screens that are gone; they become a plain search.
  useEffect(() => {
    if (!snapshot.ready) return
    if (!['designation', 'company', 'view', 'tab'].some((key) => params.has(key))) return
    const legacy = legacyJobsQuery({ designation: params.get('designation'), company: params.get('company') }, {
      designationTitle: (id) => store.designation(id)?.title,
      companyName: (id) => store.company(id)?.name,
    })
    const keepAll = !params.get('q') && !legacy && params.get('view') === 'orders'
    patch({ designation: null, company: null, view: null, tab: null, ...(!params.get('q') && legacy ? { q: legacy } : {}), ...(keepAll ? { all: '1' } : {}) })
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [snapshot.ready])

  // "/" jumps to the search box, as on most search-first pages (ignored while typing in another field).
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== '/' || event.metaKey || event.ctrlKey || event.altKey) return
      const target = event.target as HTMLElement | null
      if (target && (target.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName))) return
      event.preventDefault(); searchBox.current?.focus()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  const setLayout = (next: Layout) => { setLayoutState(next); try { localStorage.setItem(LAYOUT_KEY, next) } catch { /* the choice just is not remembered */ } }

  // Newest jobs first.
  const rows = useMemo<JobRow[]>(() => [...store.orders].sort((a, b) => (b.createdAt ?? '').localeCompare(a.createdAt ?? '')).map((order) => {
    const designation = store.designation(order.designationId)
    const company = store.company(order.companyId)
    const client = company ? store.client(company.clientId) : undefined
    const row = {
      order, title: designation?.title ?? 'Unknown job', company: company?.name ?? '', contact: client?.name ?? '',
      country: order.country ?? '', city: order.city ?? '', deployed: stats.of(order.id).deployed, status: order.status, companyId: order.companyId,
    }
    return {
      ...row,
      search: {
        title: row.title, ref: order.ref, contact: row.contact, company: row.company, country: row.country, city: row.city,
        statuses: statusWords(order.status, orderStatusKey(order) === 'overdue'), skills: designation?.requiredSkills ?? [],
      },
    }
  // the store's lookups are rebuilt with its lists
  }), [store.orders, store.designations, store.companies, store.clients, stats]) // eslint-disable-line react-hooks/exhaustive-deps

  const searching = query.trim().length > 0
  // A filter is an alternative to typing: with one set the list shows, even without search text.
  const listing = searching || showAll || filtered
  const matched = useMemo(
    () => listing ? rows.filter((row) => passesFilters(row, filters) && matchesJob(row.search, query)) : [],
    [rows, listing, filters.status, filters.client, query], // eslint-disable-line react-hooks/exhaustive-deps
  )
  // Only clients that already have jobs can be filtered by (inactive ones too: their jobs are still in the list).
  const clientOptions = useMemo(() => clientFilterOptions({ clients: store.clients, companies: store.companies, orders: store.orders }), [store.clients, store.companies, store.orders])
  const clientItems = useMemo(() => clientOptions.map(clientItem), [clientOptions])
  const total = rows.length
  const loading = !snapshot.ready && !snapshot.error
  const loadError = !snapshot.ready ? snapshot.error : ''
  const count = matched.length

  // A job just saved is lit for a moment and scrolled into view; the list is opened wide enough to contain it.
  useEffect(() => {
    if (!newId) return undefined
    const scroll = window.setTimeout(() => document.querySelector('.jobs-simple .js-new')?.scrollIntoView({ block: 'nearest', behavior: 'smooth' }), 120)
    const done = window.setTimeout(() => setNewId(''), NEW_MARK_MS)
    return () => { window.clearTimeout(scroll); window.clearTimeout(done) }
  }, [newId])

  const clearText = () => { setText(''); patch({ q: null }); searchBox.current?.focus() }
  const clear = () => { setText(''); patch({ q: null, all: null, status: null, client: null }); searchBox.current?.focus() }
  const saved = (id: string) => { setText(''); setNewId(id); patch({ q: null, all: '1', status: null, client: null }) }
  const open = (order: Order) => navigate(`/orders/${order.id}`)

  const body = () => {
    if (loadError) {
      return <div role="alert" className="cs-state">
        <span className="cs-badge" aria-hidden="true"><BriefcaseBusiness size={24} strokeWidth={1.7} /></span>
        <h2>We could not load the jobs</h2>
        <p>{loadError}</p>
        <Button onClick={snapshot.retry}>Try again</Button>
      </div>
    }
    if (loading) return <Skeleton layout={layout} />
    if (!listing) {
      return <div className="cs-state cs-hero">
        <span className="cs-badge" aria-hidden="true"><Search size={26} strokeWidth={1.7} /></span>
        <h2>Search to find a job</h2>
        {total > 0
          ? <button type="button" className="cs-pillbtn" onClick={() => patch({ all: '1' })}>Show all {total} {total === 1 ? 'job' : 'jobs'}<ArrowRight size={15} aria-hidden="true" /></button>
          : <p>No jobs saved yet.</p>}
      </div>
    }
    if (count === 0) {
      return <div className="cs-state" role="status">
        <span className="cs-badge" aria-hidden="true"><Search size={24} strokeWidth={1.7} /></span>
        {searching
          ? <><h2>No job matches “{query.trim()}”</h2><p>Check the spelling, try fewer words{filtered ? ' or change the filters' : ''}.</p></>
          : filtered ? <h2>No jobs match these filters</h2> : <h2>No jobs yet</h2>}
        <div className="cs-state-actions">
          {(searching || filtered) && <button type="button" className="cs-pillbtn" onClick={clear}>Clear</button>}
          {canEdit && !searching && !filtered && <button type="button" className="cs-btn-primary" onClick={() => setAdding(true)}>New job</button>}
        </div>
      </div>
    }
    return layout === 'table'
      ? <JobTable rows={matched} query={query} loading={false} newId={newId} onOpen={open} onEdit={canEdit ? setEditing : undefined} />
      : <JobCards rows={matched} query={query} newId={newId} onOpen={open} onEdit={canEdit ? setEditing : undefined} />
  }

  return (
    <div className="clients-simple jobs-simple cs-page">
      <header className="cs-head">
        <h1>Jobs</h1>
        {canEdit && <div className="cs-actions">
          <button type="button" className="cs-btn-primary" onClick={() => setAdding(true)}><Plus size={16} />New job</button>
        </div>}
      </header>

      <div className="cs-panel">
        <div className="cs-seg" role="group" aria-label="Status">
          {STATUS_FILTERS.map((item) => <button key={item.value || 'all'} type="button" className={filters.status === item.value ? 'is-active' : ''} aria-pressed={filters.status === item.value} onClick={() => patch({ status: item.value || null })}>{item.label}</button>)}
        </div>
        <label className="cs-search">
          <Search size={18} aria-hidden="true" />
          <input ref={searchBox} autoFocus type="search" value={text} onChange={(event) => setText(event.target.value)} maxLength={120} autoComplete="off" spellCheck={false} aria-keyshortcuts="/"
            placeholder="Search by job, client, country or status" aria-label="Search jobs" />
          {text && <button type="button" className="cs-search-clear" aria-label="Clear search" onClick={clearText}><X size={15} /></button>}
        </label>
        <div className="js-filter">
          <SearchSelect popover compact items={clientItems} value={filters.client} ariaLabel="Client" placeholder="All clients" searchPlaceholder="Type a company or contact name"
            allItem={{ key: '', label: 'All clients', search: ['all clients'] }} emptyText="No client has a job yet." onChange={(item) => patch({ client: item.key || null })} />
        </div>
        <div className="cs-layout" role="group" aria-label="View as">
          <button type="button" className={layout === 'cards' ? 'is-active' : ''} aria-pressed={layout === 'cards'} aria-label="Cards view" title="Cards" onClick={() => setLayout('cards')}><LayoutGrid size={17} /></button>
          <button type="button" className={layout === 'table' ? 'is-active' : ''} aria-pressed={layout === 'table'} aria-label="Table view" title="Table" onClick={() => setLayout('table')}><Table2 size={17} /></button>
        </div>
      </div>

      {listing && !loading && !loadError && count > 0 && (
        <div className="cs-resultbar">
          <span role="status" className="cs-count">{count} {count === 1 ? 'job' : 'jobs'}</span>
          <button type="button" className="cs-textbtn" onClick={clear}>Clear</button>
        </div>
      )}

      {body()}

      {adding && <OrderDialog onClose={() => setAdding(false)} onSaved={saved} />}
      {editing && <OrderDialog order={editing} onClose={() => setEditing(null)} />}
    </div>
  )
}
