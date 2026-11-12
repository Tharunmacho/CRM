import { useEffect, useRef, useState } from 'react'
import { AlertCircle, Building2, ChevronRight, Download, FileSpreadsheet, Globe2, Loader2, Plane, RefreshCw, Users, X } from 'lucide-react'
import { Link, useSearchParams } from 'react-router-dom'
import { useStore } from '@/shared/state/crm-store'
import { Button, Input, Select } from '@/shared/ui/primitives'
import { EmptyState, PageHeader, StatusPill } from '@/shared/ui/display'
import { SnapshotState } from '@/shared/ui/page-loading'
import { SummaryCards } from '@/shared/ui/workspace'
import { downloadTravelled, useTravelled } from '@/features/travelled/travelled-api'
import {
  EMPTY_FILTERS, MONTH_NAMES, activeChips, activeQuick, agencyToday, barPercent, chooseTravelDate, clearChip, dateBarLabel, dateLevel, dateSummary, documentsLabel,
  documentsTitle, drillTo, filtersFromParams, formatTravelDay, monthName, queryFor, quickRanges, setFilter,
  toggleFilter, toggleIncomplete, toggleQuick, toggleUndated, withChosen,
  type TravelledFilterKey, type TravelledFilters, type TravelledResult, type TravelledRow,
} from '@/features/travelled/travelled-model'
import '@/styles/travelled.css'

const SHOWN = 6

type Options = TravelledResult['options']
const NO_OPTIONS: Options = { years: [], months: [], undated: 0, clients: [], agencies: [], countries: [], orders: [] }

/**
 * Who has travelled, when, for which client and through which agency. Every choice is in the address
 * (?year=2026&month=05&client=...), so a view can be bookmarked or sent to someone. Clicking a bar of a breakdown
 * narrows the list to it; clicking it again widens it.
 */
export default function TravelledPage() {
  const store = useStore()
  const [params, setParams] = useSearchParams()
  const filters = filtersFromParams(params)
  const apply = (next: TravelledFilters, replace = false) => setParams(new URLSearchParams(queryFor(next)), { replace })

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
  const quick = activeQuick(filters, today)
  const travelDate = filters.year && filters.month && filters.day ? `${filters.year}-${filters.month}-${filters.day}` : ''
  const busy = refreshing || (stale && !error)

  return <div className="tv" aria-busy={busy}>
    <PageHeader title="Travelled" actions={<>
      <Button size="sm" icon={<RefreshCw size={14} className={refreshing ? 'animate-spin' : undefined} />} disabled={refreshing} onClick={refresh}>{refreshing ? 'Refreshing' : 'Refresh'}</Button>
      <Button size="sm" disabled={!data || !!exporting} icon={exporting === 'xlsx' ? <Loader2 size={14} className="animate-spin" /> : <FileSpreadsheet size={14} />} onClick={() => void exportFile('xlsx')}>{exporting === 'xlsx' ? 'Preparing Excel…' : 'Export Excel'}</Button>
      <Button size="sm" disabled={!data || !!exporting} icon={exporting === 'csv' ? <Loader2 size={14} className="animate-spin" /> : <Download size={14} />} onClick={() => void exportFile('csv')}>{exporting === 'csv' ? 'Preparing CSV…' : 'Export CSV'}</Button>
    </>} />

    <section className="tv-card tv-filters" aria-label="Filters">
      <div className="tv-quick" role="group" aria-label="Quick dates">
        <Button size="sm" onClick={clearAll}>Show all candidates</Button>
        <button type="button" className={!dateSummary(filters) ? 'tv-quick-btn is-on' : 'tv-quick-btn'} aria-pressed={!dateSummary(filters)} onClick={() => apply(drillTo(filters, ''))}>All dates</button>
        {quickRanges(today).map(range => <button key={range.key} type="button" className={quick === range.key ? 'tv-quick-btn is-on' : 'tv-quick-btn'} aria-pressed={quick === range.key}
          title={`${formatTravelDay(range.from)} to ${formatTravelDay(range.to)}`} onClick={() => apply(toggleQuick(filters, range.key, today))}>{range.label}</button>)}
        <button type="button" className={filters.undated ? 'tv-quick-btn is-on' : 'tv-quick-btn'} aria-pressed={!!filters.undated} onClick={() => apply(toggleUndated(filters))}>Date not recorded{options.undated ? ` (${options.undated})` : ''}</button>
      </div>
      <div className="tv-filter-grid">
        <label>Year<Select aria-label="Year" searchable value={filters.year} onChange={e => set('year', e.target.value)}><option value="">All years</option><optgroup label="Current and recorded years">{years.map(year => <option key={year} value={year}>{year}</option>)}</optgroup><optgroup label="Other years">{otherYears.map(year => <option key={year} value={year}>{year}</option>)}</optgroup></Select></label>
        <label>Month<Select aria-label="Travel month" value={filters.month} disabled={!filters.year} onChange={e => set('month', e.target.value)}><option value="">{filters.year ? 'All months' : 'Choose a year'}</option>{MONTH_NAMES.map((name, index) => <option key={name} value={String(index + 1).padStart(2, '0')}>{name}</option>)}</Select></label>
        <label>Travel date<Input type="date" aria-label="Travel date" min="1990-01-01" max="2100-12-31" value={travelDate} onChange={e => apply(chooseTravelDate(filters, e.target.value))} /></label>
        <label>Client<Select aria-label="Client" value={filters.client} onChange={e => set('client', e.target.value)}><option value="">All clients</option>{withChosen(options.clients, filters.client).map(name => <option key={name} value={name}>{name}</option>)}</Select></label>
        <label>Job / order<Select aria-label="Job or order" value={filters.order} onChange={e => set('order', e.target.value)}><option value="">All jobs / orders</option>{filters.order && !orders.some(order => order.id === filters.order) && <option value={filters.order}>Selected order</option>}{orders.map(order => <option key={order.id} value={order.id}>{order.job || 'Job'} · {order.ref || order.client}{!filters.client ? ` · ${order.client}` : ''}</option>)}</Select></label>
        <label>Agency<Select aria-label="Agency" value={filters.agency} onChange={e => set('agency', e.target.value)}><option value="">All agencies</option>{withChosen(options.agencies, filters.agency).map(name => <option key={name} value={name}>{name}</option>)}</Select></label>
        <label>Country<Select aria-label="Country" value={filters.country} onChange={e => set('country', e.target.value)}><option value="">All countries</option>{withChosen(options.countries, filters.country).map(name => <option key={name} value={name}>{name}</option>)}</Select></label>
        <div className="tv-search-row">
          <label className="tv-search">Search<Input type="search" aria-label="Search candidate, passport, client, job or flight" placeholder="Name, passport, client, job or PNR" value={search} onChange={e => setSearch(e.target.value)} /></label>
          <button type="button" className={filters.incomplete ? 'tv-toggle is-on' : 'tv-toggle'} aria-pressed={!!filters.incomplete} onClick={() => apply(toggleIncomplete(filters))}>Missing documents only</button>
        </div>
      </div>
      {data && <p className="tv-filter-note">All travelled candidates are shown when no filters are selected. Choosing a filter updates the list immediately; Show all candidates clears every filter. {!!options.undated && <>{options.undated} travelled {options.undated === 1 ? 'record has' : 'records have'} no saved travel date and will not appear under a specific year or month.</>}</p>}
      {chips.length > 0 && <div className="tv-chips" role="group" aria-label="Filters in use">
        {chips.map(chip => <button key={chip.key} type="button" className="tv-chip" onClick={() => apply(clearChip(filters, chip.key))} aria-label={`Remove ${chip.label}`}>{chip.label}<X size={12} aria-hidden="true" /></button>)}
        <button type="button" className="tv-clear" onClick={clearAll}>Clear all</button>
      </div>}
    </section>

    {(error || exportError) && <div role="alert" className="tv-alert"><span><AlertCircle size={15} aria-hidden="true" />{error || exportError}{error && data && ' Showing the last loaded list.'}</span>{error ? <button type="button" onClick={refresh}>Try again</button> : <button type="button" disabled={!!exporting} onClick={() => void exportFile(lastExport)}>Try again</button>}</div>}

    {!data
      ? !error && <SnapshotState label="Loading travelled candidates" />
      : <div className={stale ? 'tv-body is-stale' : 'tv-body'}>
        <SummaryCards items={[
          { label: 'Travelled', value: data.total.toLocaleString(), detail: dateSummary(filters) || 'All dates', icon: Plane, tone: 'green' },
          { label: 'Clients', value: data.summary.byClient.length.toLocaleString(), detail: filters.client || 'Employers they flew to', icon: Building2, tone: 'blue' },
          { label: 'Countries', value: data.summary.byCountry.filter(item => item.country !== 'Not recorded').length.toLocaleString(), detail: filters.country || 'Where they went', icon: Globe2, tone: 'amber' },
          { label: 'Agencies', value: data.summary.byAgency.filter(item => item.agency !== 'Not recorded').length.toLocaleString(), detail: filters.agency || 'Recorded referral agencies', icon: Users, tone: 'violet' },
        ]} />

        <details className="tv-breakdown-panel"><summary>View breakdowns by date, client, agency and country</summary><div className="tv-breakdowns">
          <DateBreakdown data={data} filters={filters} onPick={value => apply(value)} />
          <Breakdown title="By client" empty="No clients yet" items={data.summary.byClient.map(item => ({ label: item.client, value: item.client, count: item.count }))}
            selected={filters.client} onPick={value => apply(toggleFilter(filters, 'client', value))} />
          <Breakdown title="By agency" empty="No agency recorded yet" items={data.summary.byAgency.map(item => ({ label: item.agency, value: item.agency, count: item.count }))}
            selected={filters.agency} onPick={value => apply(toggleFilter(filters, 'agency', value))} />
          <Breakdown title="By country" empty="No countries yet" items={data.summary.byCountry.map(item => ({ label: item.country, value: item.country, count: item.count }))}
            selected={filters.country} onPick={value => apply(toggleFilter(filters, 'country', value))} />
        </div></details>

        {data.rows.length === 0
          ? <section className="tv-card"><EmptyState icon={<Plane size={20} strokeWidth={1.6} />}
            title={chips.length ? 'No one matches these filters' : 'No one has travelled yet'}
            description={chips.length ? 'Remove a filter to see more.' : 'Confirmed travelled candidates appear here with their placement marked Deployed.'}
            action={chips.length ? <Button size="sm" onClick={clearAll}>Clear all filters</Button> : undefined} /></section>
          : <TravelTable rows={data.rows} total={data.total} />}
      </div>}
  </div>
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

function TravelTable({ rows, total }: { rows: TravelledRow[]; total: number }) {
  return <section className="tv-card tv-table-card" aria-label="Travelled candidates">
    <div className="tv-table-wrap">
      <table className="tv-table">
        <thead><tr><th>Travel date</th><th>Candidate</th><th>Client</th><th>Country</th><th>Job</th><th>Agency</th><th>Flight</th><th>Documents</th><th>Recruiter</th></tr></thead>
        <tbody>
          {rows.map(row => <tr key={row.applicationId}>
            <td data-label="Travel date" className="tnum">{row.travelDate ? formatTravelDay(row.travelDate) : <span className="tv-muted">Not recorded</span>}</td>
            <td data-label="Candidate" className="tv-who">
              <Link to={`/candidates/${row.candidateId}`}>{row.name}</Link>
              <small className="ident">{row.ref}</small>
              {row.passportNumber && <small className="ident">Passport {row.passportNumber}</small>}
              {row.status && <StatusPill status={row.status} />}
            </td>
            <td data-label="Client">{row.clientId ? <Link to={`/clients/${row.clientId}`}>{row.client}</Link> : row.client || <span className="tv-muted">—</span>}</td>
            <td data-label="Country">{row.country || <span className="tv-muted">—</span>}</td>
            <td data-label="Job">{row.orderId ? <Link to={`/orders/${row.orderId}`}>{row.job || 'Open job'}</Link> : row.job || <span className="tv-muted">—</span>}{row.orderRef && <small className="ident">{row.orderRef}</small>}</td>
            <td data-label="Agency">{row.agency ? row.agency : <span className="tv-muted">—<Link className="tv-hint" to={`/candidates/${row.candidateId}/application`}>add agency</Link></span>}</td>
            <td data-label="Flight">{row.airline || row.pnr ? <>{row.airline || ''}{row.pnr && <small className="ident">PNR {row.pnr}</small>}</> : <span className="tv-muted">—</span>}</td>
            <td data-label="Documents">{row.documents
              ? row.documents.missing.length ? <details className="tv-doc-details"><summary className="tv-docs is-short">{documentsLabel(row)}<small>{row.documents.missing.length} missing</small></summary><ul>{row.documents.missing.map(label => <li key={label}>{label}</li>)}</ul><Link to={`/candidates/${row.candidateId}/application`}>Review documents</Link></details>
                : <span className="tv-docs" title={documentsTitle(row)}>{documentsLabel(row)}<small>Complete</small></span>
              : <span className="tv-muted">—</span>}</td>
            <td data-label="Recruiter">{row.recruiter || <span className="tv-muted">—</span>}</td>
          </tr>)}
        </tbody>
      </table>
    </div>
    <footer className="tv-foot">{rows.length === total ? `${total.toLocaleString()} ${total === 1 ? 'candidate' : 'candidates'}` : `Showing ${rows.length.toLocaleString()} of ${total.toLocaleString()}. Narrow the filters or export the file to see all.`}</footer>
  </section>
}
