import { useEffect, useMemo, useRef, useState } from 'react'
import { AlertCircle, ArrowDown, ArrowUp, CalendarDays, CheckCircle2, ChevronLeft, ChevronRight, Clock, Download, FileSpreadsheet, GitBranch, Loader2, Plane, RefreshCw, Send, UserX, Users } from 'lucide-react'
import { useSearchParams } from 'react-router-dom'
import { useStore } from '@/shared/state/crm-store'
import { useLiveStatus } from '@/shared/state/live'
import { agencyDay } from '@/shared/lib/task-time'
import { Button, Input } from '@/shared/ui/primitives'
import { EmptyState, PageHeader } from '@/shared/ui/display'
import { SummaryCards } from '@/shared/ui/workspace'
import { useReportData } from '@/features/reports/report-api'
import { downloadStaffReport, staffReportPath } from '@/features/staff-report/staff-report-api'
import { StaffDrawer } from '@/features/staff-report/staff-drawer'
import {
  STATUS_LABEL, clockTime, formatHours, formatPercent, funnelWidth, isWholeWeek, rangeLabel, scoreTone, severityRank,
  shiftRange, sortStaff, statusTone, validDay, weekOf, type SortKey, type StaffRow, type StaffWeeklyReport,
} from '@/features/staff-report/staff-report-model'
import '@/styles/staff-report.css'

export default function StaffWeeklyPage() {
  const store = useStore()
  if (store.me.role !== 'admin') {
    return <EmptyState title="Only the admin can see the weekly staff report" description="It shows each person's hours, tasks and candidate progress for the week." />
  }
  return <StaffWeeklyView />
}

const COLUMNS: { key: SortKey; label: string; title?: string }[] = [
  { key: 'name', label: 'Staff' }, { key: 'absent', label: 'Days', title: 'Days worked and days absent' },
  { key: 'hours', label: 'Hours', title: 'Hours worked against hours expected' }, { key: 'completion', label: 'Daily tasks' },
  { key: 'moves', label: 'Stage moves' }, { key: 'forwarded', label: 'Sent to client' }, { key: 'visa', label: 'Visa' },
  { key: 'deployed', label: 'Deployed' }, { key: 'stuck', label: 'Stuck' }, { key: 'score', label: 'Score' },
]

function StaffWeeklyView() {
  const store = useStore()
  const live = useLiveStatus()
  const [params, setParams] = useSearchParams()
  const today = agencyDay(new Date(), store.agencyTimezone)
  const defaults = weekOf(today)
  const from = validDay(params.get('from')) ? params.get('from')! : defaults.from
  const to = validDay(params.get('to')) && params.get('to')! >= from ? params.get('to')! : validDay(params.get('from')) ? weekOf(from).to : defaults.to
  const range = useMemo(() => ({ from, to }), [from, to])
  const staffId = params.get('staff') || ''
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

  const { data: fresh, error, refresh, refreshing } = useReportData<StaffWeeklyReport>(staffReportPath(range), store.me.id)
  // Keep the previous week on screen (dimmed) while another is loading so the page does not jump.
  const last = useRef<StaffWeeklyReport | null>(null)
  if (fresh) last.current = fresh
  const data = fresh ?? last.current
  const stale = !fresh && !!data

  const [sort, setSort] = useState<{ key: SortKey; direction: 'asc' | 'desc' }>({ key: 'score', direction: 'asc' })
  const [exporting, setExporting] = useState<'' | 'xlsx' | 'csv'>('')
  const [exportError, setExportError] = useState('')
  const [flash, setFlash] = useState(false)
  const seen = useRef('')
  useEffect(() => {
    if (!fresh) return undefined
    const changed = !!seen.current && seen.current !== fresh.generatedAt
    seen.current = fresh.generatedAt
    if (!changed) return undefined
    setFlash(true)
    const timer = window.setTimeout(() => setFlash(false), 1600)
    return () => { window.clearTimeout(timer); setFlash(false) }
  }, [fresh])

  const rows = useMemo(() => data ? sortStaff(data.staff, sort.key, sort.direction) : [], [data, sort])
  const highlights = useMemo(() => data ? [...data.highlights].sort((a, b) => severityRank(a.severity) - severityRank(b.severity)) : [], [data])
  const open = data && staffId ? data.staff.find(item => item.id === staffId) : undefined

  async function exportFile(format: 'xlsx' | 'csv') {
    setExporting(format); setExportError('')
    try { await downloadStaffReport(format, range) }
    catch (failure) { setExportError(failure instanceof Error ? failure.message : 'We could not create the file. Please try again.') }
    finally { setExporting('') }
  }
  const sortBy = (key: SortKey) => setSort(previous => ({ key, direction: previous.key === key ? (previous.direction === 'asc' ? 'desc' : 'asc') : key === 'name' ? 'asc' : 'desc' }))
  const whole = isWholeWeek(range)
  const thisWeek = range.from === defaults.from && range.to === defaults.to
  const zone = data?.timezone ?? store.agencyTimezone

  return <div className="sw">
    <PageHeader title="Staff report" description="Each person's attendance, hours, daily tasks and candidates from prescreening to deployment, worked out from the live records." actions={<>
      <LiveBadge status={live} updatedAt={data?.generatedAt} zone={zone} flash={flash} />
      <Button size="sm" icon={<RefreshCw size={14} className={refreshing ? 'animate-spin' : undefined} />} disabled={refreshing} onClick={refresh}>{refreshing ? 'Refreshing' : 'Refresh'}</Button>
      <Button size="sm" disabled={!data || !!exporting} icon={exporting === 'xlsx' ? <Loader2 size={14} className="animate-spin" /> : <FileSpreadsheet size={14} />} onClick={() => void exportFile('xlsx')}>Excel</Button>
      <Button size="sm" disabled={!data || !!exporting} icon={exporting === 'csv' ? <Loader2 size={14} className="animate-spin" /> : <Download size={14} />} onClick={() => void exportFile('csv')}>CSV</Button>
    </>} />

    <section className="sw-card sw-period" aria-label="Choose the period">
      <div className="sw-weekpick">
        <button type="button" className="sw-step" aria-label="Previous period" onClick={() => setRange(shiftRange(range, -1))}><ChevronLeft size={18} /></button>
        <div className="sw-weeklabel"><CalendarDays size={16} aria-hidden="true" /><div><strong>{rangeLabel(range)}</strong><span>{whole ? (thisWeek ? 'This week, Monday to Sunday' : 'Monday to Sunday') : `${data?.range.days.length ?? ''} days`}</span></div></div>
        <button type="button" className="sw-step" aria-label="Next period" onClick={() => setRange(shiftRange(range, 1))}><ChevronRight size={18} /></button>
        <Button size="sm" disabled={thisWeek} onClick={() => setRange(defaults)}>This week</Button>
      </div>
      <div className="sw-custom">
        <label>From<Input type="date" aria-label="From date" value={range.from} max={range.to} onChange={e => validDay(e.target.value) && setRange({ from: e.target.value, to: range.to })} /></label>
        <label>To<Input type="date" aria-label="To date" value={range.to} min={range.from} onChange={e => validDay(e.target.value) && setRange({ from: range.from, to: e.target.value })} /></label>
      </div>
    </section>

    {(error || exportError) && <div role="alert" className="sw-alert"><span><AlertCircle size={15} aria-hidden="true" />{error || exportError}{error && data && ' Showing the last loaded result.'}</span>
      {error && <button type="button" onClick={refresh}>Try again</button>}</div>}

    {!data && !error && <div className="sw-skeleton" role="status" aria-label="Loading the staff report"><div>{[0, 1, 2, 3, 4, 5].map(i => <i key={i} />)}</div><i className="sw-skeleton-block" /></div>}

    {data && <div className={stale ? 'sw-body is-stale' : 'sw-body'} aria-busy={stale}>
      <SummaryCards items={[
        { label: 'Staff who worked', value: `${data.summary.staffWorked} of ${data.summary.staffTotal}`, detail: data.summary.staffAbsent ? `${data.summary.staffAbsent} did not work` : data.summary.staffPending ? `${data.summary.staffPending} not in yet today` : 'Everyone scheduled came in', icon: data.summary.staffAbsent ? UserX : Users, tone: data.summary.staffAbsent ? 'red' : 'green' },
        { label: 'Hours worked', value: formatHours(data.summary.hoursWorked), detail: `${formatHours(data.summary.hoursExpected)} expected, ${formatHours(data.summary.activeHours)} active in the CRM`, icon: Clock, tone: 'blue' },
        { label: 'Daily tasks completed', value: data.summary.tasksAvailable && data.summary.tasksAssigned ? formatPercent(data.summary.taskCompletionPercent) : 'No data yet', detail: data.summary.tasksAssigned ? `${data.summary.tasksCompleted} of ${data.summary.tasksAssigned} done, ${data.summary.tasksPostponed} postponed, ${data.summary.tasksOverdue} overdue` : 'No daily tasks were handed out in this period', icon: CheckCircle2, tone: 'violet' },
        { label: 'Sent to client', value: data.summary.forwarded.toLocaleString(), detail: `${data.summary.interviewsScheduled} interviews set, ${data.summary.selected} selected`, icon: Send, tone: 'amber' },
        { label: 'Visa received', value: data.summary.visaReceived.toLocaleString(), detail: `${data.summary.visaStamped} stamped, ${data.summary.stageMoves} stage moves in all`, icon: GitBranch, tone: 'blue' },
        { label: 'Deployed', value: data.summary.deployed.toLocaleString(), detail: data.summary.averageScore == null ? 'No team score yet' : `Team score ${data.summary.averageScore} of 100`, icon: Plane, tone: 'green' },
      ]} />

      <section className="sw-card" aria-label="Questions to ask">
        <header className="sw-head"><div><h2>Questions to ask</h2><p>Where the records show something to follow up. Select a name for the detail.</p></div><span className="sw-count">{highlights.length}</span></header>
        {highlights.length === 0
          ? <p className="sw-empty">Nothing stands out in this period.</p>
          : <ul className="sw-flags">{highlights.map(item => <li key={item.id} className={`is-${item.severity}`}>
            <span className="sw-dot" aria-hidden="true" />
            <div><button type="button" className="sw-link" onClick={() => openStaff(item.staffId)}>{item.staffName}</button><b>{item.title}</b><p>{item.detail}</p></div>
          </li>)}</ul>}
      </section>

      <section className="sw-card" aria-label="Staff">
        <header className="sw-head"><div><h2>Staff</h2><p>One row per person. Select a row to see every day and every candidate.</p></div></header>
        {rows.length === 0 ? <p className="sw-empty">No staff accounts to report on.</p> : <div className="sw-scroll"><table className="sw-table sw-staff">
          <thead><tr>{COLUMNS.map(column => <th key={column.key} aria-sort={sort.key === column.key ? (sort.direction === 'asc' ? 'ascending' : 'descending') : 'none'} title={column.title}>
            <button type="button" onClick={() => sortBy(column.key)}>{column.label}{sort.key === column.key && (sort.direction === 'asc' ? <ArrowUp size={12} /> : <ArrowDown size={12} />)}</button></th>)}</tr></thead>
          <tbody>{rows.map(row => <StaffLine key={row.id} row={row} onOpen={() => openStaff(row.id)} />)}</tbody>
        </table></div>}
        <details className="sw-how"><summary>How the score is worked out</summary>
          <ul>{data.formula.lines.map(line => <li key={line}>{line}</li>)}</ul>
          <p>75 and over is shown green, 50 to 74 amber, below 50 red. It is a prompt for a conversation, not a verdict: check the parts and the days first.</p></details>
      </section>

      <section className="sw-card" aria-label="Stages by staff">
        <header className="sw-head"><div><h2>Stage by stage</h2><p>Each cell: candidates waiting in that stage now, and in brackets how many moved out of it in this period.</p></div></header>
        <div className="sw-scroll"><table className="sw-table sw-matrix">
          <thead><tr><th>Staff</th>{data.funnel.map(stage => <th key={stage.stage} className="num" title={stage.label}><span>{stage.stage}</span><small>{stage.label}</small></th>)}</tr></thead>
          <tbody>{rows.map(row => <tr key={row.id}>
            <th><button type="button" className="sw-link" onClick={() => openStaff(row.id)}>{row.name}</button></th>
            {data.funnel.map(stage => { const now = row.stages.inStageNow[stage.stage] ?? 0; const moved = row.stages.movesByStage[stage.stage] ?? 0
              return <td key={stage.stage} className="num">{now || moved ? <><b>{now}</b>{moved ? <em>({moved})</em> : null}</> : ''}</td> })}
          </tr>)}</tbody>
          <tfoot><tr><th>Team</th>{data.funnel.map(stage => <td key={stage.stage} className="num"><b>{stage.inStageNow || ''}</b>{stage.movesInPeriod ? <em>({stage.movesInPeriod})</em> : null}</td>)}</tr></tfoot>
        </table></div>
      </section>

      <section className="sw-card" aria-label="Team funnel">
        <header className="sw-head"><div><h2>Team funnel</h2><p>Every candidate placed on an order, owned by staff, counted at the furthest stage reached. The percentage is how many of the previous stage went on.</p></div></header>
        <ol className="sw-funnel">{data.funnel.map(stage => <li key={stage.stage}>
          <span className="sw-fname"><b>{stage.stage}</b>{stage.label}</span>
          <span className="sw-ftrack" aria-hidden="true"><i style={{ width: `${funnelWidth(stage.reached, data.funnel[0]?.reached ?? 0)}%` }} /></span>
          <span className="sw-fnum"><b>{stage.reached}</b> reached</span>
          <span className="sw-fconv">{stage.conversionPercent == null ? '' : `${formatPercent(stage.conversionPercent)} of previous`}{stage.lostFromPrevious ? <em>{stage.lostFromPrevious} lost</em> : null}</span>
          <span className="sw-fmeta">{stage.inStageNow} waiting, {stage.closedHere} closed here, {stage.movesInPeriod} done this period</span>
        </li>)}</ol>
      </section>

      <details className="sw-notes"><summary>How these numbers are worked out</summary>
        <ul>{data.notes.map(note => <li key={note}>{note}</li>)}<li>Dates include the first and last day. Times are in {data.timezone}. Updated {clockTime(data.generatedAt, data.timezone)}.</li></ul></details>
    </div>}

    {open && data && <StaffDrawer row={open} report={data} onClose={() => openStaff('')} />}
  </div>
}

function LiveBadge({ status, updatedAt, zone, flash }: { status: string; updatedAt?: string; zone?: string; flash: boolean }) {
  const up = status === 'up'
  return <span className={`sw-live${up ? ' is-up' : ''}${flash ? ' is-flash' : ''}`} role="status" title={up ? 'Updates arrive the moment attendance, tasks or stages change.' : 'The live connection is not up; the report re-reads every minute.'}>
    <i aria-hidden="true" />{up ? 'Live' : status === 'connecting' ? 'Connecting' : 'Refreshes every minute'}
    {updatedAt && <small>updated {new Date(updatedAt).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', second: '2-digit', timeZone: zone })}</small>}
  </span>
}

function StaffLine({ row, onOpen }: { row: StaffRow; onOpen: () => void }) {
  const a = row.attendance
  const t = row.tasks
  const hoursShare = a.expectedHours ? Math.min(100, Math.round((a.hoursWorked / a.expectedHours) * 100)) : a.hoursWorked ? 100 : 0
  return <tr className={row.status === 'no_attendance' ? 'is-flag' : undefined} onClick={onOpen}>
    <td><button type="button" className="sw-link" onClick={event => { event.stopPropagation(); onOpen() }}>{row.name}</button>
      <span className={`sw-chip is-${statusTone(row.status)}`}>{STATUS_LABEL[row.status]}</span>{!row.active && <span className="sw-chip is-muted">Inactive</span>}</td>
    <td><b>{a.workedDays}</b> worked{a.absentDays ? <span className="sw-bad"> - {a.absentDays} absent</span> : null}
      {a.leaveDays ? <small>{a.leaveDays} on leave</small> : null}{a.missingCheckouts ? <small>{a.missingCheckouts} no check-out</small> : null}</td>
    <td><b>{formatHours(a.hoursWorked)}</b>{a.expectedHours ? <small>of {formatHours(a.expectedHours)}</small> : null}
      <i className="sw-meter" aria-hidden="true"><b style={{ width: `${hoursShare}%` }} /></i>
      {a.activeHours ? <small>{formatHours(a.activeHours)} active in the CRM</small> : null}</td>
    <td>{t.available && t.assigned ? <><b>{t.completed}</b> of {t.assigned}<small>{formatPercent(t.completionPercent)}{t.postponed ? ` - ${t.postponed} postponed` : ''}{t.overdue ? ` - ${t.overdue} overdue` : ''}</small></> : <span className="sw-muted">{t.available ? 'None assigned' : 'No data yet'}</span>}</td>
    <td className="num">{row.stages.moves || <span className="sw-muted">0</span>}</td>
    <td className="num">{row.outcomes.forwarded}</td>
    <td className="num">{row.outcomes.visaReceived}{row.outcomes.visaStamped ? <small>{row.outcomes.visaStamped} stamped</small> : null}</td>
    <td className="num">{row.outcomes.deployed}</td>
    <td className="num">{row.stuck.count ? <span className="sw-bad">{row.stuck.count}<small>oldest {row.stuck.oldestDays} d</small></span> : <span className="sw-muted">0</span>}</td>
    <td className="num"><span className={`sw-score-pill is-${scoreTone(row.score)}`} title={row.scoreParts.map(p => `${p.label}: ${p.detail}`).join('\n')}>{row.score ?? '-'}</span></td>
  </tr>
}
