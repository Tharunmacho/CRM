import { useEffect, useRef, useState, type ReactNode } from 'react'
import { AlertCircle, Building2, Check, ChevronDown, ChevronRight, Download, FileSpreadsheet, Globe2, Loader2, Plane, RefreshCw, Search, SlidersHorizontal, Users, X } from 'lucide-react'
import { Link, useSearchParams } from 'react-router-dom'
import { useStore } from '@/shared/state/crm-store'
import { Button, Input, Select } from '@/shared/ui/primitives'
import { EmptyState, PageHeader, StatusPill } from '@/shared/ui/display'
import { SummaryCards } from '@/shared/ui/workspace'
import { SnapshotState } from '@/shared/ui/page-loading'
import { cn } from '@/shared/lib/cn'
import { Pager } from '@/features/candidates/components/db-parts'
import { readPageSize, savePageSize } from '@/features/candidates/pager-model'
import { useMediaQuery } from '@/features/application-form/use-media-query'
import { downloadTravelled, useTravelled } from '@/features/travelled/travelled-api'
import {
  EMPTY_FILTERS, MONTH_NAMES, PAGE_SIZE_CHOICES, activeChips, agencyToday, barPercent, choosePeriod, chooseTravelDate, clearChip, dateBarLabel, dateLevel,
  dateSummary, documentsLabel, documentsTitle, drillTo, filterCount, filtersFromParams, formatTravelDay, monthName, moreFilterCount, pageRows, periodValue, queryFor, quickRanges,
  serialNumber, setFilter, startingPageSize, toggleFilter, toggleIncomplete, withChosen,
  type PeriodValue, type TravelledFilterKey, type TravelledFilters, type TravelledResult, type TravelledRow,
} from '@/features/travelled/travelled-model'
import '@/styles/data-bank-v2.css'
import '@/styles/travelled.css'
import '@/styles/touch-targets.css'
import { docToText } from '@/screens/tasks/doc-input'

const SHOWN = 6
const BREAKDOWN_KEY = 'crm-travelled-breakdown'
/** The phone layout: the toolbar is the search box and one Filters button, the rest lives in a bottom sheet. */
const PHONE = '(max-width: 768px)'

type Options = TravelledResult['options']
const NO_OPTIONS: Options = { years: [], months: [], undated: 0, clients: [], agencies: [], countries: [], orders: [] }

const readBreakdownOpen = () => { try { return window.localStorage.getItem(BREAKDOWN_KEY) === 'open' } catch { return false } }
const saveBreakdownOpen = (open: boolean) => { try { window.localStorage.setItem(BREAKDOWN_KEY, open ? 'open' : 'closed') } catch { /* the choice lasts until the page closes */ } }

/**
 * Who has travelled, when, for which client and through which agency. Every choice is in the address
 * (?year=2026&month=05&client=...), so a view can be bookmarked or sent to someone. The table comes first: one slim line of totals above it,
 * one toolbar row (search, Period, Client, Country, Agency, More filters) and, only when a filter is on, a row of chips. The breakdowns are
 * folded away until asked for; clicking a bar narrows the list to it, clicking it again widens it.
 */
export default function TravelledPage() {
  const store = useStore()
  const [params, setParams] = useSearchParams()
  const filters = filtersFromParams(params)
  const apply = (next: TravelledFilters, replace = false) => setParams(new URLSearchParams(queryFor(next)), { replace })
  const phone = useMediaQuery(PHONE)

  const { data: fresh, error, refresh, refreshing } = useTravelled(filters, store.me.id)
  // Keep the previous list on screen (dimmed) while a new choice loads, so the page does not jump.
  const last = useRef<{ user: string; data: TravelledResult } | null>(null)
  if (fresh) last.current = { user: store.me.id, data: fresh }
  const data = fresh ?? (last.current?.user === store.me.id ? last.current.data : null)
  const stale = !fresh && !!data

  // Options describe every visible placement, independently of the filters. An empty answer clears old options too.
  const options = data?.options ?? NO_OPTIONS
  const filterQuery = queryFor(filters)

  // The search box types freely and moves the address a moment after typing stops.
  const [search, setSearch] = useState(filters.q)
  useEffect(() => { setSearch(filters.q) }, [filters.q])
  useEffect(() => {
    const wanted = search.replace(/\s+/g, ' ').trim()
    if (wanted === filters.q) return undefined
    const timer = window.setTimeout(() => apply(setFilter(filters, 'q', wanted), true), 300)
    return () => window.clearTimeout(timer)
  }, [search, filterQuery]) // eslint-disable-line react-hooks/exhaustive-deps

  const clearAll = () => { setSearch(''); apply(EMPTY_FILTERS) }

  const [exporting, setExporting] = useState<'' | 'xlsx' | 'csv'>('')
  const [exportError, setExportError] = useState('')
  const [lastExport, setLastExport] = useState<'xlsx' | 'csv'>('xlsx')
  async function exportFile(format: 'xlsx' | 'csv') {
    setExporting(format); setLastExport(format); setExportError('')
    try { await downloadTravelled(filters, format) }
    catch (failure) { setExportError(failure instanceof Error ? failure.message : 'We could not create the file. Please try again.') }
    finally { setExporting('') }
  }

  const chips = activeChips(filters)
  const set = (key: TravelledFilterKey, value: string) => apply(setFilter(filters, key, value))
  const orders = (options.orders ?? []).filter(order => !filters.client || order.client === filters.client || order.id === filters.order)
  const today = agencyToday()
  const years = withChosen([...new Set([today.slice(0, 4), ...options.years.map(String)])].sort().reverse(), filters.year)
  const otherYears = Array.from({ length: 111 }, (_, index) => String(2100 - index)).filter(year => !years.includes(year))
  const period = periodValue(filters, today)
  const travelDate = filters.year && filters.month && filters.day ? `${filters.year}-${filters.month}-${filters.day}` : ''
  const countries = data ? data.summary.byCountry.filter(item => item.country !== 'Not recorded').length : 0
  const agencies = data ? data.summary.byAgency.filter(item => item.agency !== 'Not recorded').length : 0
  const busy = refreshing || (stale && !error)

  // More filters (desktop popover, phone bottom sheet) and the folded breakdown.
  const [moreOpen, setMoreOpen] = useState(false)
  const moreButton = useRef<HTMLButtonElement>(null)
  const morePanel = useRef<HTMLDivElement>(null)
  const [breakdownOpen, setBreakdownOpen] = useState(readBreakdownOpen)
  const toggleBreakdown = () => setBreakdownOpen(open => { saveBreakdownOpen(!open); return !open })
  const badge = phone ? filterCount(filters) : moreFilterCount(filters, today)
  useEffect(() => {
    if (!moreOpen) return undefined
    const inside = (target: EventTarget | null) => target instanceof Element && !!(morePanel.current?.contains(target) || moreButton.current?.contains(target) || target.closest('.custom-select-popup'))
    const outside = (event: PointerEvent) => { if (!inside(event.target)) setMoreOpen(false) }
    const key = (event: KeyboardEvent) => {
      if (event.key !== 'Escape' || document.querySelector('.custom-select-popup')) return
      event.preventDefault(); setMoreOpen(false); moreButton.current?.focus()
    }
    const frame = requestAnimationFrame(() => Array.from(morePanel.current?.querySelectorAll<HTMLElement>('button:not([disabled]), input:not([disabled]), [tabindex="0"]') ?? []).find(item => item.getClientRects().length > 0)?.focus({ preventScroll: true }))
    document.addEventListener('pointerdown', outside)
    document.addEventListener('keydown', key)
    return () => { cancelAnimationFrame(frame); document.removeEventListener('pointerdown', outside); document.removeEventListener('keydown', key) }
  }, [moreOpen])
  useEffect(() => { if (!phone) return undefined; document.body.classList.toggle('tv-sheet-open', moreOpen); return () => document.body.classList.remove('tv-sheet-open') }, [phone, moreOpen])

  // The list is read whole; pages are slices of it (no new request when the page or the size changes).
  const [pageSize, setPageSize] = useState(() => startingPageSize(readPageSize('travelled', PAGE_SIZE_CHOICES), phone))
  const [page, setPage] = useState(0)
  useEffect(() => { setPage(0) }, [filterQuery])

  const periodField = <Select aria-label="Period" value={period} onChange={event => {
    const value = event.target.value as PeriodValue
    if (value === 'custom') setMoreOpen(true); else apply(choosePeriod(filters, value, today))
  }}>
    <option value="all">All dates</option>
    {quickRanges(today).map(range => <option key={range.key} value={range.key}>{range.label}</option>)}
    <option value="undated">Date not recorded{options.undated ? ` (${options.undated})` : ''}</option>
    <option value="custom">{period === 'custom' ? `Custom: ${dateSummary(filters)}` : 'Custom…'}</option>
  </Select>
  const clientField = <Select aria-label="Client" value={filters.client} onChange={event => set('client', event.target.value)}><option value="">All clients</option>{withChosen(options.clients, filters.client).map(name => <option key={name} value={name}>{name}</option>)}</Select>
  const countryField = <Select aria-label="Country" value={filters.country} onChange={event => set('country', event.target.value)}><option value="">All countries</option>{withChosen(options.countries, filters.country).map(name => <option key={name} value={name}>{name}</option>)}</Select>
  const agencyField = <Select aria-label="Agency" value={filters.agency} onChange={event => set('agency', event.target.value)}><option value="">All agencies</option>{withChosen(options.agencies, filters.agency).map(name => <option key={name} value={name}>{name}</option>)}</Select>

  return <div className="tv" aria-busy={busy}>
    <PageHeader title="Travelled" actions={<>
      <Button size="sm" icon={<RefreshCw size={14} className={refreshing ? 'animate-spin' : undefined} />} disabled={refreshing} onClick={refresh}>{refreshing ? 'Refreshing' : 'Refresh'}</Button>
      <Button size="sm" disabled={!data || !!exporting} icon={exporting === 'xlsx' ? <Loader2 size={14} className="animate-spin" /> : <FileSpreadsheet size={14} />} onClick={() => void exportFile('xlsx')}>{exporting === 'xlsx' ? 'Preparing Excel…' : 'Export Excel'}</Button>
      <Button size="sm" disabled={!data || !!exporting} icon={exporting === 'csv' ? <Loader2 size={14} className="animate-spin" /> : <Download size={14} />} onClick={() => void exportFile('csv')}>{exporting === 'csv' ? 'Preparing CSV…' : 'Export CSV'}</Button>
    </>}>
    </PageHeader>
    {data && <SummaryCards items={[
      { label: 'Travelled', value: data.total.toLocaleString(), detail: data.total === 1 ? 'candidate deployed' : 'candidates deployed', icon: Plane, tone: 'green' },
      { label: data.summary.byClient.length === 1 ? 'Client' : 'Clients', value: data.summary.byClient.length.toLocaleString(), detail: 'employers they flew to', icon: Building2 },
      { label: countries === 1 ? 'Country' : 'Countries', value: countries.toLocaleString(), detail: 'where they went', icon: Globe2 },
      { label: agencies === 1 ? 'Agency' : 'Agencies', value: agencies.toLocaleString(), detail: 'recorded referral agencies', icon: Users },
    ]} />}

    <section className="tv-toolbar" aria-label="Filters">
      <label className="tv-search">
        <Search size={16} aria-hidden="true" />
        <input type="search" aria-label="Search candidate, client or job" placeholder="Search" value={search} onChange={event => setSearch(event.target.value)} />
      </label>
      {!phone && <>
        <div className="tv-sel">{periodField}</div>
        <div className="tv-sel">{clientField}</div>
        <div className="tv-sel">{countryField}</div>
        <div className="tv-sel">{agencyField}</div>
      </>}
      <div className="tv-more-wrap">
        <button ref={moreButton} type="button" className={cn('tv-more-toggle', moreOpen && 'is-open', badge > 0 && 'has-count')} aria-expanded={moreOpen} aria-haspopup="dialog" aria-controls="tv-more-panel" onClick={() => setMoreOpen(open => !open)}>
          <SlidersHorizontal size={15} aria-hidden="true" />
          {phone ? 'Filters' : 'More filters'}
          {badge > 0 && <span className="tv-count tnum" aria-label={`${badge} ${badge === 1 ? 'filter' : 'filters'} on`}>{badge}</span>}
          <ChevronDown size={15} aria-hidden="true" className="tv-chevron" />
        </button>
        {moreOpen && <>
          {phone && <div className="tv-backdrop" aria-hidden="true" onClick={() => setMoreOpen(false)} />}
          <div ref={morePanel} id="tv-more-panel" className="tv-more-panel" role="dialog" aria-label={phone ? 'Filters' : 'More filters'}>
            {phone && <header><h2>Filters</h2><button type="button" className="tv-sheet-close" aria-label="Close filters" onClick={() => { setMoreOpen(false); moreButton.current?.focus() }}><X size={18} aria-hidden="true" /></button></header>}
            <div className="tv-more-grid">
              {phone && <>
                <Field label="Period">{periodField}</Field>
                <Field label="Client">{clientField}</Field>
                <Field label="Country">{countryField}</Field>
                <Field label="Agency">{agencyField}</Field>
              </>}
              <Field label="Year"><Select aria-label="Year" searchable value={filters.year} onChange={event => set('year', event.target.value)}><option value="">All years</option><optgroup label="Current and recorded years">{years.map(year => <option key={year} value={year}>{year}</option>)}</optgroup><optgroup label="Other years">{otherYears.map(year => <option key={year} value={year}>{year}</option>)}</optgroup></Select></Field>
              <Field label="Month"><Select aria-label="Travel month" value={filters.month} disabled={!filters.year} onChange={event => set('month', event.target.value)}><option value="">{filters.year ? 'All months' : 'Choose a year'}</option>{MONTH_NAMES.map((name, index) => <option key={name} value={String(index + 1).padStart(2, '0')}>{name}</option>)}</Select></Field>
              <Field label="Date of travel"><Input type="date" aria-label="Date of travel" min="1990-01-01" max="2100-12-31" value={travelDate} onChange={event => apply(chooseTravelDate(filters, event.target.value))} /></Field>
              <Field label="Job / order"><Select aria-label="Job or order" value={filters.order} onChange={event => set('order', event.target.value)}><option value="">All jobs / orders</option>{filters.order && !orders.some(order => order.id === filters.order) && <option value={filters.order}>Selected order</option>}{orders.map(order => <option key={order.id} value={order.id}>{order.job || 'Job'} · {order.ref || order.client}{!filters.client ? ` · ${order.client}` : ''}</option>)}</Select></Field>
              <button type="button" className={filters.incomplete ? 'tv-toggle is-on' : 'tv-toggle'} aria-pressed={!!filters.incomplete} onClick={() => apply(toggleIncomplete(filters))}>
                {filters.incomplete && <Check size={14} aria-hidden="true" />}Missing documents only
              </button>
            </div>
            <footer>
              {chips.length > 0 && <button type="button" className="tv-clear" onClick={clearAll}>Clear all</button>}
              <Button size="sm" variant="primary" onClick={() => { setMoreOpen(false); moreButton.current?.focus() }}>Done</Button>
            </footer>
          </div>
        </>}
      </div>
    </section>

    <div className="tv-meta">
      {chips.length > 0 && <div className="tv-chips" role="group" aria-label="Filters in use">
        {chips.map(chip => <button key={chip.key} type="button" className="tv-chip" onClick={() => apply(clearChip(filters, chip.key))} aria-label={`Remove ${chip.label}`}>{chip.label}<X size={12} aria-hidden="true" /></button>)}
        <button type="button" className="tv-clear" onClick={clearAll}>Clear all</button>
      </div>}
      {data && <button type="button" className="tv-breakdown-toggle" aria-expanded={breakdownOpen} aria-controls="tv-breakdowns" onClick={toggleBreakdown}>
        <ChevronRight size={15} aria-hidden="true" className="tv-chevron" />Breakdown
      </button>}
    </div>

    {(error || exportError) && <div role="alert" className="tv-alert"><span><AlertCircle size={15} aria-hidden="true" />{error || exportError}{error && data && ' Showing the last loaded list.'}</span>{error ? <button type="button" onClick={refresh}>Try again</button> : <button type="button" disabled={!!exporting} onClick={() => void exportFile(lastExport)}>Try again</button>}</div>}

    {!data
      ? !error && <SnapshotState label="Loading travelled candidates" />
      : <div className={stale ? 'tv-body is-stale' : 'tv-body'}>
        {breakdownOpen && <div id="tv-breakdowns" className="tv-breakdowns">
          <DateBreakdown data={data} filters={filters} onPick={value => apply(value)} />
          <Breakdown title="By client" empty="No clients yet" items={data.summary.byClient.map(item => ({ label: item.client, value: item.client, count: item.count }))}
            selected={filters.client} onPick={value => apply(toggleFilter(filters, 'client', value))} />
          <Breakdown title="By agency" empty="No agency recorded yet" items={data.summary.byAgency.map(item => ({ label: item.agency, value: item.agency, count: item.count }))}
            selected={filters.agency} onPick={value => apply(toggleFilter(filters, 'agency', value))} />
          <Breakdown title="By country" empty="No countries yet" items={data.summary.byCountry.map(item => ({ label: item.country, value: item.country, count: item.count }))}
            selected={filters.country} onPick={value => apply(toggleFilter(filters, 'country', value))} />
        </div>}

        {data.rows.length === 0
          ? <section className="tv-card"><EmptyState icon={<Plane size={20} strokeWidth={1.6} />}
            title={chips.length ? 'No one matches these filters' : 'No one has travelled yet'}
            description={chips.length ? 'Remove a filter to see more.' : 'Confirmed travelled candidates appear here with their placement marked Deployed.'}
            action={chips.length ? <Button size="sm" onClick={clearAll}>Clear all filters</Button> : undefined} /></section>
          : <TravelTable rows={data.rows} total={data.total} page={page} pageSize={pageSize} filtered={chips.length > 0}
            onPage={setPage} onPageSize={size => { setPageSize(size); savePageSize('travelled', size); setPage(0) }} />}
      </div>}
  </div>
}

/** A label above a control, for the fields inside the "More filters" panel. */
function Field({ label, children }: { label: string; children: ReactNode }) {
  return <div className="tv-field"><span>{label}</span>{children}</div>
}

/** Years, then the months of a year, then the days of a month: each bar narrows the list, the crumbs above walk back out. */
function DateBreakdown({ data, filters, onPick }: { data: TravelledResult; filters: TravelledFilters; onPick: (next: TravelledFilters) => void }) {
  const level = dateLevel(filters)
  const chosen = { year: filters.year, month: filters.year && filters.month ? `${filters.year}-${filters.month}` : '', day: filters.year && filters.month && filters.day ? `${filters.year}-${filters.month}-${filters.day}` : '' }
  const items = (level === 'year'
    ? data.summary.byYear.map(item => ({ value: String(item.year), count: item.count }))
    : level === 'month' ? data.summary.byMonth.map(item => ({ value: item.month, count: item.count }))
      : data.summary.byDay.map(item => ({ value: item.date, count: item.count })))
    .sort((a, b) => a.value.localeCompare(b.value))
  const largest = Math.max(0, ...items.map(item => item.count))
  const parent = (value: string) => value === chosen.year ? '' : value === chosen.month ? chosen.year : chosen.month
  const pick = (value: string) => onPick(chosen[level] === value ? drillTo(filters, parent(value)) : drillTo(filters, value))
  const title = level === 'year' ? 'By year' : level === 'month' ? 'By month' : 'By day'
  return <section className="tv-card tv-break tv-date" aria-label={title}>
    <header>
      <h2>{title}</h2>
      <nav className="tv-crumbs" aria-label="Date">
        <button type="button" className={!filters.year ? 'is-here' : undefined} onClick={() => onPick(drillTo(filters, ''))}>All years</button>
        {filters.year && <><ChevronRight size={13} aria-hidden="true" /><button type="button" className={!filters.month ? 'is-here' : undefined} onClick={() => onPick(drillTo(filters, filters.year))}>{filters.year}</button></>}
        {filters.month && <><ChevronRight size={13} aria-hidden="true" /><button type="button" className={!filters.day ? 'is-here' : undefined} onClick={() => onPick(drillTo(filters, `${filters.year}-${filters.month}`))}>{monthName(filters.month)}</button></>}
        {filters.day && <><ChevronRight size={13} aria-hidden="true" /><span className="is-here">{Number(filters.day)}</span></>}
      </nav>
    </header>
    {items.length === 0 ? <p className="tv-none">Nothing to show for these dates.</p>
      : <ul className={level === 'day' ? 'tv-bars is-days' : 'tv-bars'}>
        {items.map(item => <li key={item.value}>
          <button type="button" className={chosen[level] === item.value ? 'is-chosen' : undefined} aria-pressed={chosen[level] === item.value} onClick={() => pick(item.value)}
            title={`${dateBarLabel(level, item.value)}: ${item.count.toLocaleString()}`}>
            <span className="tv-bar-label">{dateBarLabel(level, item.value)}</span>
            <span className="tv-bar-track" aria-hidden="true"><i style={{ width: `${barPercent(item.count, largest)}%` }} /></span>
            <span className="tv-bar-count tnum">{item.count.toLocaleString()}</span>
          </button>
        </li>)}
      </ul>}
  </section>
}

function Breakdown({ title, items, selected, onPick, empty }: { title: string; items: { label: string; value: string; count: number }[]; selected: string; onPick: (value: string) => void; empty: string }) {
  const [all, setAll] = useState(false)
  const sorted = [...items].sort((a, b) => b.count - a.count || a.label.localeCompare(b.label))
  const shown = all ? sorted : sorted.slice(0, SHOWN)
  const largest = Math.max(0, ...sorted.map(item => item.count))
  return <section className="tv-card tv-break" aria-label={title}>
    <header><h2>{title}</h2></header>
    {sorted.length === 0 ? <p className="tv-none">{empty}</p>
      : <ul className="tv-bars">
        {shown.map(item => <li key={item.value}>
          <button type="button" className={selected === item.value ? 'is-chosen' : undefined} aria-pressed={selected === item.value} onClick={() => onPick(item.value)} title={`${item.label}: ${item.count.toLocaleString()}`}>
            <span className="tv-bar-label">{item.label}</span>
            <span className="tv-bar-track" aria-hidden="true"><i style={{ width: `${barPercent(item.count, largest)}%` }} /></span>
            <span className="tv-bar-count tnum">{item.count.toLocaleString()}</span>
          </button>
        </li>)}
      </ul>}
    {sorted.length > SHOWN && <button type="button" className="tv-more" onClick={() => setAll(value => !value)}>{all ? 'Show fewer' : `Show all ${sorted.length}`}</button>}
  </section>
}

function TravelTable({ rows, total, page, pageSize, filtered, onPage, onPageSize }: { rows: TravelledRow[]; total: number; page: number; pageSize: number; filtered: boolean; onPage: (page: number) => void; onPageSize: (size: number) => void }) {
  const wrap = useRef<HTMLDivElement>(null)
  const shown = pageRows(rows, page, pageSize)
  const go = (next: number) => { onPage(next); wrap.current?.scrollTo({ top: 0 }) }
  return <section className="tv-card tv-table-card" aria-label="Travelled candidates">
    <div ref={wrap} className="tv-table-wrap">
      <table className="tv-table">
        <colgroup><col className="tv-c-sno" /><col className="tv-c-doc" /><col className="tv-c-date" /><col className="tv-c-who" /><col className="tv-c-client" /><col className="tv-c-country" /><col className="tv-c-job" /><col className="tv-c-agency" /><col className="tv-c-docs" /><col className="tv-c-recruiter" /></colgroup>
        <thead><tr><th>S NO</th><th>DOC</th><th className="tv-th-wrap">Date of travel</th><th>Candidate</th><th>Client</th><th>Country</th><th>Job</th><th>Agency</th><th className="tv-th-wrap">Uploaded documents</th><th>Recruiter</th></tr></thead>
        <tbody>
          {shown.map((row, index) => <tr key={row.applicationId}>
            <td data-label="S NO" className="tnum">{serialNumber(page, pageSize, index)}</td>
            <td data-label="DOC" className="tnum">{docToText(row.commencementDate) || <span className="tv-muted">—</span>}</td>
            <td data-label="Date of travel" className="tnum tv-c-when">{row.travelDate ? formatTravelDay(row.travelDate) : <span className="tv-muted">Not recorded</span>}</td>
            <td data-label="Candidate" className="tv-who">
              <Link to={`/candidates/${row.candidateId}`}>{row.name}</Link>
              <small className="tv-passport tnum">{row.passportNumber || <span className="tv-muted">Passport not added</span>}</small>
              {row.travellerId && <span className="tv-traveller tnum" title={`Traveller number ${row.travellerId}`}>{row.travellerId}</span>}
              {row.status && row.status !== 'deployed' && <StatusPill status={row.status} />}
            </td>
            <td data-label="Client">{row.clientId ? <Link to={`/clients/${row.clientId}`}>{row.client}</Link> : row.client || <span className="tv-muted">—</span>}</td>
            <td data-label="Country">{row.country || <span className="tv-muted">—</span>}</td>
            <td data-label="Job">{row.orderId ? <Link to={`/orders/${row.orderId}`}>{row.job || 'Open job'}</Link> : row.job || <span className="tv-muted">—</span>}</td>
            <td data-label="Agency">{row.agency ? row.agency : <Link className="tv-hint" to={`/candidates/${row.candidateId}/application`}>Add agency</Link>}</td>
            <td data-label="Uploaded documents">{row.documents
              ? row.documents.missing.length ? <details className="tv-doc-details"><summary className="tv-docs is-short" title={documentsTitle(row)}>{documentsLabel(row)}<ChevronDown size={13} aria-hidden="true" /><span className="sr-only"> documents on file, {row.documents.missing.length} missing</span></summary><ul>{row.documents.missing.map(label => <li key={label}>{label}</li>)}</ul><Link to={`/candidates/${row.candidateId}/application`}>Review documents</Link></details>
                : <span className="tv-docs is-done" title={documentsTitle(row)}><Check size={13} aria-hidden="true" />{documentsLabel(row)}<span className="sr-only"> documents on file, none missing</span></span>
              : <span className="tv-muted">—</span>}</td>
            <td data-label="Recruiter">{row.recruiter || <span className="tv-muted">—</span>}</td>
          </tr>)}
        </tbody>
      </table>
    </div>
    <Pager page={page} pageSize={pageSize} total={rows.length} noun="candidates" filtered={filtered} sizes={PAGE_SIZE_CHOICES} onPage={go} onPageSize={onPageSize} />
    {rows.length < total && <p className="tv-foot">Showing the first {rows.length.toLocaleString()} of {total.toLocaleString()}. Narrow the filters or export the file to see all.</p>}
  </section>
}
