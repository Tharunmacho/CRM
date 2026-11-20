import { useEffect, useId, useMemo, useRef, useState, type ReactNode } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { ArrowRight, FileSpreadsheet, LayoutGrid, Pencil, Plus, Search, Table2, Trash2, Users, X } from 'lucide-react'
import { useStore } from '@/shared/state/crm-store'
import type { Client, Company, ReferenceAgency } from '@/shared/model/types'
import { initials } from '@/shared/lib/format'
import { Button } from '@/shared/ui/primitives'
import { Dialog } from '@/shared/ui/overlay'
import { DeleteRecordDialog } from '@/shared/components/delete-dialog'
import { Panel } from '@/shared/ui/display'
import { DataTable, type Column } from '@/shared/ui/table'
import { ClientDialog } from '@/features/clients/components/client-dialog'
import { AgencyDialog } from '@/features/clients/components/agency-dialog'
import { AgencyImportDialog } from '@/features/clients/components/agency-import-dialog'
import { ActiveToggle } from '@/features/clients/components/active-toggle'
import { Avatar, CopyButton, hueOf } from '@/features/clients/components/parts'
import { agencyApi, useReferenceAgencies } from '@/features/clients/agency-api'
import { agencyDisplayName, agencyPayloadFor, firstName } from '@/features/clients/client-form'
import { activeFirst, agencyFilterParams, COUNTRY_OPTIONS, describeAgencyFilters, hasAgencyFilters, matchesAgencyFilters, NO_FILTERS, parseAgencyFilters, SPEC_OPTIONS, type ActivityFilter, type AgencyFilters } from '@/features/clients/agency-filters'
import { highlightPhone, highlightPieces, matchesSearch, type Searchable } from '@/features/clients/client-search'
import { useWorkspaceReady } from '@/features/master-data/use-workspace-ready'
import '@/styles/clients-simple.css'
import '@/styles/ops-tables.css'
import '@/styles/touch-targets.css'
import '@/styles/table-theme.css'

type Side = 'clients' | 'agencies'
type Layout = 'cards' | 'table'

const LAYOUT_KEY = 'crm-clients-layout'
const SEARCH_DELAY_MS = 150

/** The remembered choice. The table is the default (every result list opens as one); storage can be blocked, so access is guarded. */
function readLayout(): Layout {
  try { return localStorage.getItem(LAYOUT_KEY) === 'cards' ? 'cards' : 'table' } catch { return 'table' }
}

interface ClientRow {
  client: Client
  agencyName: string
  phone: string
  email: string
  address: string
  city: string
  country: string
  staff: { id: string; name: string }[]
  search: Searchable
}

interface AgencyRow {
  agency: ReferenceAgency
  search: Searchable
}

/** Text with the matched part of the search lit up. */
function Hi({ text, query }: { text?: string | null; query: string }) {
  if (!text) return null
  return <>{highlightPieces(text, query).map((piece, index) => piece.hit ? <mark key={index} className="cs-mark">{piece.text}</mark> : piece.text)}</>
}

function HiPhone({ text, query }: { text?: string | null; query: string }) {
  if (!text) return null
  return <>{highlightPhone(text, query).map((piece, index) => piece.hit ? <mark key={index} className="cs-mark">{piece.text}</mark> : piece.text)}</>
}

const Quiet = ({ children = 'Not added' }: { children?: string }) => <span className="cl-quiet">{children}</span>

/** Avatar chip and name, as in the old directory (soft per-name colour). The name can carry the search highlight; `avatar={false}` shows the name alone (agencies). */
function Identity({ name, query, avatar = true }: { name: string; query: string; avatar?: boolean }) {
  return <div className="record-identity">
    {avatar && <span className={`record-avatar avatar-hue-${hueOf(name)}`} aria-hidden="true">{initials(name)}</span>}
    <div className="min-w-0"><p className="record-name" title={name}><Hi text={name} query={query} /></p></div>
  </div>
}

/** Contact person, then their phone and email (with the copy button) under it, as the old table showed them. */
function ContactCell({ name, phone, email, query }: { name: string; phone: string; email: string; query: string }) {
  return <div className="cl-cell">
    {name && <b title={name}><Hi text={name} query={query} /></b>}
    {phone && <span className="cl-line ident"><a href={`tel:${phone.replace(/[^\d+]/g, '')}`} title={phone} onClick={(event) => event.stopPropagation()}><HiPhone text={phone} query={query} /></a></span>}
    {email && <span className="cl-line"><a href={`mailto:${email}`} title={email} onClick={(event) => event.stopPropagation()}><Hi text={email} query={query} /></a><CopyButton value={email} label={`Copy email of ${name}`} /></span>}
    
  </div>
}

/** The main place in bold and the finer detail under it (no flag: the Tasks table look). */
function Location({ primary, secondary, query }: { primary: string; secondary?: string; query: string }) {
  if (!primary && !secondary) return <Quiet />
  return <span className="cl-place is-stacked">
    <span className="cl-place-text">
      <b title={primary || secondary}><Hi text={primary || secondary} query={query} /></b>
      {primary && secondary && <small title={secondary}><Hi text={secondary} query={query} /></small>}
    </span>
  </span>
}

/** A client's place is its country and city (the client and company records carry no state). */
const clientPlace = (row: { country: string; city: string }) => ({ primary: row.country || row.city, secondary: row.country ? row.city : '' })

/** Overlapping small avatars with the first names beside them, or a muted line when nobody is allocated. */
function StaffStack({ staff, query }: { staff: { id: string; name: string }[]; query: string }) {
  if (staff.length === 0) return <span className="cl-quiet" aria-label="No recruiter assigned">—</span>
  const shown = staff.slice(0, 4)
  const more = staff.length - shown.length
  const label = staff.map((person) => person.name).join(', ')
  return <span className="cs-stack" title={label}>
    <span className="cs-stack-avs" role="img" aria-label={`Assigned recruiter: ${label}`}>
      {shown.map((person) => <Avatar key={person.id} name={person.name} size="sm" />)}
      {more > 0 && <span className="cs-more" aria-hidden="true">+{more}</span>}
    </span>
    <span className="cs-stack-names" aria-hidden="true">{staff.map((person, index) => <span key={person.id}>{index > 0 && ', '}<Hi text={firstName(person.name)} query={query} /></span>)}</span>
  </span>
}

/** Compact chips for a saved selection (specialisations, preferred countries); a dash when nothing is chosen. */
function ChipList({ values, label }: { values?: string[]; label: string }) {
  if (!values?.length) return <span className="cl-quiet" aria-label={`${label}: none`}>—</span>
  return <ul className="cs-tags" aria-label={label}>{values.map((value) => <li key={value} className="cs-tag" title={value}>{value}</li>)}</ul>
}

/** One card: avatar (clients only), name, contact person, a short fact list, then (clients) the allocated staff and the actions. */
function RecordCard({ title, contact, facts, staff, actions, avatar = true }: { title: string; contact: string; facts: { label: string; value: ReactNode }[]; staff?: ReactNode; actions: ReactNode; avatar?: boolean }) {
  return <article className="cs-card">
    <header>
      {avatar && <Avatar name={title} size="lg" />}
      <div className="cs-title"><h3 title={title}>{title}</h3>{contact && contact !== title && <p title={contact}>{contact}</p>}</div>
    </header>
    <dl className="cs-facts">{facts.map((fact) => <div key={fact.label}><dt>{fact.label}</dt><dd>{fact.value}</dd></div>)}</dl>
    {staff && <div className="cs-staff"><span className="cs-staff-label">Recruiter</span>{staff}</div>}
    <footer>{actions}</footer>
  </article>
}

const tel = (phone: string, query: string) => phone ? <a href={`tel:${phone.replace(/[^\d+]/g, '')}`} className="cs-tnum"><HiPhone text={phone} query={query} /></a> : <Quiet />
const mail = (email: string, name: string, query: string) => email ? <><a href={`mailto:${email}`} title={email}><Hi text={email} query={query} /></a><CopyButton value={email} label={`Copy email of ${name}`} /></> : <Quiet />
const place = (address: string, query: string) => address ? <span title={address}><Hi text={address} query={query} /></span> : <Quiet />

function ClientCards({ rows, query, onOpen, onEdit, onDelete }: { rows: ClientRow[]; query: string; onOpen: (c: Client) => void; onEdit?: (c: Client) => void; onDelete?: (c: Client) => void }) {
  return <div className="cs-grid">{rows.map((row) => {
    const name = row.agencyName || row.client.name
    return <RecordCard key={row.client.id} title={name} contact={row.client.name}
      facts={[
        { label: 'Contact no', value: tel(row.phone, query) },
        { label: 'Email', value: mail(row.email, row.client.name, query) },
        { label: 'Address', value: place(row.address, query) },
        { label: 'Location', value: <Location {...clientPlace(row)} query={query} /> },
      ]}
      staff={<StaffStack staff={row.staff} query={query} />}
      actions={<>
        <button type="button" className="cl-open cs-wide" onClick={() => onOpen(row.client)} aria-label={`Open ${name}`}>Open<ArrowRight size={14} aria-hidden="true" /></button>
        {onEdit && <button type="button" className="cl-open cs-wide" onClick={() => onEdit(row.client)} aria-label={`Edit ${name}`}><Pencil size={13} aria-hidden="true" />Edit</button>}
        {onDelete && <button type="button" className="cl-open cs-wide is-danger" onClick={() => onDelete(row.client)} aria-label={`Delete ${name}`}><Trash2 size={13} aria-hidden="true" />Delete</button>}
      </>} />
  })}</div>
}

/** What each agency's Active switch needs: whether a save is under way and why the last one failed. */
interface ActiveControl { onToggle?: (agency: ReferenceAgency, next: boolean) => void; busy: (id: string) => boolean; error: (id: string) => string }
const toggleOf = (control: ActiveControl | undefined, agency: ReferenceAgency) => <ActiveToggle agencyName={agencyDisplayName(agency)} active={agency.activeAgency === true}
  busy={control?.busy(agency.id)} error={control?.error(agency.id)} onToggle={control?.onToggle ? (next) => control.onToggle?.(agency, next) : undefined} />

function AgencyCards({ rows, query, active, onEdit, onDelete }: { rows: AgencyRow[]; query: string; active?: ActiveControl; onEdit?: (a: ReferenceAgency) => void; onDelete?: (a: ReferenceAgency) => void }) {
  return <div className="cs-grid">{rows.map(({ agency }) => <RecordCard key={agency.id} avatar={false} title={agencyDisplayName(agency)} contact={agency.name}
    facts={[
      { label: 'Contact no', value: tel(agency.phone ?? '', query) },
      { label: 'Email', value: mail(agency.email ?? '', agency.name, query) },
      { label: 'Address', value: place(agency.address ?? '', query) },
      { label: 'Place / City', value: <Location primary={agency.place ?? ''} secondary={agency.state} query={query} /> },
      { label: 'Specialisation', value: <ChipList values={agency.specialisations} label="Specialisation" /> },
      { label: 'Preferred country', value: <ChipList values={agency.preferredCountries} label="Preferred country" /> },
      { label: 'Active', value: toggleOf(active, agency) },
    ]}
    actions={<>
      {onEdit && <button type="button" className="cl-open cs-wide" onClick={() => onEdit(agency)} aria-label={`Edit ${agencyDisplayName(agency)}`}><Pencil size={13} aria-hidden="true" />Edit</button>}
      {onDelete && <button type="button" className="cl-open cs-wide is-danger" onClick={() => onDelete(agency)} aria-label={`Delete ${agencyDisplayName(agency)}`}><Trash2 size={13} aria-hidden="true" />Delete</button>}
    </>} />)}</div>
}

/** The tables use the shared DataTable (the old directory's look and column alignment); on a phone it stacks into labelled rows. */
function ClientTable({ rows, query, loading, onOpen, onEdit, onDelete }: { rows: ClientRow[]; query: string; loading: boolean; onOpen: (c: Client) => void; onEdit?: (c: Client) => void; onDelete?: (c: Client) => void }) {
  const columns: Column<ClientRow>[] = [
    { key: 'agency', header: 'Company', width: '24%', render: (row) => <Identity name={row.agencyName || row.client.name} query={query} /> },
    { key: 'contact', header: 'Contact person', width: '26%', align: 'left', render: (row) => <ContactCell name={row.client.name} phone={row.phone} email={row.email} query={query} /> },
    { key: 'location', header: 'Location', width: '14%', align: 'left', render: (row) => <Location {...clientPlace(row)} query={query} /> },
    { key: 'staff', header: 'Recruiter', width: '15%', align: 'left', render: (row) => <StaffStack staff={row.staff} query={query} /> },
    {
      key: 'actions', header: 'Actions', actions: true,
      render: (row) => <span className="cs-row-actions">
        {onEdit && <button type="button" className="cs-icon-act" onClick={(event) => { event.stopPropagation(); onEdit(row.client) }} aria-label={`Edit ${row.agencyName || row.client.name}`} title="Edit"><Pencil size={14} aria-hidden="true" /></button>}
        {onDelete && <button type="button" className="cs-icon-act is-danger" onClick={(event) => { event.stopPropagation(); onDelete(row.client) }} aria-label={`Delete ${row.agencyName || row.client.name}`} title="Delete"><Trash2 size={14} aria-hidden="true" /></button>}
        <button type="button" className="cl-open" onClick={(event) => { event.stopPropagation(); onOpen(row.client) }} aria-label={`Open ${row.agencyName || row.client.name}`}>Open<ArrowRight size={14} aria-hidden="true" /></button>
      </span>,
    },
  ]
  return <Panel flush className="tb-blue ops-fit ops-clients"><DataTable compact loading={loading} resetPageKey={query} rows={rows} columns={columns} rowKey={(row) => row.client.id} onRowClick={(row) => onOpen(row.client)} /></Panel>
}

/**
 * The agencies table: Agency (the name alone) | Contact | Location | Specialisation | Country | Actions | Active. The header is the Tasks table's
 * blue band (clients-simple.css); the Active column is the last one and saves at once.
 */
function AgencyTable({ rows, query, pageKey = query, loading, active, onEdit, onDelete }: { rows: AgencyRow[]; query: string; pageKey?: string; loading: boolean; active?: ActiveControl; onEdit?: (a: ReferenceAgency) => void; onDelete?: (a: ReferenceAgency) => void }) {
  const empty = <span className="cl-quiet" aria-label="Not added">—</span>
  const columns: Column<AgencyRow>[] = [
    { key: 'agency', header: 'Agency', width: '14%', render: ({ agency }) => <Identity avatar={false} name={agencyDisplayName(agency)} query={query} /> },
    { key: 'contact', header: 'Contact', width: '16%', align: 'left', render: ({ agency }) => <ContactCell name={agency.name === agency.agencyName ? '' : agency.name} phone={agency.phone ?? ''} email={agency.email ?? ''} query={query} /> },
    { key: 'location', header: 'Location', width: '11%', align: 'left', render: ({ agency }) => agency.place || agency.state || agency.address
      ? <Location primary={[agency.place, agency.state].filter(Boolean).join(', ')} secondary={agency.address} query={query} /> : empty },
    { key: 'specialisation', header: 'Specialisation', width: '16%', align: 'left', render: ({ agency }) => <ChipList values={agency.specialisations} label="Specialisation" /> },
    { key: 'country', header: 'Country', width: '9%', align: 'left', priority: 'low', render: ({ agency }) => <ChipList values={agency.preferredCountries} label="Preferred country" /> },
    {
      key: 'actions', header: 'Actions', actions: true, pin: 'none', align: 'left',
      render: ({ agency }) => <span className="cs-row-actions">
        {onEdit && <button type="button" className="cl-open" onClick={() => onEdit(agency)} aria-label={`Edit ${agencyDisplayName(agency)}`}><Pencil size={13} aria-hidden="true" />Edit</button>}
        {onDelete && <button type="button" className="cs-icon-act is-danger" onClick={() => onDelete(agency)} aria-label={`Delete ${agencyDisplayName(agency)}`} title="Delete"><Trash2 size={14} aria-hidden="true" /></button>}
      </span>,
    },
    { key: 'active', header: 'Active', width: '19%', align: 'left', pin: 'none', render: ({ agency }) => toggleOf(active, agency) },
  ]
  return <Panel flush className="cs-agency-table tb-blue ops-fit ops-agencies"><DataTable compact stackBelow={1024} loading={loading} resetPageKey={pageKey} rows={rows} columns={columns} rowKey={({ agency }) => agency.id} /></Panel>
}

function Skeleton({ layout, side }: { layout: Layout; side: Side }) {
  if (layout === 'table') return side === 'clients'
    ? <ClientTable rows={[]} query="" loading onOpen={() => undefined} />
    : <AgencyTable rows={[]} query="" loading />
  return <div className="cs-grid" role="status" aria-label="Loading">{[0, 1, 2].map((i) => <div key={i} className="cs-card cs-skel" />)}</div>
}

/** A labelled native select (keyboard and screen-reader friendly by default). */
function FilterSelect({ label, value, options, onChange, describedBy }: { label: string; value: string; options: readonly { value: string; label: string }[]; onChange: (value: string) => void; describedBy?: string }) {
  const id = useId()
  return <div className="cs-filter">
    <label htmlFor={id}>{label}</label>
    <select id={id} value={value} onChange={(event) => onChange(event.target.value)} aria-describedby={describedBy}>
      <option value="">All</option>
      {options.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
    </select>
  </div>
}

const ACTIVITY_OPTIONS: readonly { value: ActivityFilter; label: string }[] = [{ value: 'active', label: 'Active' }, { value: 'inactive', label: 'Not active' }]
const SECTOR_NOTE = 'Agencies tagged All sectors serve every sector, so they also appear under each sector.'

/** Specialisation, Preferred country and Activity. They combine with each other and with the search box; the address keeps them. */
function AgencyFilterBar({ filters, onChange, onClear }: { filters: AgencyFilters; onChange: (changes: Partial<AgencyFilters>) => void; onClear: () => void }) {
  const note = useId()
  return <div className="cs-filterbar" role="group" aria-label="Filter agencies">
    <FilterSelect label="Specialisation" value={filters.spec} options={SPEC_OPTIONS} describedBy={note} onChange={(spec) => onChange({ spec })} />
    <FilterSelect label="Preferred country" value={filters.country} options={COUNTRY_OPTIONS} onChange={(country) => onChange({ country })} />
    <FilterSelect label="Activity" value={filters.activity} options={ACTIVITY_OPTIONS} onChange={(activity) => onChange({ activity: activity as ActivityFilter })} />
    {hasAgencyFilters(filters) && <button type="button" className="cs-textbtn cs-filter-clear" onClick={onClear}>Clear filters</button>}
    <p id={note} className="cs-filter-note">{SECTOR_NOTE}</p>
  </div>
}

/** A 404 from /reference-agencies means the server has not been updated yet; say so calmly instead of printing "Not Found". */
const looksOutdated = (message: string) => /not found|404/i.test(message)

/**
 * Clients and reference agencies. Search first: nothing is listed until the person types or presses "Show all". The search text,
 * the side and "show all" live in the address (?q=, ?view=, ?all=1) so Back from a client returns to the same results.
 */
export default function ClientsPage() {
  const store = useStore()
  const snapshot = useWorkspaceReady('clients')
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()
  const side: Side = params.get('view') === 'agencies' ? 'agencies' : 'clients'
  const query = params.get('q') ?? ''
  const showAll = params.get('all') === '1'
  const filters = useMemo(() => parseAgencyFilters(params), [params])
  const filtering = side === 'agencies' && hasAgencyFilters(filters)
  const filterText = filtering ? describeAgencyFilters(filters) : ''
  const [text, setText] = useState(query)
  const [layout, setLayoutState] = useState<Layout>(readLayout)
  const [adding, setAdding] = useState(false)
  const [editing, setEditing] = useState<Client | null>(null)
  const [deletingClient, setDeletingClient] = useState<Client | null>(null)
  const [addingAgency, setAddingAgency] = useState(false)
  const [importingAgencies, setImportingAgencies] = useState(false)
  const [editingAgency, setEditingAgency] = useState<ReferenceAgency | null>(null)
  const [deleting, setDeleting] = useState<ReferenceAgency | null>(null)
  const [deleteBusy, setDeleteBusy] = useState(false)
  const [deleteError, setDeleteError] = useState('')
  const searchBox = useRef<HTMLInputElement>(null)
  const agencies = useReferenceAgencies(side === 'agencies')
  // Same roles that may create clients may change them; everyone else reads.
  const canEdit = store.can('manage_records')
  // Deleting a client is for the supervisors who may delete any master record; the server enforces the same rule.
  const canDelete = store.can('delete_records')

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

  const companyOf = useMemo(() => {
    const first = new Map<string, Company>()
    for (const company of store.companies) if (!first.has(company.clientId)) first.set(company.clientId, company)
    return first
  }, [store.companies])

  // The directory is stored oldest first, so the newest clients come last; show them first.
  const clientRows = useMemo<ClientRow[]>(() => [...store.clients].reverse().map((client) => {
    const company = companyOf.get(client.id)
    const staff = (client.staffIds ?? []).map((id) => ({ id, name: store.user(id)?.name ?? '' })).filter((person) => person.name)
    const row = {
      client, agencyName: company?.name ?? '', phone: client.phone ?? company?.phone ?? '', email: client.email ?? company?.contactEmail ?? '',
      address: company?.address ?? '', city: client.city || company?.city || '', country: client.country || company?.country || '', staff,
    }
    return { ...row, search: { name: client.name, agencyName: row.agencyName, email: row.email, address: [row.address, row.city, row.country].filter(Boolean).join(' '), phone: row.phone, staffNames: staff.map((p) => p.name), staffContacts: client.staffContacts } }
  // store.user is rebuilt with the user list
  }), [store.clients, store.users, companyOf]) // eslint-disable-line react-hooks/exhaustive-deps

  // The Active switch in the list saves at once. The row shows the new mark straight away (`pending`) and moves to its place; a refused
  // save puts the old mark back and says why under the switch.
  const [pending, setPending] = useState<Record<string, boolean>>({})
  const [savingActive, setSavingActive] = useState<Record<string, boolean>>({})
  const [activeError, setActiveError] = useState<Record<string, string>>({})
  const without = <T,>(record: Record<string, T>, id: string) => { const { [id]: _gone, ...rest } = record; return rest }
  // Once the refreshed list agrees with a choice it no longer needs to be held here.
  useEffect(() => {
    if (!agencies.items) return
    setPending((current) => {
      const next = { ...current }
      for (const agency of agencies.items ?? []) if (agency.id in next && (agency.activeAgency === true) === next[agency.id]) delete next[agency.id]
      return Object.keys(next).length === Object.keys(current).length ? current : next
    })
  }, [agencies.items])
  const toggleActive = async (agency: ReferenceAgency, next: boolean) => {
    if (savingActive[agency.id]) return
    setPending((current) => ({ ...current, [agency.id]: next }))
    setSavingActive((current) => ({ ...current, [agency.id]: true }))
    setActiveError((current) => without(current, agency.id))
    try { await agencyApi.update(agency.id, agencyPayloadFor(agency, next)); agencies.refresh() }
    catch (err) {
      setPending((current) => without(current, agency.id))
      setActiveError((current) => ({ ...current, [agency.id]: err instanceof Error && err.message ? err.message : 'Not saved. Please try again.' }))
    } finally { setSavingActive((current) => without(current, agency.id)) }
  }
  const activeControl: ActiveControl = { onToggle: canEdit ? (agency, next) => void toggleActive(agency, next) : undefined, busy: (id) => savingActive[id] === true, error: (id) => activeError[id] ?? '' }

  // Active agencies first, the others last; the server already lists them that way, and the order is kept here for any other source and after a switch.
  const agencyRows = useMemo<AgencyRow[]>(() => activeFirst((agencies.items ?? []).map((agency) => agency.id in pending ? { ...agency, activeAgency: pending[agency.id] } : agency)).map((agency) => ({
    agency,
    search: { name: agency.name, agencyName: agencyDisplayName(agency), email: agency.email, address: [agency.place, agency.address].filter(Boolean).join(' '), state: agency.state, phone: agency.phone, staffContacts: agency.staffContacts,
      tags: [...(agency.specialisations ?? []), ...(agency.preferredCountries ?? [])] },
  })), [agencies.items, pending])

  const searching = query.trim().length > 0
  const everyone = side === 'clients' ? clientRows : agencyRows
  const matched = useMemo(() => {
    if (!searching && !showAll && !filtering) return []
    const found = (everyone as { search: Searchable }[]).filter((row) => matchesSearch(row.search, query))
    return side === 'agencies' ? (found as AgencyRow[]).filter((row) => matchesAgencyFilters(row.agency, filters)) : found
  }, [everyone, searching, showAll, filtering, filters, side, query])
  const total = everyone.length
  const noun = side === 'clients' ? 'client' : 'agency'
  const nouns = side === 'clients' ? 'clients' : 'agencies'
  const loading = side === 'clients' ? !snapshot.ready && !snapshot.error : agencies.items === null && !agencies.error
  const loadError = side === 'clients' ? (!snapshot.ready ? snapshot.error : '') : (agencies.items === null ? agencies.error : '')
  const retry = side === 'clients' ? snapshot.retry : agencies.refresh

  const clear = () => { setText(''); patch({ q: null, all: null, ...agencyFilterParams(NO_FILTERS) }); searchBox.current?.focus() }
  const changeFilters = (changes: Partial<AgencyFilters>) => patch(agencyFilterParams({ ...filters, ...changes }))
  const clearFilters = () => patch(agencyFilterParams(NO_FILTERS))
  const choose = (next: Side) => patch({ view: next === 'agencies' ? 'agencies' : null })

  const confirmDelete = async () => {
    if (!deleting || deleteBusy) return
    setDeleteBusy(true); setDeleteError('')
    try {
      await agencyApi.remove(deleting.id)
      store.notify('Agency deleted')
      agencies.refresh()
      setDeleting(null)
    } catch (err) { setDeleteError(err instanceof Error ? err.message : 'We could not delete this agency. Please try again.') }
    finally { setDeleteBusy(false) }
  }

  const count = matched.length
  const body = () => {
    if (loadError) {
      const outdated = side === 'agencies' && looksOutdated(loadError)
      return <div role="alert" className="cs-state">
        <span className="cs-badge" aria-hidden="true"><Users size={24} strokeWidth={1.7} /></span>
        <h2>{outdated ? 'Agencies need the latest server update' : `We could not load the ${nouns}`}</h2>
        <p>{outdated ? 'Restart the CRM server, then press Try again.' : side === 'agencies' ? 'Check your connection, then press Try again.' : loadError}</p>
        <Button onClick={retry}>Try again</Button>
      </div>
    }
    if (loading) return <Skeleton layout={layout} side={side} />
    if (!searching && !showAll && !filtering) {
      return <div className="cs-state cs-hero">
        <span className="cs-badge" aria-hidden="true"><Search size={26} strokeWidth={1.7} /></span>
        <h2>{side === 'clients' ? 'Search to find a client' : 'Search to find an agency'}</h2>
        {total > 0
          ? <button type="button" className="cs-pillbtn" onClick={() => patch({ all: '1' })}>Show all {total} {total === 1 ? noun : nouns}<ArrowRight size={15} aria-hidden="true" /></button>
          : <p>No {nouns} saved yet.</p>}
      </div>
    }
    if (count === 0) {
      return <div className="cs-state" role="status">
        <span className="cs-badge" aria-hidden="true"><Search size={24} strokeWidth={1.7} /></span>
        {filtering
          ? <><h2>No {nouns} match these filters</h2><p>{searching ? `Nothing for “${query.trim()}” with ${filterText}.` : `No agency is saved for ${filterText}.`}</p></>
          : searching
            ? <><h2>No {noun} matches “{query.trim()}”</h2><p>Check the spelling or try fewer words.</p></>
            : <><h2>No {nouns} yet</h2><p>{side === 'clients' ? 'Add your first client to see it here.' : 'Add your first agency to see it here.'}</p></>}
        <div className="cs-state-actions">
          {filtering && <button type="button" className="cs-pillbtn" onClick={clearFilters}>Clear filters</button>}
          {searching && <button type="button" className="cs-pillbtn" onClick={() => { setText(''); patch({ q: null }); searchBox.current?.focus() }}>Clear search</button>}
          {canEdit && !searching && !filtering && <button type="button" className="cs-btn-primary" onClick={() => side === 'clients' ? setAdding(true) : setAddingAgency(true)}>{side === 'clients' ? 'New client' : 'Add agency'}</button>}
        </div>
      </div>
    }
    if (side === 'clients') {
      const rows = matched as ClientRow[]
      const open = (client: Client) => navigate(`/clients/${client.id}`)
      return layout === 'table'
        ? <ClientTable rows={rows} query={query} loading={false} onOpen={open} onEdit={canEdit ? setEditing : undefined} onDelete={canDelete ? setDeletingClient : undefined} />
        : <ClientCards rows={rows} query={query} onOpen={open} onEdit={canEdit ? setEditing : undefined} onDelete={canDelete ? setDeletingClient : undefined} />
    }
    const rows = matched as AgencyRow[]
    const props = { rows, query, active: activeControl, onEdit: canEdit ? setEditingAgency : undefined, onDelete: canEdit ? setDeleting : undefined }
    return layout === 'table' ? <AgencyTable {...props} pageKey={`${query}|${filters.spec}|${filters.country}|${filters.activity}`} loading={false} /> : <AgencyCards {...props} />
  }

  return (
    <div className="clients-simple cs-page">
      <header className="cs-head">
        <h1>{side === 'agencies' ? 'Agencies' : 'Clients'}</h1>
        {canEdit && <div className="cs-actions">
          {side === 'clients' && <button type="button" className="cs-btn-primary" onClick={() => setAdding(true)}><Plus size={16} />New client</button>}
          {side === 'agencies' && <button type="button" className="cs-btn-import" onClick={() => setImportingAgencies(true)}><FileSpreadsheet size={16} />Import Excel / CSV</button>}
          <button type="button" className="cs-btn-green" onClick={() => setAddingAgency(true)}><Plus size={16} />New agency</button>
        </div>}
      </header>

      <div className="cs-panel">
        <div className="cs-seg" role="group" aria-label="Show">
          <button type="button" className={side === 'clients' ? 'is-active' : ''} aria-pressed={side === 'clients'} onClick={() => choose('clients')}>Clients</button>
          <button type="button" className={side === 'agencies' ? 'is-active' : ''} aria-pressed={side === 'agencies'} onClick={() => choose('agencies')}>Agencies</button>
        </div>
        <label className="cs-search">
          <Search size={18} aria-hidden="true" />
          <input ref={searchBox} autoFocus type="search" value={text} onChange={(event) => setText(event.target.value)} maxLength={120} autoComplete="off" spellCheck={false} aria-keyshortcuts="/"
            placeholder={side === 'clients' ? 'Search by name, company, phone, email or recruiter' : 'Search by agency, name, phone, place or recruiter'} aria-label={`Search ${nouns}`} />
          {text && <button type="button" className="cs-search-clear" aria-label="Clear search" onClick={clear}><X size={15} /></button>}
        </label>
        <div className="cs-layout" role="group" aria-label="View as">
          <button type="button" className={layout === 'cards' ? 'is-active' : ''} aria-pressed={layout === 'cards'} aria-label="Cards view" title="Cards" onClick={() => setLayout('cards')}><LayoutGrid size={17} /></button>
          <button type="button" className={layout === 'table' ? 'is-active' : ''} aria-pressed={layout === 'table'} aria-label="Table view" title="Table" onClick={() => setLayout('table')}><Table2 size={17} /></button>
        </div>
      </div>

      {side === 'agencies' && !loadError && <AgencyFilterBar filters={filters} onChange={changeFilters} onClear={clearFilters} />}

      {(searching || showAll || filtering) && !loading && !loadError && count > 0 && (
        <div className="cs-resultbar">
          <span role="status" className="cs-count">{count} {count === 1 ? 'result' : 'results'}{filtering ? ` · ${filterText}` : !searching && showAll ? ` · all ${nouns}` : ''}</span>
          <button type="button" className="cs-textbtn" onClick={clear}>Clear</button>
        </div>
      )}

      {body()}

      {adding && <ClientDialog onClose={() => setAdding(false)} />}
      {editing && <ClientDialog client={editing} onClose={() => setEditing(null)} />}
      {deletingClient && <DeleteRecordDialog kind="client" id={deletingClient.id} open onClose={() => setDeletingClient(null)} onEdit={() => setEditing(deletingClient)} />}
      {addingAgency && <AgencyDialog onClose={() => setAddingAgency(false)} onSaved={agencies.refresh} />}
      {editingAgency && <AgencyDialog agency={editingAgency} onClose={() => setEditingAgency(null)} onSaved={agencies.refresh} />}
      {importingAgencies && <AgencyImportDialog onClose={() => setImportingAgencies(false)} onImported={() => { agencies.refresh(); setText(''); patch({ view: 'agencies', all: '1', q: null }) }} />}
      {deleting && <Dialog open onClose={() => { if (!deleteBusy) setDeleting(null) }} width="sm" title="Delete agency"
        footer={<><Button variant="ghost" disabled={deleteBusy} onClick={() => setDeleting(null)}>Cancel</Button><Button variant="danger" disabled={deleteBusy} onClick={() => void confirmDelete()}>{deleteBusy ? 'Deleting…' : 'Delete'}</Button></>}>
        {deleteError && <p role="alert" className="client-form-error">{deleteError}</p>}
        <p>Delete <b>{agencyDisplayName(deleting)}</b> from the agencies? Candidates already recorded with this agency keep their text.</p>
      </Dialog>}
    </div>
  )
}
