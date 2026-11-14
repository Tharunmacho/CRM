import { useEffect, useMemo, useRef, useState } from 'react'
import { Activity, AlertCircle, Banknote, Check, ClipboardList, Download, FileSpreadsheet, Filter, GitBranch, Inbox, Loader2, Lock, Plane, RefreshCw, RotateCcw, UserX, Users, type LucideIcon } from 'lucide-react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { useStore } from '@/shared/state/crm-store'
import { Button, Input, Select } from '@/shared/ui/primitives'
import { PageHeader } from '@/shared/ui/display'
import { Tabs } from '@/shared/ui/overlay'
import { SummaryCards } from '@/shared/ui/workspace'
import { agencyDay } from '@/shared/lib/task-time'
import { downloadReport, useReportData, type ReportResult } from '@/features/reports/report-api'
import { REPORTS, STAFF_WEEKLY_COPY, type ReportKey } from '@/features/reports/report-config'
import { paramsForTab, reportTabFromParams } from '@/features/reports/report-tabs'
import { AdvanceReport, FunnelReport, OrdersReport, RejectionsReport, SourceReport, StaffReport } from '@/features/reports/report-views'
import { validDateFilter } from '@/features/staff/task-filters'
import { rangeFromParams } from '@/features/staff-report/staff-report-model'
import { StaffWeeklyBody, StaffWeeklyExport, StaffWeeklyPeriod, useStaffWeekly } from '@/features/staff-report/staff-weekly-report'
import { FeeCollectionsReport } from '@/features/reports/fee-collections'
import '@/styles/reports-page.css'
import '@/styles/reports-v2.css'

const ICONS: Record<ReportKey, LucideIcon> = { funnel: GitBranch, staff: Activity, source: Inbox, orders: ClipboardList, rejections: UserX, advance: Banknote, collections: Banknote }

function shiftDay(day: string, days: number) { const date = new Date(`${day}T12:00:00Z`); date.setUTCDate(date.getUTCDate() + days); return date.toISOString().slice(0, 10) }
const FIELDS = [['client_id', 'Client', 'clients'], ['order_id', 'Order', 'orders'], ['designation_id', 'Job title', 'designations']] as const
const PRESETS: { label: string; days: number | 'ytd' }[] = [{ label: '30 days', days: 30 }, { label: '90 days', days: 90 }, { label: 'This year', days: 'ytd' }]

/**
 * One Reports area. The tab is in the address (?tab=funnel|staff|source|orders|rejections|advance), so a link opens it.
 * Staff performance is the weekly staff report for the admin (own dates, Live badge, drawer per person) and the table
 * of staff figures for everyone else; the Filters card applies to the other tabs and to that table.
 */
export default function ReportsPage() {
  const store = useStore()
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()
  const supervisor = store.can('all_reports')
  const invoices = store.can('view_invoices')
  const report = reportTabFromParams(params, supervisor, invoices)
  const weeklyMode = store.can('staff_report') && report === 'staff'
  const collectionsMode = report === 'collections'
  // In the weekly report ?from=&to=&staff= mean its week and the open person; elsewhere they seed the Filters card.
  const linkedStaff = supervisor && !weeklyMode ? params.get('staff') || '' : ''
  // The server's agency day, not the browser's zone or a compiled-in constant.
  const today = agencyDay(new Date(), store.agencyTimezone)
  const defaultFrom = shiftDay(today, -89)
  const linkedFrom = (!weeklyMode && validDateFilter(params.get('from'))) || defaultFrom
  const linkedTo = (!weeklyMode && validDateFilter(params.get('to'))) || today
  const [filters, setFilters] = useState({ from_date: linkedFrom, to_date: linkedTo, client_id: '', order_id: '', designation_id: '', staff_id: linkedStaff })
  const linked = !weeklyMode && (params.has('from') || params.has('to') || !!linkedStaff)
  useEffect(() => {
    if (linked) setFilters(previous => ({ ...previous, from_date: linkedFrom, to_date: linkedTo, staff_id: linkedStaff }))
  }, [linked, linkedStaff, linkedFrom, linkedTo])
  const [exporting, setExporting] = useState<'' | 'xlsx' | 'pdf'>('')
  const [lastFormat, setLastFormat] = useState<'xlsx' | 'pdf'>('xlsx')
  const [exportError, setExportError] = useState('')
  const [exported, setExported] = useState('')
  const query = new URLSearchParams(Object.entries(filters).filter(([, value]) => !!value)).toString()
  const { data: fresh, error, refresh, refreshing } = useReportData<ReportResult>(weeklyMode || collectionsMode ? null : `/reports?${query}`, store.me.id)
  // Keep the previous result on screen (dimmed) while new filters load, so the page does not jump.
  const last = useRef<{ user: string; data: ReportResult } | null>(null)
  if (fresh) last.current = { user: store.me.id, data: fresh }
  const data = fresh ?? (last.current?.user === store.me.id ? last.current.data : null)
  const stale = !weeklyMode && !fresh && !!data
  const [options, setOptions] = useState<ReportResult['options'] | null>(null)
  useEffect(() => { if (fresh) setOptions(fresh.options) }, [fresh])

  // The weekly report's period and the person whose drawer is open live in the address, so a link reopens them.
  const range = useMemo(() => rangeFromParams(params.get('from'), params.get('to'), today), [params, today])
  const weekly = useStaffWeekly(range, weeklyMode)
  const setRange = (next: { from: string; to: string }) => {
    const copy = new URLSearchParams(params)
    copy.set('from', next.from); copy.set('to', next.to)
    setParams(copy, { replace: true })
  }
  const openStaff = (id: string) => {
    const copy = new URLSearchParams(params)
    if (id) copy.set('staff', id); else copy.delete('staff')
    setParams(copy, { replace: !id })
  }
  const chooseReport = (key: string) => { setParams(paramsForTab(params, key as ReportKey)); setExportError(''); setExported('') }

  const active = REPORTS.find(item => item.key === report)!
  const copy = weeklyMode ? STAFF_WEEKLY_COPY : active
  const update = (key: keyof typeof filters, value: string) => { setExported(''); setFilters(previous => ({ ...previous, [key]: value })) }
  const changed = filters.client_id || filters.order_id || filters.designation_id || (supervisor && filters.staff_id) || filters.from_date !== defaultFrom || filters.to_date !== today
  const activeCount = [filters.client_id, filters.order_id, filters.designation_id, supervisor ? filters.staff_id : ''].filter(Boolean).length + (filters.from_date !== defaultFrom || filters.to_date !== today ? 1 : 0)
  const reset = () => { setExported(''); setFilters({ from_date: defaultFrom, to_date: today, client_id: '', order_id: '', designation_id: '', staff_id: '' }) }
  const presetFrom = (days: number | 'ytd') => days === 'ytd' ? `${today.slice(0, 4)}-01-01` : shiftDay(today, -(days - 1))
  const preset = (days: number | 'ytd') => { setExported(''); setFilters(previous => ({ ...previous, from_date: presetFrom(days), to_date: today })) }
  async function exportFile(format: 'xlsx' | 'pdf') {
    setExporting(format); setLastFormat(format); setExportError(''); setExported('')
    try { await downloadReport(report, format, query); setExported(`${active.title} downloaded as ${format === 'xlsx' ? 'Excel' : 'PDF'}.`) }
    catch (failure) { setExportError(failure instanceof Error ? failure.message : 'We could not create the file. Please try again.') }
    finally { setExporting('') }
  }
  const go = (path: string) => navigate(path)
  const Icon = ICONS[report]
  const busy = weeklyMode ? weekly.refreshing : refreshing
  return <div className="reports-page rv">
    <PageHeader title="Reports" description={invoices ? 'Recruitment progress, team activity and candidate fee collections.' : supervisor ? 'Recruitment progress and team activity.' : 'How your recruitment is going, how busy you are, and advance money collected.'} actions={collectionsMode ? undefined :
      <Button size="sm" icon={<RefreshCw size={14} className={busy ? 'animate-spin' : undefined} />} disabled={busy} onClick={weeklyMode ? weekly.refresh : refresh}>{busy ? 'Refreshing' : 'Refresh'}</Button>} />

    <section className="rv-card rv-report-head" aria-label="Choose a report">
      <Tabs tabs={REPORTS.filter(item => invoices || item.key !== 'collections').map(item => ({ key: item.key, label: item.label }))} active={report} onChange={chooseReport} />
      <header>
        <div className="rv-report-title"><span className="rv-icon is-lg"><Icon size={20} strokeWidth={1.8} /></span><div><h2>{copy.title}</h2><p>{copy.note}</p></div></div>
        {collectionsMode ? null : weeklyMode ? <StaffWeeklyExport weekly={weekly} /> : <div className="rv-export">
          <div role="group" aria-label="Export this report">
            <Button size="sm" disabled={!data || !!exporting} icon={exporting === 'xlsx' ? <Loader2 size={14} className="animate-spin" /> : <FileSpreadsheet size={14} />} onClick={() => void exportFile('xlsx')}>{exporting === 'xlsx' ? 'Preparing Excel…' : 'Export Excel'}</Button>
            <Button size="sm" disabled={!data || !!exporting} icon={exporting === 'pdf' ? <Loader2 size={14} className="animate-spin" /> : <Download size={14} />} onClick={() => void exportFile('pdf')}>{exporting === 'pdf' ? 'Preparing PDF…' : 'Export PDF'}</Button>
          </div>
          <p className={exporting ? 'rv-export-note is-busy' : exported && !exportError ? 'rv-export-note is-done' : 'rv-export-note'} role="status">
            {exporting ? <><span className="rv-progress" aria-hidden="true"><i /></span>Preparing your {exporting === 'xlsx' ? 'Excel' : 'PDF'} file. A long period can take up to a minute.</>
              : exported && !exportError ? <><Check size={14} aria-hidden="true" />{exported}</> : 'The file uses the same filters and dates as the screen.'}
          </p>
        </div>}
      </header>
    </section>

    {collectionsMode ? <FeeCollectionsReport /> : weeklyMode
      ? <>
        <StaffWeeklyPeriod range={range} today={today} onRange={setRange} weekly={weekly} />
        <div className="rv-report-body" role="tabpanel" aria-label={copy.title}>
          <StaffWeeklyBody weekly={weekly} staffId={params.get('staff') || ''} onOpenStaff={openStaff} />
        </div>
      </>
      : <>
        {report === 'funnel' && (data
          ? <SummaryCards items={[
            { label: 'Matched to orders', value: data.summary.applications.toLocaleString(), detail: 'Candidates in this period', icon: GitBranch, tone: 'blue' },
            { label: 'Still in progress', value: data.summary.active.toLocaleString(), detail: 'Of those matched to orders', icon: Activity, tone: 'violet' },
            { label: 'Deployed', value: data.summary.deployed.toLocaleString(), detail: 'Of those matched to orders', icon: Plane, tone: 'green' },
            { label: 'Candidates added', value: data.summary.intake.toLocaleString(), detail: 'New in this period, with your filters', icon: Users, tone: 'amber' },
          ]} />
          : !error && <div className="rv-skeleton" role="status" aria-label="Loading reports"><div className="rv-skeleton-tiles">{[0, 1, 2, 3].map(i => <i key={i} />)}</div></div>)}

        <section className="rv-card rv-filters" aria-label="Report filters">
          <header>
            <div className="rv-filters-title"><span className="rv-icon"><Filter size={16} /></span><div><h2>Filters</h2><p>{activeCount ? `${activeCount} filter${activeCount === 1 ? '' : 's'} in use for every report and file` : 'Used for every report and file you download'}</p></div></div>
            <div className="rv-filters-actions">
              <div className="rv-presets" role="group" aria-label="Date range shortcuts">
                {PRESETS.map(item => { const on = filters.from_date === presetFrom(item.days) && filters.to_date === today
                  return <button key={item.label} type="button" aria-pressed={on} onClick={() => preset(item.days)}>{item.label}</button> })}
              </div>
              <Button size="sm" icon={<RotateCcw size={13} />} disabled={!changed} onClick={reset}>Reset</Button>
            </div>
          </header>
          <div className="rv-filter-grid">
            <label>From<Input type="date" aria-label="From date" value={filters.from_date} max={filters.to_date} onChange={e => update('from_date', e.target.value)} /></label>
            <label>To<Input type="date" aria-label="To date" value={filters.to_date} min={filters.from_date} onChange={e => update('to_date', e.target.value)} /></label>
            {FIELDS.map(([key, label, group]) => <label key={key}>{label}<Select aria-label={label} value={filters[key]} onChange={e => update(key, e.target.value)}><option value="">All {label.toLowerCase()}s</option>{options?.[group].map(option => <option key={option.id} value={option.id}>{option.label}</option>)}</Select></label>)}
            {supervisor
              ? <label>Recruiter<Select aria-label="Recruiter" value={filters.staff_id} onChange={e => update('staff_id', e.target.value)}><option value="">All recruiters</option>{options?.staff.map(option => <option key={option.id} value={option.id}>{option.label}</option>)}</Select></label>
              : <div className="rv-own"><span>Recruiter</span><p title={`Reports are limited to ${store.me.name}`}><Lock size={14} aria-hidden="true" /><span>Only you</span></p></div>}
          </div>
        </section>

        {!data && !error && <div className="rv-skeleton" aria-hidden="true"><i className="rv-skeleton-block" /><span>Loading reports…</span></div>}

        {(error || exportError) && <div role="alert" className="reports-alert rv-alert"><span><AlertCircle size={15} aria-hidden="true" />{error || exportError}{error && data && ' Showing the last loaded result.'}</span>{error ? <button type="button" onClick={refresh}>Try again</button> : <button type="button" disabled={!!exporting} onClick={() => void exportFile(lastFormat)}>Try again</button>}</div>}

        {data && <div className={stale ? 'rv-body is-stale' : 'rv-body'} aria-busy={stale}>
          <div className="rv-report-body" role="tabpanel" aria-label={active.title}>
            {report === 'funnel' && <FunnelReport data={data} />}
            {report === 'staff' && <StaffReport data={data} />}
            {report === 'source' && <SourceReport data={data} />}
            {report === 'orders' && <OrdersReport data={data} go={go} />}
            {report === 'rejections' && <RejectionsReport data={data} />}
            {report === 'advance' && <AdvanceReport data={data} go={go} />}
          </div>
          <details className="reports-notes"><summary>How these numbers are worked out</summary>
            <h3>{active.title}</h3><ul>{active.how.map(line => <li key={line}>{line}</li>)}</ul>
            <h3>All reports</h3><ul><li>Dates include the first and last day. Times are in {data.timezone}. Updated {new Date(data.generatedAt).toLocaleString()}.</li>{data.notes.map(note => <li key={note}>{note}</li>)}</ul></details>
        </div>}
      </>}
  </div>
}
