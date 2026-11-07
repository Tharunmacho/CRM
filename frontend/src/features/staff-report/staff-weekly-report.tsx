/**
 * The weekly staff report, built to sit inside the Reports page (Staff performance tab, admin only). It has no page
 * header of its own: Reports owns the title, the refresh button and the export area, and shows these pieces in them.
 *
 *  - useStaffWeekly: reads the week, remembers the last one while another loads, and builds the Excel/CSV files.
 *  - StaffWeeklyExport: Excel and CSV buttons with the Live badge, in the same slot as the other tabs' export.
 *  - StaffWeeklyPeriod: the week picker, in the same slot as the other tabs' Filters card.
 *  - StaffWeeklyBody: cards, questions, staff table, stage matrix, team funnel and the staff drawer.
 */
import { Fragment, useEffect, useMemo, useRef, useState } from 'react'
import { AlertCircle, ArrowDown, ArrowUp, CalendarDays, CheckCircle2, ChevronLeft, ChevronRight, Clock, Download, FileSpreadsheet, GitBranch, Loader2, PhoneCall, Plane, Send, UserCheck, UserX, Users } from 'lucide-react'
import { useStore } from '@/shared/state/crm-store'
import { useLiveStatus } from '@/shared/state/live'
import { Button, Input } from '@/shared/ui/primitives'
import { SummaryCards } from '@/shared/ui/workspace'
import { LEAD_REPORT_TOPICS, useReportData } from '@/features/reports/report-api'
import { downloadStaffReport, staffReportPath } from './staff-report-api'
import { StaffDrawer } from './staff-drawer'
import {
  STATUS_LABEL, clockTime, formatHours, formatPercent, funnelWidth, hasLeadSheet, hasNotesFigure, isWholeWeek, leadSummaryCards, leadsCalledText, leadsTone,
  rangeLabel, scoreTone, severityRank, shiftRange, sortStaff, statusTone, validDay, weekOf, type SortKey, type StaffRow, type StaffWeeklyReport,
} from './staff-report-model'
import '@/styles/staff-report.css'

type Range = { from: string; to: string }

/** Everything the three pieces share: the week's data, its loading state and the file builder. */
export function useStaffWeekly(range: Range, enabled: boolean) {
  const store = useStore()
  const { data: fresh, error, refresh, refreshing } = useReportData<StaffWeeklyReport>(enabled ? staffReportPath(range) : null, store.me.id, '', LEAD_REPORT_TOPICS)
  // Keep the previous week on screen (dimmed) while another is loading so the page does not jump.
  const last = useRef<StaffWeeklyReport | null>(null)
  if (fresh) last.current = fresh
  const data = enabled ? fresh ?? last.current : null
  const stale = !fresh && !!data

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

  async function exportFile(format: 'xlsx' | 'csv') {
    setExporting(format); setExportError('')
    try { await downloadStaffReport(format, range) }
    catch (failure) { setExportError(failure instanceof Error ? failure.message : 'We could not create the file. Please try again.') }
    finally { setExporting('') }
  }
  return { data, stale, error, refresh, refreshing, exporting, exportError, exportFile, flash, zone: data?.timezone ?? store.agencyTimezone }
}
export type StaffWeekly = ReturnType<typeof useStaffWeekly>

/** Excel and CSV buttons, and the Live badge where the other reports print their file note. */
export function StaffWeeklyExport({ weekly }: { weekly: StaffWeekly }) {
  const live = useLiveStatus()
  const { data, exporting, exportFile } = weekly
  return <div className="rv-export">
    <div role="group" aria-label="Export this report">
      <Button size="sm" disabled={!data || !!exporting} icon={exporting === 'xlsx' ? <Loader2 size={14} className="animate-spin" /> : <FileSpreadsheet size={14} />} onClick={() => void exportFile('xlsx')}>{exporting === 'xlsx' ? 'Preparing Excel…' : 'Export Excel'}</Button>
      <Button size="sm" disabled={!data || !!exporting} icon={exporting === 'csv' ? <Loader2 size={14} className="animate-spin" /> : <Download size={14} />} onClick={() => void exportFile('csv')}>{exporting === 'csv' ? 'Preparing CSV…' : 'Export CSV'}</Button>
    </div>
    <LiveBadge status={live} updatedAt={data?.generatedAt} zone={weekly.zone} flash={weekly.flash} />
  </div>
}

/** The week picker. It replaces the Filters card on this tab: only dates apply here. */
export function StaffWeeklyPeriod({ range, today, onRange, weekly }: { range: Range; today: string; onRange: (next: Range) => void; weekly: StaffWeekly }) {
  const current = weekOf(today)
  const whole = isWholeWeek(range)
  const thisWeek = range.from === current.from && range.to === current.to
  return <section className="rv-card rv-filters sw-period" aria-label="Choose the period">
    <header>
      <div className="rv-filters-title"><span className="rv-icon"><CalendarDays size={16} /></span><div><h2>Period</h2><p>A week, or any dates up to 62 days. The client, order and job title filters do not apply here.</p></div></div>
      <div className="rv-filters-actions"><Button size="sm" disabled={thisWeek} onClick={() => onRange(current)}>This week</Button></div>
    </header>
    <div className="sw-picker">
      <div className="sw-weekpick">
        <button type="button" className="sw-step" aria-label="Previous period" onClick={() => onRange(shiftRange(range, -1))}><ChevronLeft size={18} /></button>
        <div className="sw-weeklabel"><strong>{rangeLabel(range)}</strong><span>{whole ? (thisWeek ? 'This week, Monday to Sunday' : 'Monday to Sunday') : `${weekly.data?.range.days.length ?? ''} days`}</span></div>
        <button type="button" className="sw-step" aria-label="Next period" onClick={() => onRange(shiftRange(range, 1))}><ChevronRight size={18} /></button>
      </div>
      <div className="sw-custom">
        <label>From<Input type="date" aria-label="From date" value={range.from} max={range.to} onChange={e => validDay(e.target.value) && onRange({ from: e.target.value, to: range.to })} /></label>
        <label>To<Input type="date" aria-label="To date" value={range.to} min={range.from} onChange={e => validDay(e.target.value) && onRange({ from: range.from, to: e.target.value })} /></label>
      </div>
    </div>
  </section>
}

const LEAD_COLUMNS: { key?: SortKey; label: string; title?: string }[] = [
  { key: 'name', label: 'Staff' },
  { key: 'leadsAssigned', label: 'Assigned', title: 'Leads given to the person in this period, and how many of them were called' },
  { label: 'Interested', title: 'Leads the person marked interested in this period' },
  { key: 'leadCalls', label: 'Calls', title: 'Lead calls the person logged in this period, and how many different leads they were on' },
  { label: 'Resumes', title: 'Leads of the person that received a resume in this period' },
  { key: 'leadsConverted', label: 'Converted', title: 'Leads of the person converted to candidates in this period, and the share of leads worked' },
  { key: 'leadsOverdue', label: 'Overdue', title: 'Open leads whose follow-up date has already passed (as of now)' },
]

const NOTES_COLUMN = { label: 'Notes written', title: 'Recruiter notes the person wrote on candidates in this period' }

const COLUMNS: { key: SortKey; label: string; title?: string }[] = [
  { key: 'name', label: 'Staff' }, { key: 'absent', label: 'Days', title: 'Days worked and days absent' },
  { key: 'hours', label: 'Hours', title: 'Hours worked against hours expected' }, { key: 'completion', label: 'Daily tasks' },
  { key: 'moves', label: 'Stage moves' }, { key: 'forwarded', label: 'Sent to client' }, { key: 'visa', label: 'Visa' },
  { key: 'deployed', label: 'Deployed' }, { key: 'stuck', label: 'Stuck' }, { key: 'score', label: 'Score' },
]

export function StaffWeeklyBody({ weekly, staffId, onOpenStaff }: { weekly: StaffWeekly; staffId: string; onOpenStaff: (id: string) => void }) {
  const { data, error, refresh, stale, exportError } = weekly
  const [sort, setSort] = useState<{ key: SortKey; direction: 'asc' | 'desc' }>({ key: 'score', direction: 'asc' })
  const rows = useMemo(() => data ? sortStaff(data.staff, sort.key, sort.direction) : [], [data, sort])
  const highlights = useMemo(() => data ? [...data.highlights].sort((a, b) => severityRank(a.severity) - severityRank(b.severity)) : [], [data])
  const open = data && staffId ? data.staff.find(item => item.id === staffId) : undefined
  const notes = hasNotesFigure(rows)
  const leadSheet = !!data && hasLeadSheet(data)
  const leadCards = data ? leadSummaryCards(data.summary) : null
  const sortBy = (key: SortKey) => setSort(previous => ({ key, direction: previous.key === key ? (previous.direction === 'asc' ? 'desc' : 'asc') : key === 'name' ? 'asc' : 'desc' }))

  return <>
    {(error || exportError) && <div role="alert" className="reports-alert rv-alert"><span><AlertCircle size={15} aria-hidden="true" />{error || exportError}{error && data && ' Showing the last loaded result.'}</span>
      {error && <button type="button" onClick={refresh}>Try again</button>}</div>}

    {!data && !error && <div className="sw-skeleton" role="status" aria-label="Loading the staff report"><div>{[0, 1, 2, 3, 4, 5].map(i => <i key={i} />)}</div><i className="sw-skeleton-block" /></div>}

    {data && <div className={stale ? 'sw sw-body is-stale' : 'sw sw-body'} aria-busy={stale}>
      <SummaryCards items={[
        { label: 'Staff who worked', value: `${data.summary.staffWorked} of ${data.summary.staffTotal}`, detail: data.summary.staffAbsent ? `${data.summary.staffAbsent} did not work` : data.summary.staffPending ? `${data.summary.staffPending} not in yet today` : 'Everyone scheduled came in', icon: data.summary.staffAbsent ? UserX : Users, tone: data.summary.staffAbsent ? 'red' : 'green' },
        { label: 'Hours worked', value: formatHours(data.summary.hoursWorked), detail: `${formatHours(data.summary.hoursExpected)} expected, ${formatHours(data.summary.activeHours)} active in the CRM`, icon: Clock, tone: 'blue' },
        { label: 'Daily tasks completed', value: data.summary.tasksAvailable && data.summary.tasksAssigned ? formatPercent(data.summary.taskCompletionPercent) : 'No data yet', detail: data.summary.tasksAssigned ? `${data.summary.tasksCompleted} of ${data.summary.tasksAssigned} done, ${data.summary.tasksPostponed} postponed, ${data.summary.tasksOverdue} overdue` : 'No daily tasks were handed out in this period', icon: CheckCircle2, tone: 'violet' },
        { label: 'Sent to client', value: data.summary.forwarded.toLocaleString(), detail: `${data.summary.interviewsScheduled} interviews set, ${data.summary.selected} selected`, icon: Send, tone: 'amber' },
        { label: 'Visa received', value: data.summary.visaReceived.toLocaleString(), detail: `${data.summary.visaStamped} stamped, ${data.summary.stageMoves} stage moves in all`, icon: GitBranch, tone: 'blue' },
        { label: 'Deployed', value: data.summary.deployed.toLocaleString(), detail: data.summary.averageScore == null ? 'No team score yet' : `Team score ${data.summary.averageScore} of 100`, icon: Plane, tone: 'green' },
        ...(leadCards ? [
          { label: 'Leads worked', value: leadCards.worked.value, detail: leadCards.worked.detail, icon: PhoneCall, tone: 'blue' as const },
          { label: 'Leads converted', value: leadCards.converted.value, detail: leadCards.converted.detail, icon: UserCheck, tone: 'green' as const },
        ] : []),
      ]} />

      <section className="rv-card sw-card" aria-label="Questions to ask">
        <header className="sw-head"><div><h3>Questions to ask</h3><p>Where the records show something to follow up. Select a name for the detail.</p></div><span className="sw-count">{highlights.length}</span></header>
        {highlights.length === 0
          ? <p className="sw-empty">Nothing stands out in this period.</p>
          : <ul className="sw-flags">{highlights.map(item => <li key={item.id} className={`is-${item.severity}`}>
            <span className="sw-dot" aria-hidden="true" />
            <div><button type="button" className="sw-link" onClick={() => onOpenStaff(item.staffId)}>{item.staffName}</button><b>{item.title}</b><p>{item.detail}</p></div>
          </li>)}</ul>}
      </section>

      <section className="rv-card sw-card" aria-label="Staff">
        <header className="sw-head"><div><h3>Staff</h3><p>One row per person. Select a row to see every day and every candidate.</p></div></header>
        {rows.length === 0 ? <p className="sw-empty">No staff accounts to report on.</p> : <div className="sw-scroll"><table className="sw-table sw-staff">
          <thead><tr>{COLUMNS.map(column => <Fragment key={column.key}>
            <th aria-sort={sort.key === column.key ? (sort.direction === 'asc' ? 'ascending' : 'descending') : 'none'} title={column.title}>
              <button type="button" onClick={() => sortBy(column.key)}>{column.label}{sort.key === column.key && (sort.direction === 'asc' ? <ArrowUp size={12} /> : <ArrowDown size={12} />)}</button></th>
            {notes && column.key === 'completion' && <th className="num" title={NOTES_COLUMN.title}>{NOTES_COLUMN.label}</th>}
          </Fragment>)}</tr></thead>
          <tbody>{rows.map(row => <StaffLine key={row.id} row={row} notes={notes} onOpen={() => onOpenStaff(row.id)} />)}</tbody>
        </table></div>}
        <details className="sw-how"><summary>How the score is worked out</summary>
          <ul>{data.formula.lines.map(line => <li key={line}>{line}</li>)}</ul>
          <p>75 and over is shown green, 50 to 74 amber, below 50 red. It is a prompt for a conversation, not a verdict: check the parts and the days first.</p></details>
      </section>

      {leadSheet && <section className="rv-card sw-card" aria-label="Lead sheet by staff">
        <header className="sw-head"><div><h3>Lead sheet</h3><p>Leads handed to each person and what they did with them in this period. Lead follow-up counts as an optional 10% of the score, only for people who had leads assigned.</p></div></header>
        <div className="sw-scroll"><table className="sw-table sw-leads">
          <thead><tr>{LEAD_COLUMNS.map(column => <th key={column.label} className={column.key === 'name' ? undefined : 'num'} aria-sort={column.key && sort.key === column.key ? (sort.direction === 'asc' ? 'ascending' : 'descending') : undefined} title={column.title}>
            {column.key ? <button type="button" onClick={() => sortBy(column.key as SortKey)}>{column.label}{sort.key === column.key && (sort.direction === 'asc' ? <ArrowUp size={12} /> : <ArrowDown size={12} />)}</button> : column.label}</th>)}</tr></thead>
          <tbody>{rows.map(row => <LeadLine key={row.id} row={row} onOpen={() => onOpenStaff(row.id)} />)}</tbody>
        </table></div>
      </section>}

      <section className="rv-card sw-card" aria-label="Stages by staff">
        <header className="sw-head"><div><h3>Stage by stage</h3><p>Each cell: candidates waiting in that stage now, and in brackets how many moved out of it in this period.</p></div></header>
        <div className="sw-scroll"><table className="sw-table sw-matrix">
          <thead><tr><th>Staff</th>{data.funnel.map(stage => <th key={stage.stage} className="num" title={stage.label}><span>{stage.stage}</span><small>{stage.label}</small></th>)}</tr></thead>
          <tbody>{rows.map(row => <tr key={row.id}>
            <th><button type="button" className="sw-link" onClick={() => onOpenStaff(row.id)}>{row.name}</button></th>
            {data.funnel.map(stage => { const now = row.stages.inStageNow[stage.stage] ?? 0; const moved = row.stages.movesByStage[stage.stage] ?? 0
              return <td key={stage.stage} className="num">{now || moved ? <><b>{now}</b>{moved ? <em>({moved})</em> : null}</> : ''}</td> })}
          </tr>)}</tbody>
          <tfoot><tr><th>Team</th>{data.funnel.map(stage => <td key={stage.stage} className="num"><b>{stage.inStageNow || ''}</b>{stage.movesInPeriod ? <em>({stage.movesInPeriod})</em> : null}</td>)}</tr></tfoot>
        </table></div>
      </section>

      <section className="rv-card sw-card" aria-label="Team funnel">
        <header className="sw-head"><div><h3>Team funnel</h3><p>Every candidate placed on an order, owned by staff, counted at the furthest stage reached. The percentage is how many of the previous stage went on.</p></div></header>
        <ol className="sw-funnel">{data.funnel.map(stage => <li key={stage.stage}>
          <span className="sw-fname"><b>{stage.stage}</b>{stage.label}</span>
          <span className="sw-ftrack" aria-hidden="true"><i style={{ width: `${funnelWidth(stage.reached, data.funnel[0]?.reached ?? 0)}%` }} /></span>
          <span className="sw-fnum"><b>{stage.reached}</b> reached</span>
          <span className="sw-fconv">{stage.conversionPercent == null ? '' : `${formatPercent(stage.conversionPercent)} of previous`}{stage.lostFromPrevious ? <em>{stage.lostFromPrevious} lost</em> : null}</span>
          <span className="sw-fmeta">{stage.inStageNow} waiting, {stage.closedHere} closed here, {stage.movesInPeriod} done this period</span>
        </li>)}</ol>
      </section>

      <details className="reports-notes"><summary>How these numbers are worked out</summary>
        <ul>{data.notes.map(note => <li key={note}>{note}</li>)}<li>Dates include the first and last day. Times are in {data.timezone}. Updated {clockTime(data.generatedAt, data.timezone)}.</li></ul></details>
    </div>}

    {open && data && <StaffDrawer row={open} report={data} onClose={() => onOpenStaff('')} />}
  </>
}

function LiveBadge({ status, updatedAt, zone, flash }: { status: string; updatedAt?: string; zone?: string; flash: boolean }) {
  const up = status === 'up'
  return <span className={`sw-live${up ? ' is-up' : ''}${flash ? ' is-flash' : ''}`} role="status" title={up ? 'Updates arrive the moment attendance, tasks or stages change.' : 'The live connection is not up; the report re-reads every minute.'}>
    <i aria-hidden="true" />{up ? 'Live' : status === 'connecting' ? 'Connecting' : 'Refreshes every minute'}
    {updatedAt && <small>updated {new Date(updatedAt).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', second: '2-digit', timeZone: zone })}</small>}
  </span>
}

function StaffLine({ row, notes, onOpen }: { row: StaffRow; notes: boolean; onOpen: () => void }) {
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
    {notes && <td className="num">{row.feedbackNotes || <span className="sw-muted">0</span>}</td>}
    <td className="num">{row.stages.moves || <span className="sw-muted">0</span>}</td>
    <td className="num">{row.outcomes.forwarded}</td>
    <td className="num">{row.outcomes.visaReceived}{row.outcomes.visaStamped ? <small>{row.outcomes.visaStamped} stamped</small> : null}</td>
    <td className="num">{row.outcomes.deployed}</td>
    <td className="num">{row.stuck.count ? <span className="sw-bad">{row.stuck.count}<small>oldest {row.stuck.oldestDays} d</small></span> : <span className="sw-muted">0</span>}</td>
    <td className="num"><span className={`sw-score-pill is-${scoreTone(row.score)}`} title={row.scoreParts.map(p => `${p.label}: ${p.detail}`).join('\n')}>{row.score ?? '-'}</span></td>
  </tr>
}

function LeadLine({ row, onOpen }: { row: StaffRow; onOpen: () => void }) {
  const leads = row.leads
  const none = <span className="sw-muted">0</span>
  return <tr onClick={onOpen}>
    <td><button type="button" className="sw-link" onClick={event => { event.stopPropagation(); onOpen() }}>{row.name}</button></td>
    {!leads?.available
      ? <td colSpan={LEAD_COLUMNS.length - 1} className="sw-muted">No lead data</td>
      : <>
        <td className="num">{leads.assigned ? <><b>{leads.assigned}</b><small className={`sw-lead-${leadsTone(leads)}`}>{leadsCalledText(leads)}</small></> : none}</td>
        <td className="num">{leads.interested || none}</td>
        <td className="num">{leads.calls || none}{leads.worked ? <small>{leads.worked} {leads.worked === 1 ? 'lead' : 'leads'}</small> : null}</td>
        <td className="num">{leads.resumesReceived || none}</td>
        <td className="num">{leads.converted || none}{leads.conversionPercent != null ? <small>{formatPercent(leads.conversionPercent)}</small> : null}</td>
        <td className="num">{leads.followUpsOverdue ? <span className="sw-bad">{leads.followUpsOverdue}</span> : none}{leads.open ? <small>{leads.open} open</small> : null}</td>
      </>}
  </tr>
}
