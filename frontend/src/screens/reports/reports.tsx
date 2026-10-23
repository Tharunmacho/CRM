import { useEffect, useState } from 'react'
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { BarChart3, Download, FileSpreadsheet, GitBranch, RefreshCw, Users, Plane } from 'lucide-react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { useStore } from '@/shared/state/crm-store'
import { Button, Input, Select } from '@/shared/ui/primitives'
import { PageHeader, Panel } from '@/shared/ui/display'
import { Tabs } from '@/shared/ui/overlay'
import { SummaryCards } from '@/shared/ui/workspace'
import { DataTable, type Column } from '@/shared/ui/table'
import { agencyDay } from '@/shared/lib/task-time'
import { downloadReport, useReportData, type ReportResult } from '@/features/reports/report-api'
import { validDateFilter } from '@/features/staff/task-filters'
import '@/styles/reports-page.css'

type ReportKey = 'funnel' | 'staff' | 'source' | 'orders' | 'rejections' | 'advance'
const REPORTS: { key: ReportKey; label: string; note: string }[] = [
  { key: 'funnel', label: 'Pipeline', note: 'Applications mapped in the period. Closures show actual drop-off; waiting candidates stay active.' },
  { key: 'staff', label: 'Staff performance', note: 'Assignments, completed tasks and recorded events in the period.' },
  { key: 'source', label: 'Sources', note: 'Candidates first added in the period, by source and email account.' },
  { key: 'orders', label: 'Order status', note: 'Applications mapped in the period against each order’s openings.' },
  { key: 'rejections', label: 'Rejections', note: 'Applications closed in the period, by stage and reason.' },
  { key: 'advance', label: 'Advance collections', note: 'Stage 8 collections in the period. Each currency is totalled separately.' },
]
function initialFrom(today: string) { const date = new Date(`${today}T12:00:00Z`); date.setUTCDate(date.getUTCDate() - 89); return date.toISOString().slice(0, 10) }

function Table<T extends object>({ rows, columns, rowKey, onRowClick }: { rows: T[]; columns: [keyof T & string, string][]; rowKey: (row: T) => string; onRowClick?: (row: T) => void }) {
  const defs: Column<T>[] = columns.map(([key, header]) => ({ key, header, sortable: true, align: typeof rows[0]?.[key] === 'number' ? 'right' : undefined, value: row => {
    const val = row[key]; return typeof val === 'number' ? val : val == null ? '—' : String(val)
  } }))
  return <DataTable rows={rows} columns={defs} rowKey={rowKey} pageSize={15} onRowClick={onRowClick} />
}

export default function ReportsPage() {
  const store = useStore()
  const navigate = useNavigate()
  const [params] = useSearchParams()
  const linkedStaff = store.can('all_reports') ? params.get('staff') || '' : ''
  // The server's agency day, not the browser's zone or a compiled-in constant.
  const today = agencyDay(new Date(), store.agencyTimezone)
  const linkedFrom = validDateFilter(params.get('from')) || initialFrom(today)
  const linkedTo = validDateFilter(params.get('to')) || today
  const [report, setReport] = useState<ReportKey>(linkedStaff ? 'staff' : 'funnel')
  const [filters, setFilters] = useState({ from_date: linkedFrom, to_date: linkedTo, client_id: '', order_id: '', designation_id: '', staff_id: linkedStaff })
  useEffect(() => {
    setFilters(previous => ({ ...previous, from_date: linkedFrom, to_date: linkedTo, staff_id: linkedStaff }))
    if (linkedStaff) setReport('staff')
  }, [linkedStaff, linkedFrom, linkedTo])
  const [exporting, setExporting] = useState('')
  const [exportError, setExportError] = useState('')
  const query = new URLSearchParams(Object.entries(filters).filter(([, value]) => !!value)).toString()
  const { data, error, refresh, refreshing } = useReportData<ReportResult>(`/reports?${query}`, store.me.id)
  const [options, setOptions] = useState<ReportResult['options'] | null>(null)
  useEffect(() => { if (data) setOptions(data.options) }, [data])
  const active = REPORTS.find(item => item.key === report)!
  const update = (key: keyof typeof filters, value: string) => setFilters(previous => ({ ...previous, [key]: value }))
  async function exportFile(format: 'xlsx' | 'pdf') {
    setExporting(format); setExportError('')
    try { await downloadReport(report, format, query) } catch (error) { setExportError(error instanceof Error ? error.message : 'Export failed.') } finally { setExporting('') }
  }
  return <div className="reports-page">
    <PageHeader title="Reports" description="Recruitment outcomes, workload and collections." actions={<div className="flex flex-wrap gap-2">
      <Button size="sm" icon={<RefreshCw size={14} />} disabled={refreshing} onClick={refresh}>{refreshing ? 'Refreshing…' : 'Refresh'}</Button>
      <Button size="sm" disabled={!data || !!exporting} icon={<FileSpreadsheet size={14} />} onClick={() => void exportFile('xlsx')}>{exporting === 'xlsx' ? 'Preparing…' : 'Excel'}</Button>
      <Button size="sm" disabled={!data || !!exporting} icon={<Download size={14} />} onClick={() => void exportFile('pdf')}>{exporting === 'pdf' ? 'Preparing…' : 'PDF'}</Button>
    </div>} />
    <div className="reports-toolbar" aria-label="Report filters">
      <label className="reports-date">From<Input type="date" aria-label="From date" value={filters.from_date} max={filters.to_date} onChange={e => update('from_date', e.target.value)} /></label>
      <label className="reports-date">To<Input type="date" aria-label="To date" value={filters.to_date} min={filters.from_date} onChange={e => update('to_date', e.target.value)} /></label>
      {([['client_id', 'Client', 'clients'], ['order_id', 'Order', 'orders'], ['designation_id', 'Designation', 'designations']] as const).map(([key, label, group]) => <Select key={key} aria-label={label} className="reports-select" value={filters[key]} onChange={e => update(key, e.target.value)}><option value="">All {label.toLowerCase()}s</option>{options?.[group].map(option => <option key={option.id} value={option.id}>{option.label}</option>)}</Select>)}
      {store.can('all_reports')
        ? <Select aria-label="Staff" className="reports-select" value={filters.staff_id} onChange={e => update('staff_id', e.target.value)}><option value="">All staff</option>{options?.staff.map(option => <option key={option.id} value={option.id}>{option.label}</option>)}</Select>
        : <span className="reports-own">Your own numbers · {store.me.name}</span>}
    </div>
    {(error || exportError) && <div role="alert" className="reports-alert">{error || exportError}{error && data && ' Showing the last loaded result.'}</div>}
    {!data && !error && <Panel><p role="status" className="p-8 text-sm text-ink-500">Loading reports…</p></Panel>}
    {data && <>
      <SummaryCards items={[
        { label: 'Applications mapped', value: data.summary.applications, detail: 'In the selected period', icon: GitBranch },
        { label: 'Currently active', value: data.summary.active, detail: 'From mapped applications', icon: BarChart3, tone: 'violet' },
        { label: 'Deployed', value: data.summary.deployed, detail: 'From mapped applications', icon: Plane, tone: 'green' },
        { label: 'Candidates added', value: data.summary.intake, detail: 'Matching intake and filters', icon: Users, tone: 'amber' },
      ]} />
      <Panel flush>
        <Tabs tabs={REPORTS.map(({ key, label }) => ({ key, label }))} active={report} onChange={key => { setReport(key as ReportKey); setExportError('') }} className="px-2" />
        <div className="reports-section-head"><h2>{active.label}</h2><p>{active.note}</p></div>
        {report === 'funnel' && <><div className="h-72 px-5 pb-5"><ResponsiveContainer><BarChart data={data.pipeline}><CartesianGrid vertical={false} stroke="#e4eaf2" /><XAxis dataKey="stage" tickFormatter={n => `S${n}`} /><YAxis allowDecimals={false} /><Tooltip /><Bar isAnimationActive={false} dataKey="active" name="Active / waiting" fill="#3b6be3" stackId="status" /><Bar isAnimationActive={false} dataKey="progressed" name="Progressed" fill="#6e56cf" stackId="status" /><Bar isAnimationActive={false} dataKey="closed" name="Closed here" fill="#e9a23b" stackId="status" /></BarChart></ResponsiveContainer></div><Table rows={data.pipeline} rowKey={r => String(r.stage)} columns={[["stage", "Stage"], ["label", "Name"], ["reached", "Reached"], ["active", "Active / waiting"], ["progressed", "Progressed"], ["closed", "Closed here"], ["dropOffPercent", "Closure rate %"]]} /></>}
        {report === 'staff' && <><Table rows={data.staff} rowKey={r => r.id} columns={[["name", "Staff"], ["assigned", "Initial assignments"], ["completedTasks", "Tasks completed"], ["averageStageHours", "Avg stage hours"], ["durationSamples", "Stage samples"], ["escalations", "Escalations"], ["missedUpdates", "Missed daily updates"]]} /><h3 className="reports-subhead">Average time by stage</h3><Table rows={data.stageHours} rowKey={r => String(r.stage)} columns={[["stage", "Stage"], ["label", "Name"], ["completed", "Samples"], ["averageHours", "Average hours"]]} /></>}
        {report === 'source' && <div className="grid lg:grid-cols-2"><div><Table rows={data.sources} rowKey={r => r.source} columns={[["label", "Source"], ["count", "Candidates"]]} /></div><div className="border-t border-rule lg:border-l lg:border-t-0"><Table rows={data.accounts} rowKey={r => r.account} columns={[["account", "Email account"], ["count", "Candidates"]]} /></div></div>}
        {report === 'orders' && <Table rows={data.orders} rowKey={r => r.id} onRowClick={r => navigate(`/orders/${r.id}`)} columns={[["ref", "Order"], ["client", "Client"], ["company", "Company"], ["designation", "Designation"], ["status", "Status"], ["openings", "Openings"], ["matched", "Matched"], ["selected", "Selected"], ["deployed", "Deployed"]]} />}
        {report === 'rejections' && <Table rows={data.rejections} rowKey={r => `${r.stage}:${r.reason}`} columns={[["stage", "Stage"], ["label", "Reason"], ["count", "Closures"]]} />}
        {report === 'advance' && <><div className="reports-totals">{data.advanceTotals.length ? data.advanceTotals.map(total => <div key={total.currency}><p>{total.currency}</p><strong className="tnum">{Number(total.amount).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</strong></div>) : <p className="text-sm text-ink-500">No collections in this period.</p>}</div><Table rows={data.advances} rowKey={r => r.id} onRowClick={r => navigate(`/candidates/${r.candidateId}`)} columns={[["candidate", "Candidate"], ["order", "Order"], ["date", "Collected"], ["currency", "Currency"], ["amount", "Amount"]]} /><h3 className="reports-subhead">Collections by month and currency</h3><Table rows={data.advanceMonths} rowKey={r => `${r.month}:${r.currency}`} columns={[["month", "Month"], ["currency", "Currency"], ["amount", "Amount"]]} /></>}
      </Panel>
      <details className="reports-notes"><summary>Scope and calculation notes</summary><p>Dates are inclusive in {data.timezone}. Updated {new Date(data.generatedAt).toLocaleString()}.</p><ul>{data.notes.map(note => <li key={note}>{note}</li>)}</ul></details>
    </>}
  </div>
}
