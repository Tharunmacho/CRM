import type { ReactNode } from 'react'
import { Building2, CheckCircle2, ClipboardCheck, Clock3, FileText, Layers, Minus, PhoneCall, Percent, Plane, PlusCircle, Send, Stamp, UserCheck, Users, Video, type LucideIcon } from 'lucide-react'
import { Badge } from '@/shared/ui/display'
import { Select } from '@/shared/ui/primitives'
import { StatStrip, type StatTileData } from '@/screens/tasks/daily-stat-strip'
import { FigureTable, type FigureGroup, type FigureRow } from './report-table'
import { PeopleBoard } from './report-people'
import { LeadStatusPanel, StageProgressPanel } from './report-sections'
import { AttendancePanel } from './report-attendance'
import {
  attendanceDay, changeLine, changedSinceText, clientRows, contactedPct, dailyCells, dailyCols, dayClosed, dayCloseTitle, DAY_CLOSED_COL, figureGroups, interviewsNote, hasLeadPeriod, leadCols, LEAD_PCT_COL, leadCells, unassignedInfo, metricOf, numberText,
  pctText, showByClient, STAGE_TOTAL_COL, stageSummary, statusRows, sumCells, taskStageCols, taskStageRows, taskStageTotals, totalCells, totalVs, vsLabel, vsOf,
} from './daily-report-view'
import type { DailyReport, StaffRow } from './daily-report-types'

export interface ViewProps {
  report: DailyReport
  /** The period is Total (everything): not a single day, and nothing to compare with. */
  allTime?: boolean
  /** Everyone, even while the page is narrowed to one person: the recruiter filter's list only. The Team board and the table show the picked person alone. */
  roster: StaffRow[]
  today: string
  timeZone: string
  /** Names the question being shown, so changed numbers flash only within the same tab, period and filters. */
  scope: string
  /** The person the page is narrowed to ('' for everyone). */
  staff: string
  onPickStaff: (id: string) => void
  onDrill: (metric: string, staffId: string | null) => void
  /** A number of the By client card: the metric and the client. */
  onDrillClient?: (metric: string, clientId: string) => void
  /** The client filter of the page ('all' or a client id): the By client card is hidden while one is chosen. */
  clientFilter?: string
  /** A "Submitted" pill was pressed: show that person's report. */
  onSubmission?: (staffId: string) => void
  /** "Submitted reports" was pressed. */
  onHistory?: () => void
}

const NEEDS_SERVER = 'This part needs the latest server version.'
const ICONS: Record<string, LucideIcon> = { forwarded: Send, prescreened: ClipboardCheck, shortlisted: UserCheck, interviewsOnline: Video, offers: FileText, visas: Stamp, travelled: Plane }

/** An empty or unavailable view: one short sentence. */
function Empty({ title }: { title: string }) {
  return <section className="dw-panel dw-state" role="status"><p className="dwr-empty-title">{title}</p></section>
}

/** Five or fewer tiles in one row, more as rows of four, in the Tasks screen's summary-strip style. Nothing is printed under a number: the
 *  explanation of a tile is its hover text (title). */
function Tiles({ tiles, label }: { tiles: StatTileData[]; label: string }) {
  const rows = (tiles.length <= 5 ? [tiles] : [tiles.slice(0, 4), tiles.slice(4)]).filter(row => row.length > 0)
  return <>{rows.map((row, index) => <StatStrip key={index} tiles={row} label={rows.length > 1 ? `${label}, row ${index + 1}` : label} />)}</>
}

/** The panel head of a table card: the heading with an icon, and on the right any small text links and the "All recruiters" filter. */
function PanelHead({ title, staff, roster, onPickStaff, team, links }: { title: string; staff: string; roster: StaffRow[]; onPickStaff: (id: string) => void; team: boolean; links?: ReactNode }) {
  return <div className="dw-panel-head">
    <div><h2><Users size={16} aria-hidden="true" /> {title}</h2></div>
    <div className="dwr-head-tools">
      {links}
      {team && <Select value={staff || 'all'} onChange={event => onPickStaff(event.target.value === 'all' ? '' : event.target.value)} className="tk-select" aria-label="Filter by recruiter">
        <option value="all">All recruiters</option>
        {[...roster].sort((a, b) => a.name.localeCompare(b.name)).map(row => <option key={row.id} value={row.id}>{row.name}</option>)}
      </Select>}
    </div>
  </div>
}

/** A tile that opens its list when there is something behind its number. */
const opens = (value: number | null | undefined, open: () => void) => ((value ?? 0) > 0 ? { onSelect: open, actionLabel: 'Show the list' } : {})

/* ---------------------------------------------------------------- Daily report */

/**
 * The client's Daily CRM Work Report: the seven figures in the client's order as summary tiles and Day closed, the team (who has
 * reported), task progress by stage, everyone's figures in a table, the lead sheet by status and the day's attendance. With one
 * recruiter picked, all of it is that person's alone. How each figure moved, who has not closed the day
 * and the interviews without a mode are the tiles' hover texts, not printed text.
 */
export function DailyView({ report, allTime = false, roster, today, timeZone, scope, staff, onPickStaff, onDrill, onDrillClient, clientFilter = 'all', onSubmission, onHistory }: ViewProps) {
  const single = !allTime && report.period.from === report.period.to
  const todayOnly = single && report.period.to === today
  const team = report.scope === 'team'
  const cols = dailyCols(report.metrics, todayOnly)
  const totals = totalCells(report, report.staff)
  const vs = allTime ? {} : totalVs(report.metrics)
  const label = vsLabel(report.period, today)
  const interviews = metricOf(report.metrics, 'interviews')?.value ?? 0
  const note = interviewsNote(report)
  const tiles: StatTileData[] = cols.map(col => {
    const value = totals[col.id] ?? 0
    const caption = col.id === 'interviewsOnline' ? (interviews > 0 ? `of ${numberText(interviews)} interviews` : '') : changeLine(vs[col.id], label)
    const title = [caption, col.id === 'interviewsOnline' ? note : ''].filter(Boolean).join('. ')
    return { key: col.id, label: col.label, value: numberText(value), icon: ICONS[col.id], title: title || undefined, ...(col.drill ? opens(value, () => onDrill(col.drill!, null)) : {}) }
  })
  const compliance = single && team ? report.compliance : null
  const mine = !team && single ? report.staff[0] : undefined
  const closed = mine ? dayClosed(mine, true, timeZone) : null
  if (compliance) tiles.push({ key: 'closed', label: 'Day closed', value: compliance.submitted, unit: `of ${compliance.expected}`, icon: CheckCircle2, tone: 'green', progress: { done: compliance.submitted, total: compliance.expected }, title: dayCloseTitle(compliance) })
  else if (closed && closed.kind !== 'none') tiles.push({ key: 'closed', label: 'Day closed', value: closed.kind === 'submitted' ? 'Yes' : 'Not yet', icon: CheckCircle2, tone: closed.kind === 'submitted' ? 'green' : 'blue', title: closed.text })

  // One recruiter picked: the Team board, the table and its heading are that person's alone (the roster only fills the filter's list).
  const picked = team && staff ? report.staff.filter(row => row.id === staff) : []
  const shown = team && staff ? (picked.length ? picked : roster.filter(row => row.id === staff)) : report.staff
  const pickedName = staff ? shown[0]?.name : undefined
  const figuresTitle = !team ? 'Your figures' : staff ? (pickedName ? `${pickedName}'s figures` : 'Their figures') : "Everyone's figures"
  const groups: FigureGroup[] = figureGroups(shown, single, todayOnly).map(group => ({
    key: group.key, title: group.title, tone: group.key === 'active' ? 'done' as const : undefined,
    icon: group.key === 'active' ? <CheckCircle2 size={15} aria-hidden="true" /> : <Minus size={15} aria-hidden="true" />,
    rows: group.rows.map((row): FigureRow => {
      const state = dayClosed(row, single, timeZone)
      const pill = <Badge tone={state.kind === 'submitted' ? 'green' : 'amber'} variant="chip">{state.text}</Badge>
      const tail = state.kind === 'none' ? <span className="dw-none">{state.text}</span>
        : state.kind === 'submitted' && onSubmission
          ? <><button type="button" className="dwr-pillbtn" onClick={() => onSubmission(row.id)} aria-label={`${row.name}: ${state.text}. Show their report`}>{pill}</button>{row.changedSinceSubmit && <small className="dwr-changed" title={changedSinceText(row)}>changed</small>}</>
          : pill
      return { id: row.id, name: row.name, idle: row.idle, cells: dailyCells(row), tail }
    }),
  }))
  const byClient = clientRows(report)
  return <>
    <Tiles tiles={tiles} label="The day's figures" />
    {team && <PeopleBoard staff={shown} single={single} todayOnly={todayOnly} selected={staff || 'all'} onSelect={id => onPickStaff(id === 'all' ? '' : id)} />}
    <StageProgressPanel report={report} onDrill={onDrill} />
    <section className="dw-panel" aria-label={figuresTitle}>
      <PanelHead title={figuresTitle} staff={staff} roster={roster} onPickStaff={onPickStaff} team={team} links={<>
        {note && <button type="button" className="dwr-textlink" title={note} onClick={() => onDrill('interviews', null)}>Show the interviews</button>}
        {onHistory && <button type="button" className="dwr-textlink" onClick={onHistory}>Submitted reports</button>}
      </>} />
      {groups.length === 0
        ? <p className="dwb-none">Nobody in this selection.</p>
        : <FigureTable caption="Daily CRM work report: each person's figures for the period" cols={cols} tailHead={single ? DAY_CLOSED_COL.label : undefined} groups={groups}
          selectedId={staff} scope={scope} onDrill={onDrill} onPickName={team ? onPickStaff : undefined} />}
    </section>
    {byClient.length > 0 && showByClient(byClient, clientFilter) && onDrillClient && <section className="dw-panel" aria-label="By client">
      <div className="dw-panel-head"><div><h2><Building2 size={16} aria-hidden="true" /> By client</h2></div></div>
      <FigureTable caption="By client: the figures of the period for each client" cols={cols} tailHead="Total" tailNumeric nameHeader="Client" scope={`${scope}|clients`} onDrill={(metric, id) => id && onDrillClient(metric, id)}
        groups={[{ key: 'clients', title: '', icon: null, rows: byClient.map((row): FigureRow => ({ id: row.id, name: row.name, cells: row.cells, tail: <strong className="tnum">{numberText(row.cells.total)}</strong> })) }]} />
    </section>}
    <LeadStatusPanel report={report} />
    <AttendancePanel day={attendanceDay(report.period, allTime, today)} staff={staff} team={team} timeZone={timeZone} onPickStaff={onPickStaff} />
  </>
}

/* ---------------------------------------------------------------- Lead sheet monitoring */

function PctCell({ assigned, contacted }: { assigned: number | null | undefined; contacted: number | null | undefined }) {
  const pct = contactedPct(assigned, contacted)
  if (pct == null) return <span className="dw-none">{'–'}</span>
  return <span className="dwr-pct"><span className="dwb-meter" aria-hidden="true"><i style={{ width: `${pct}%` }} /></span><b className="tnum">{pctText(pct)}</b></span>
}

/** Leads only: how many are on the sheet, who owns them, who has contacted how many and what the leads said. Every figure ties to the Lead sheet screen. */
export function LeadSheetView({ report, roster, today, scope, staff, onPickStaff, onDrill }: ViewProps) {
  const sheet = report.leadSheet
  const cols = leadCols(report)
  const period = hasLeadPeriod(report)
  const team = report.scope === 'team'
  const people = report.staff.map(row => ({ row, cells: leadCells(row) })).sort((a, b) => (b.cells.contacted ?? 0) - (a.cells.contacted ?? 0) || a.row.name.localeCompare(b.row.name))
  const totals = sumCells(people.map(p => p.cells))
  const contacted = sheet?.contacted ?? report.leadFeedback.contacted
  const assigned = sheet?.assigned ?? totals.assigned
  const pct = contactedPct(assigned, contacted)
  const label = vsLabel(report.period, today)
  const prev = sheet?.previous
  const unassigned = unassignedInfo(sheet)
  /** "+3 vs yesterday": the hover text of a tile that has a previous value (nothing when the period is Total). */
  const moved = (now: number | undefined, before: number | undefined | null) => (now == null || before == null ? undefined : changeLine(vsOf(now, before), label) || undefined)
  const status = statusRows(sheet)
  const never = sheet?.neverContacted
  const tiles: StatTileData[] = period && sheet && never !== undefined
    ? [
      { key: 'total', label: 'Total leads', value: numberText(sheet.totalLeads), icon: Layers, title: 'All the leads on the sheet, whatever the period', ...opens(sheet.totalLeads, () => onDrill('leadsTotal', null)) },
      { key: 'new', label: 'New leads', value: numberText(sheet.newInPeriod), icon: PlusCircle, title: moved(sheet.newInPeriod, prev?.newInPeriod), ...opens(sheet.newInPeriod, () => onDrill('leadsNewInPeriod', null)) },
      { key: 'assigned', label: 'Assigned', value: numberText(sheet.assignedInPeriod), icon: UserCheck, title: moved(sheet.assignedInPeriod, prev?.assignedInPeriod), ...opens(sheet.assignedInPeriod, () => onDrill('leadsAssignedInPeriod', null)) },
      { key: 'contacted', label: 'Contacted', value: numberText(contacted), icon: PhoneCall, title: moved(contacted, prev?.contacted), ...opens(contacted, () => onDrill('leadsContacted', null)) },
      { key: 'never', label: 'Not contacted', value: numberText(never), icon: Clock3, title: 'Leads never contacted, whatever the period: the same number as the Lead sheet screen', ...opens(never, () => onDrill('leadsNotContacted', null)) },
    ]
    : period && sheet
      ? [
        { key: 'new', label: 'New leads', value: numberText(sheet.newInPeriod), icon: PlusCircle, title: moved(sheet.newInPeriod, prev?.newInPeriod), ...opens(sheet.newInPeriod, () => onDrill('leadsNewInPeriod', null)) },
        { key: 'assigned', label: 'Assigned', value: numberText(sheet.assignedInPeriod), icon: UserCheck, title: moved(sheet.assignedInPeriod, prev?.assignedInPeriod), ...opens(sheet.assignedInPeriod, () => onDrill('leadsAssignedInPeriod', null)) },
        { key: 'contacted', label: 'Contacted', value: numberText(contacted), icon: PhoneCall, title: moved(contacted, prev?.contacted), ...opens(contacted, () => onDrill('leadsContacted', null)) },
        { key: 'backlog', label: 'Still to contact', value: numberText(sheet.backlog), icon: Clock3, title: 'Assigned leads never contacted, whatever the period', ...opens(sheet.backlog, () => onDrill('leadsBacklog', null)) },
      ]
      : [
        { key: 'total', label: 'Total leads on the sheet', value: numberText(sheet?.totalLeads), icon: Layers, title: sheet ? `${numberText(sheet.notContacted)} not contacted yet` : undefined },
        { key: 'assigned', label: 'Assigned', value: numberText(sheet?.assigned), icon: UserCheck, title: sheet ? `${numberText(unassigned?.total)} not assigned` : undefined, ...opens(sheet?.assigned, () => onDrill('leadsAssigned', null)) },
        { key: 'contacted', label: 'Contacted in this period', value: numberText(contacted), icon: PhoneCall, title: 'Leads called or updated', ...opens(contacted, () => onDrill('leadsContacted', null)) },
        { key: 'pct', label: 'Contacted of assigned', value: pct == null ? '–' : pctText(pct), icon: Percent, tone: 'green', ...(pct == null ? {} : { progress: { done: pct, total: 100 } }) },
      ]
  const rows: FigureRow[] = people.map(({ row, cells }) => ({ id: row.id, name: row.name, idle: row.idle, cells, tail: <PctCell assigned={cells.workload ?? cells.assigned} contacted={cells.contacted} /> }))
  // The whole sheet only when nobody is picked: leads nobody owns, then the Total that ties to the tiles and to the Lead sheet screen.
  const wholeSheet = team && !staff
  if (wholeSheet && unassigned && unassigned.neverContacted != null && period) {
    rows.push({ id: 'unassigned', name: 'Unassigned', fixed: true, cells: { assigned: null, contacted: null, interested: null, callback: null, notInterested: null, wrongNumber: null, noResponse: null, notContacted: unassigned.neverContacted }, tail: <span className="dw-none">{'–'}</span> })
  }
  const unassignedNever = wholeSheet && period ? unassigned?.neverContacted ?? 0 : 0
  const totalRow: FigureRow | undefined = wholeSheet && period && rows.length > 0
    ? {
      id: 'total', name: 'Total',
      cells: { ...totals, assigned: period ? sheet?.assignedInPeriod ?? totals.assigned : totals.assigned, contacted: contacted, notContacted: never ?? (totals.notContacted == null ? null : totals.notContacted + unassignedNever) },
      tail: <PctCell assigned={totals.workload ?? totals.assigned} contacted={contacted} />,
    }
    : undefined
  const groups: FigureGroup[] = rows.length ? [{ key: 'all', title: '', icon: null, rows }] : []
  return <>
    <Tiles tiles={tiles} label="Lead sheet at a glance" />
    <section className="dw-panel" aria-label="Everyone's leads">
      <PanelHead title="Everyone's leads" staff={staff} roster={roster} onPickStaff={onPickStaff} team={team} />
      {groups.length === 0
        ? <p className="dwb-none">Nobody in this selection.</p>
        : <FigureTable caption="Lead sheet monitoring: each person's leads and what the leads said" cols={cols} tailHead={LEAD_PCT_COL.label} tailWidth={120} groups={groups} total={totalRow} selectedId={staff} scope={scope}
          onDrill={onDrill} onPickName={team ? onPickStaff : undefined} dense nameWidth={200} />}
    </section>
    {sheet
      ? <section className="dw-panel dwr-status" aria-label="Lead status now">
        <div className="dw-panel-head"><div><h2>Lead status now</h2></div></div>
        {status.length === 0 ? <p className="dwb-none">No leads on the sheet.</p>
          : <table className="dw-table"><colgroup><col /><col style={{ width: '1%' }} /><col style={{ width: '1%' }} /></colgroup><thead><tr><th scope="col">Status</th><th scope="col" className="is-num">Leads</th><th scope="col" className="is-num">Share</th></tr></thead>
            <tbody>{status.map(item => <tr key={item.key}><th scope="row" className="dwr-status-name">{item.label}</th><td className="is-num tnum">{numberText(item.count)}</td><td className="is-num tnum">{item.pct}%</td></tr>)}</tbody></table>}
      </section>
      : <p className="dwr-quiet">{NEEDS_SERVER}</p>}
  </>
}

/* ---------------------------------------------------------------- Candidate monitoring */

/** Candidates and tasks only: who holds how many at each of the 12 stages (and none on an order yet), and their tasks. */
export function CandidateMonitoringView({ report, roster, scope, staff, onPickStaff, onDrill }: ViewProps) {
  const cols = taskStageCols(report)
  const hasStages = !!report.stages && cols.length > 3
  const hasTasks = !!report.tasks || report.staff.some(row => row.tasks)
  if (!hasStages && !hasTasks) return <Empty title={NEEDS_SERVER} />
  const team = report.scope === 'team'
  const totals = taskStageTotals(report)
  const { inProgress, notOnOrder } = stageSummary(report)
  const tiles: StatTileData[] = [
    { key: 'progress', label: 'Candidates in progress', value: numberText(inProgress), icon: Layers, title: notOnOrder > 0 ? `Right now, whatever the period. ${numberText(notOnOrder)} not on an order yet` : 'Right now, whatever the period' },
    { key: 'done', label: 'Tasks done', value: numberText(totals.done), icon: CheckCircle2, tone: 'green', title: 'Completed in this period', ...opens(totals.done, () => onDrill('tasksDone', null)) },
    { key: 'pending', label: 'Tasks pending', value: numberText(totals.pending), icon: ClipboardCheck, title: 'Open now and not yet due, whatever the period', ...opens(totals.pending, () => onDrill('tasksPending', null)) },
    { key: 'overdue', label: 'Tasks overdue', value: numberText(totals.overdue), icon: Clock3, tone: (totals.overdue ?? 0) > 0 ? 'red' : 'green', title: 'Open now and past their time, whatever the period', ...opens(totals.overdue, () => onDrill('tasksOverdue', null)) },
  ]
  const rows: FigureRow[] = taskStageRows(report).map(row => ({ id: row.id, name: row.name, cells: row.cells, tail: <strong className="tnum">{numberText(row.cells.total)}</strong> }))
  const groups: FigureGroup[] = rows.length ? [{ key: 'all', title: '', icon: null, rows }] : []
  return <>
    <Tiles tiles={tiles} label="Candidates and tasks at a glance" />
    <section className="dw-panel" aria-label="Everyone's candidates">
      <PanelHead title="Everyone's candidates" staff={staff} roster={roster} onPickStaff={onPickStaff} team={team} />
      {groups.length === 0
        ? <p className="dwb-none">Nobody in this selection.</p>
        : <FigureTable caption="Candidate monitoring: each person's tasks and how many candidates are at each stage right now" cols={cols} tailHead={hasStages ? STAGE_TOTAL_COL.label : undefined} tailWidth={STAGE_TOTAL_COL.width} tailNumeric groups={groups}
          selectedId={staff} scope={scope} onDrill={onDrill} onPickName={team ? onPickStaff : undefined} dense nameWidth={200} />}
    </section>
  </>
}
