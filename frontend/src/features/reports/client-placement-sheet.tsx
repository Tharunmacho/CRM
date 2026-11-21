import { useState, type ReactNode } from 'react'
import { useSearchParams } from 'react-router-dom'
import { AlertCircle, FileSpreadsheet, Loader2, RefreshCw, RotateCcw, Table2 } from 'lucide-react'
import { useStore } from '@/shared/state/crm-store'
import { Button, Select } from '@/shared/ui/primitives'
import { EmptyState } from '@/shared/ui/display'
import { agencyDay } from '@/shared/lib/task-time'
import { TOTAL_STAGES } from '@/shared/workflow/stages'
import { downloadClientTracker, useClientTracker } from './client-tracker-api'
import {
  EMPTY_TRACKER_FILTERS, NO_TRACKER_OPTIONS, TRACKER_COLUMNS, activeTrackerFilters, cellText, trackerSummary, withChosen,
  type TrackerFilters, type TrackerResult,
} from './client-tracker-model'
import '@/styles/report-mgmt-tabs.css'
import '@/styles/client-tracker.css'
import '@/styles/touch-targets.css'

/** The stage a Dashboard chip asks for (?stage=10), when the address carries one. */
export function stageFromAddress(params: URLSearchParams): string {
  const wanted = Number(params.get('stage'))
  return Number.isInteger(wanted) && wanted >= 1 && wanted <= TOTAL_STAGES ? String(wanted) : ''
}

/**
 * The placement sheet: every placement still in progress and the ones that travelled, grouped by client, in the sheet the clients ask for
 * (S.No, Client, Country, Name, Position, Ref, Advance, Stage, Remarks). The file is the same sheet with the same filters.
 */
export function PlacementSheet({ switcher }: { switcher: ReactNode }) {
  const store = useStore()
  // A chip on the Dashboard card opens the tracker already filtered to that stage (?stage=10).
  const [params, setParams] = useSearchParams()
  const [filters, setFilters] = useState<TrackerFilters>(() => {
    const stage = stageFromAddress(params)
    return stage ? { ...EMPTY_TRACKER_FILTERS, stage } : EMPTY_TRACKER_FILTERS
  })
  const { data: fresh, error, refresh, refreshing } = useClientTracker(filters, store.me.id)
  // Keep the previous sheet on screen (dimmed) while a new choice loads, so the page does not jump.
  const [last, setLast] = useState<{ user: string; data: TrackerResult } | null>(null)
  if (fresh && last?.data !== fresh) setLast({ user: store.me.id, data: fresh })
  const data = fresh ?? (last?.user === store.me.id ? last.data : null)
  const stale = !fresh && !!data
  const options = data?.options ?? NO_TRACKER_OPTIONS
  const [exporting, setExporting] = useState(false)
  const [exportError, setExportError] = useState('')
  // The address keeps only what the chip put there: choosing another stage (or Reset) must not leave a stale ?stage= behind, or a reload would bring it back.
  const keepStageInAddress = (stage: string) => setParams(previous => { const next = new URLSearchParams(previous); if (stage) next.set('stage', stage); else next.delete('stage'); return next }, { replace: true })
  const choose = (patch: Partial<TrackerFilters>) => { setFilters(previous => ({ ...previous, ...patch })); if (patch.stage !== undefined) keepStageInAddress(patch.stage) }
  const clearFilters = () => { setFilters(EMPTY_TRACKER_FILTERS); keepStageInAddress('') }
  const inUse = activeTrackerFilters(filters)
  async function exportFile() {
    setExporting(true); setExportError('')
    try { await downloadClientTracker(filters, 'xlsx', agencyDay(new Date(), store.agencyTimezone)) }
    catch (failure) { setExportError(failure instanceof Error ? failure.message : 'We could not create the file. Please try again.') }
    finally { setExporting(false) }
  }
  return <section className="rm-panel ct-sheet" aria-label="Placement sheet" aria-busy={refreshing || stale}>
    <div className="rm-head">
      <div><h2><Table2 size={16} aria-hidden="true" /> Placement sheet</h2></div>
      {switcher}
    </div>
    <div className="rm-filters ct-filters" role="group" aria-label="Client tracker filters">
      <label className="rm-filter">
        <span>Client</span>
        <Select compact aria-label="Client" value={filters.client} onChange={event => choose({ client: event.target.value })}><option value="">All clients</option>{withChosen(options.clients, filters.client).map(name => <option key={name} value={name}>{name}</option>)}</Select>
      </label>
      <label className="rm-filter">
        <span>Country</span>
        <Select compact aria-label="Country" value={filters.country} onChange={event => choose({ country: event.target.value })}><option value="">All countries</option>{withChosen(options.countries, filters.country).map(name => <option key={name} value={name}>{name}</option>)}</Select>
      </label>
      <label className="rm-filter">
        <span>Referral agency</span>
        <Select compact aria-label="Referral agency" value={filters.agency} onChange={event => choose({ agency: event.target.value })}><option value="">All agencies</option>{withChosen(options.agencies, filters.agency).map(name => <option key={name} value={name}>{name}</option>)}</Select>
      </label>
      <label className="rm-filter">
        <span>Stage</span>
        <Select compact aria-label="Stage" value={filters.stage} onChange={event => choose({ stage: event.target.value })}><option value="">All stages</option>{options.stages.map(item => <option key={item.no} value={String(item.no)}>{item.label}</option>)}</Select>
      </label>
      <label className="ct-check"><input type="checkbox" aria-label="Include travelled" checked={filters.includeTravelled} disabled={!!filters.stage} onChange={event => choose({ includeTravelled: event.target.checked })} /><span>Include travelled</span></label>
      <div className="rm-filters-end">
        <Button size="sm" variant="ghost" icon={<RotateCcw size={13} />} disabled={!inUse} onClick={clearFilters}>Reset{inUse ? ` (${inUse})` : ''}</Button>
        <Button size="sm" icon={<RefreshCw size={14} className={refreshing ? 'rm-spin' : undefined} />} disabled={refreshing} onClick={refresh}>{refreshing ? 'Refreshing' : 'Refresh'}</Button>
        <Button size="sm" variant="secondary" disabled={!data || exporting} icon={exporting ? <Loader2 size={14} className="rm-spin" /> : <FileSpreadsheet size={14} />} onClick={() => void exportFile()}>{exporting ? 'Preparing Excel…' : 'Export Excel'}</Button>
      </div>
    </div>

    {(error || exportError) && <div role="alert" className="rm-alert ct-alert"><span><AlertCircle size={15} aria-hidden="true" />{error || exportError}{error && data && ' Showing the last loaded sheet.'}</span>{error ? <button type="button" onClick={refresh}>Try again</button> : <button type="button" disabled={exporting} onClick={() => void exportFile()}>Try again</button>}</div>}
    {!data && !error && <p className="ct-loading" role="status">Loading the client tracker…</p>}

    {data && <div className={stale ? 'ct-table is-stale' : 'ct-table'}>
      {data.rows.length === 0
        ? <EmptyState icon={<Table2 size={20} strokeWidth={1.6} />} title={inUse ? 'No placements match these filters' : 'No placements in progress'}
          description={inUse ? 'Remove a filter to see more.' : 'Candidates matched to an order, and those who travelled, appear here.'}
          action={inUse ? <Button size="sm" onClick={clearFilters}>Clear filters</Button> : undefined} />
        : <div className="ct-scroll" role="region" aria-label="Client tracker table, scrolls sideways on small screens" tabIndex={0}><table>
          <colgroup>{TRACKER_COLUMNS.map(column => <col key={column.key} className={`ct-col-${column.key}`} />)}</colgroup>
          <thead><tr>{TRACKER_COLUMNS.map(column => <th key={column.key} scope="col" className={column.numeric ? 'is-num' : undefined}>{column.label}</th>)}</tr></thead>
          <tbody>{data.rows.map((row, index) => <tr key={row.applicationId} className={index > 0 && data.rows[index - 1].client !== row.client ? 'is-first-of-client' : undefined}>
            {TRACKER_COLUMNS.map(column => <td key={column.key} data-label={column.label} className={column.numeric ? 'is-num tnum' : column.key === 'remarks' ? 'is-remarks' : undefined}><span className="ct-cell" title={cellText(row, column.key) || undefined}>{cellText(row, column.key)}</span></td>)}
          </tr>)}</tbody>
        </table></div>}
      <footer className="rm-foot"><span>{trackerSummary(data)}</span></footer>
    </div>}
  </section>
}
