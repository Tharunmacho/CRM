import { useEffect, useState } from 'react'
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { BarChart3, Download, FileSpreadsheet, GitBranch, RefreshCw, Users, Plane } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { useStore } from '@/shared/state/crm-store'
import { Button, Field, Input, Select } from '@/shared/ui/primitives'
import { PageHeader, Panel } from '@/shared/ui/display'
import { SummaryCards } from '@/shared/ui/workspace'
import { DataTable, type Column } from '@/shared/ui/table'
import { agency } from '@/app/config/agency'
import { downloadReport, useReportData, type ReportResult } from '../report-api'

type ReportKey = 'funnel' | 'staff' | 'source' | 'orders' | 'rejections' | 'advance'
const REPORTS: { key: ReportKey; label: string; note: string }[] = [
  { key: 'funnel', label: 'Pipeline', note: 'Applications mapped in the period. Closures measure actual drop-off; candidates still waiting remain active.' },
  { key: 'staff', label: 'Staff performance', note: 'Initial assignments, completed tasks and recorded events in the period. Stage hours include effective completions with a matching execution task.' },
  { key: 'source', label: 'Sources', note: 'Candidates first added in the period, grouped by their recorded source and email account.' },
  { key: 'orders', label: 'Order status', note: 'Applications mapped in the period, compared with each order’s total openings. Selected includes candidates who later deployed or closed after selection.' },
  { key: 'rejections', label: 'Rejections', note: 'Applications closed in the period by stage and reason. Unmapped applications are listed explicitly as administrative closures.' },
  { key: 'advance', label: 'Advance collections', note: 'Effective stage 8 collections recorded in the period. Each currency is totaled separately; unspecified legacy currency is never assumed.' },
]
const today = () => new Intl.DateTimeFormat('en-CA', { timeZone: agency.timezone, year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date())
function initialFrom() { const date = new Date(`${today()}T12:00:00Z`); date.setUTCDate(date.getUTCDate() - 89); return date.toISOString().slice(0, 10) }

function Table<T extends object>({ rows, columns, rowKey, onRowClick }: { rows: T[]; columns: [keyof T & string, string][]; rowKey: (row: T) => string; onRowClick?: (row: T) => void }) {
  const defs: Column<T>[] = columns.map(([key, header]) => ({ key, header, sortable: true, value: row => {
    const val = row[key]; return typeof val === 'number' ? val : val == null ? '—' : String(val)
  } }))
  return <DataTable rows={rows} columns={defs} rowKey={rowKey} pageSize={15} onRowClick={onRowClick} />
}

export default function ReportsPage() {
  const store = useStore()
  const navigate = useNavigate()
  const [report, setReport] = useState<ReportKey>('funnel')
  const [filters, setFilters] = useState({ from_date: initialFrom(), to_date: today(), client_id: '', order_id: '', designation_id: '', staff_id: '' })
  const [exporting, setExporting] = useState('')
  const [exportError, setExportError] = useState('')
  const query = new URLSearchParams(Object.entries(filters).filter(([, value]) => !!value)).toString()
  const { data, error, refresh } = useReportData<ReportResult>(`/reports?${query}`, store.me.id)
  const [options, setOptions] = useState<ReportResult['options'] | null>(null)
  useEffect(() => { if (data) setOptions(data.options) }, [data])
  const active = REPORTS.find(item => item.key === report)!
  const update = (key: keyof typeof filters, value: string) => setFilters(previous => ({ ...previous, [key]: value }))
  async function exportFile(format: 'xlsx' | 'pdf') {
    setExporting(format); setExportError('')
    try { await downloadReport(report, format, query) } catch (error) { setExportError(error instanceof Error ? error.message : 'Export failed.') } finally { setExporting('') }
  }
  return <div className="space-y-5">
    <PageHeader title="Reports" description="Recruitment outcomes, workload and collections in one view." actions={<div className="flex flex-wrap gap-2">
      <Button size="sm" icon={<RefreshCw size={14} />} onClick={refresh}>Refresh</Button>
      <Button size="sm" disabled={!data || !!exporting} icon={<FileSpreadsheet size={14} />} onClick={() => void exportFile('xlsx')}>{exporting === 'xlsx' ? 'Preparing…' : 'Excel'}</Button>
      <Button size="sm" disabled={!data || !!exporting} icon={<Download size={14} />} onClick={() => void exportFile('pdf')}>{exporting === 'pdf' ? 'Preparing…' : 'PDF'}</Button>
    </div>} />
    <Panel flush><div className="grid gap-3 p-4 sm:grid-cols-2 xl:grid-cols-6">
      <Field label="From date"><Input type="date" value={filters.from_date} max={filters.to_date} onChange={e => update('from_date', e.target.value)} /></Field>
      <Field label="To date"><Input type="date" value={filters.to_date} min={filters.from_date} onChange={e => update('to_date', e.target.value)} /></Field>
      {([['client_id', 'Client', 'clients'], ['order_id', 'Order', 'orders'], ['designation_id', 'Designation', 'designations']] as const).map(([key, label, group]) => <Field label={label} key={key}><Select value={filters[key]} onChange={e => update(key, e.target.value)}><option value="">All {label.toLowerCase()}s</option>{options?.[group].map(option => <option key={option.id} value={option.id}>{option.label}</option>)}</Select></Field>)}
      {store.can('all_reports') ? <Field label="Staff"><Select value={filters.staff_id} onChange={e => update('staff_id', e.target.value)}><option value="">All staff</option>{options?.staff.map(option => <option key={option.id} value={option.id}>{option.label}</option>)}</Select></Field> : <div className="self-end rounded-control bg-navy-50 p-3 text-xs text-navy-700">Your own numbers · {store.me.name}</div>}
    </div></Panel>
    {(error || exportError) && <div role="alert" className="rounded-panel border border-stamp-100 bg-stamp-50 p-4 text-sm text-stamp-700">{error || exportError}{error && data && ' Showing the last loaded result.'}</div>}
    {!data && !error && <Panel><p role="status" className="p-8 text-sm text-ink-500">Loading reports…</p></Panel>}
    {data && <>
      <SummaryCards items={[
        { label: 'Applications mapped', value: data.summary.applications, detail: 'In the selected period', icon: GitBranch },
        { label: 'Currently active', value: data.summary.active, detail: 'From mapped applications', icon: BarChart3, tone: 'violet' },
        { label: 'Deployed', value: data.summary.deployed, detail: 'From mapped applications', icon: Plane, tone: 'green' },
        { label: 'Candidates added', value: data.summary.intake, detail: 'Matching intake and filters', icon: Users, tone: 'amber' },
      ]} />
      <div role="tablist" aria-label="Report" className="flex gap-1 overflow-x-auto rounded-panel border border-rule bg-white p-1.5">{REPORTS.map(item => <button role="tab" aria-selected={report === item.key} key={item.key} onClick={() => { setReport(item.key); setExportError('') }} className={`whitespace-nowrap rounded-control px-4 py-2.5 text-sm font-medium ${report === item.key ? 'bg-navy-600 text-white' : 'text-ink-500 hover:bg-field'}`}>{item.label}</button>)}</div>
      <Panel flush><div className="border-b border-rule p-5"><h2 className="text-base font-semibold text-ink">{active.label}</h2><p className="mt-1 max-w-4xl text-xs leading-relaxed text-ink-500">{active.note}</p></div>
        {report === 'funnel' && <><div className="h-72 p-5"><ResponsiveContainer><BarChart data={data.pipeline}><CartesianGrid vertical={false} stroke="#e4eaf2" /><XAxis dataKey="stage" tickFormatter={n => `S${n}`} /><YAxis allowDecimals={false} /><Tooltip /><Bar isAnimationActive={false} dataKey="active" name="Active / waiting" fill="#3b6be3" stackId="status" /><Bar isAnimationActive={false} dataKey="progressed" name="Progressed" fill="#6e56cf" stackId="status" /><Bar isAnimationActive={false} dataKey="closed" name="Closed here" fill="#e9a23b" stackId="status" /></BarChart></ResponsiveContainer></div><Table rows={data.pipeline} rowKey={r => String(r.stage)} columns={[["stage", "Stage"], ["label", "Name"], ["reached", "Reached"], ["active", "Active / waiting"], ["progressed", "Progressed"], ["closed", "Closed here"], ["dropOffPercent", "Closure rate %"]]} /></>}
        {report === 'staff' && <><Table rows={data.staff} rowKey={r => r.id} columns={[["name", "Staff"], ["assigned", "Initial assignments"], ["completedTasks", "Tasks completed"], ["averageStageHours", "Avg stage hours"], ["durationSamples", "Stage samples"], ["escalations", "Escalations"], ["missedUpdates", "Missed daily updates"]]} /><h3 className="border-y border-rule p-4 text-sm font-semibold">Average time by stage</h3><Table rows={data.stageHours} rowKey={r => String(r.stage)} columns={[["stage", "Stage"], ["label", "Name"], ["completed", "Samples"], ["averageHours", "Average hours"]]} /></>}
        {report === 'source' && <div className="grid lg:grid-cols-2"><div><Table rows={data.sources} rowKey={r => r.source} columns={[["label", "Source"], ["count", "Candidates"]]} /></div><div className="border-t border-rule lg:border-l lg:border-t-0"><Table rows={data.accounts} rowKey={r => r.account} columns={[["account", "Email account"], ["count", "Candidates"]]} /></div></div>}
        {report === 'orders' && <Table rows={data.orders} rowKey={r => r.id} onRowClick={r => navigate(`/orders/${r.id}`)} columns={[["ref", "Order"], ["client", "Client"], ["company", "Company"], ["designation", "Designation"], ["status", "Status"], ["openings", "Openings"], ["matched", "Matched"], ["selected", "Selected"], ["deployed", "Deployed"]]} />}
        {report === 'rejections' && <Table rows={data.rejections} rowKey={r => `${r.stage}:${r.reason}`} columns={[["stage", "Stage"], ["label", "Reason"], ["count", "Closures"]]} />}
        {report === 'advance' && <><div className="flex flex-wrap gap-4 border-b border-rule p-5">{data.advanceTotals.length ? data.advanceTotals.map(total => <div key={total.currency} className="min-w-40 rounded-panel bg-navy-50 px-4 py-3"><p className="text-xs text-ink-500">{total.currency}</p><p className="mt-1 text-xl font-semibold tnum">{Number(total.amount).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</p></div>) : <p className="text-sm text-ink-500">No collections in this period.</p>}</div><Table rows={data.advances} rowKey={r => r.id} onRowClick={r => navigate(`/candidates/${r.candidateId}`)} columns={[["candidate", "Candidate"], ["order", "Order"], ["date", "Collected"], ["currency", "Currency"], ["amount", "Amount"]]} /><h3 className="border-y border-rule p-4 text-sm font-semibold">Collections by month and currency</h3><Table rows={data.advanceMonths} rowKey={r => `${r.month}:${r.currency}`} columns={[["month", "Month"], ["currency", "Currency"], ["amount", "Amount"]]} /></>}
      </Panel>
      <details className="rounded-panel border border-rule bg-white px-5 py-4 text-xs text-ink-500"><summary className="cursor-pointer font-medium text-ink">Scope and calculation notes</summary><p className="mt-3">Dates are inclusive in {data.timezone}. Updated {new Date(data.generatedAt).toLocaleString()}.</p><ul className="mt-2 list-disc space-y-1 pl-4">{data.notes.map(note => <li key={note}>{note}</li>)}</ul></details>
    </>}
  </div>
}
