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
  attendanceDay, changedSinceText, clientRows, contactedPct, dailyCells, dailyCols, dayClosed, dayCloseTitle, DAY_CLOSED_COL, figureGroups, interviewsNote, hasLeadPeriod, leadCols, LEAD_PCT_COL, leadCells, unassignedInfo, metricOf, numberText,
  pctText, showByClient, STAGE_TOTAL_COL, stageSummary, statusRows, sumCells, taskStageCols, taskStageRows, taskStageTotals, totalCells,
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
export function Empty({ title }: { title: string }) {
  return <section className="dw-panel dw-state" role="status"><p className="dwr-empty-title">{title}</p></section>
}

/** Five or fewer tiles in one row, more as rows of four, in the Tasks screen's summary-strip style. Nothing is printed under a number: the
 *  explanation of a tile is its hover text (title). */
export function Tiles({ tiles, label }: { tiles: StatTileData[]; label: string }) {
  const rows = (tiles.length <= 5 ? [tiles] : [tiles.slice(0, 4), tiles.slice(4)]).filter(row => row.length > 0)
  return <>{rows.map((row, index) => <StatStrip key={index} tiles={row} label={rows.length > 1 ? `${label}, row ${index + 1}` : label} />)}</>
}

/** The panel head of a table card: the heading with an icon, and on the right any small text links and the "All recruiters" filter. */
export function PanelHead({ title, staff, roster, onPickStaff, team, links }: { title: string; staff: string; roster: StaffRow[]; onPickStaff: (id: string) => void; team: boolean; links?: ReactNode }) {
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
export const opens = (value: number | null | undefined, open: () => void) => ((value ?? 0) > 0 ? { onSelect: open, actionLabel: 'Show the list' } : {})

/* ---------------------------------------------------------------- Lead sheet monitoring */

function PctCell({ assigned, contacted }: { assigned: number | null | undefined; contacted: number | null | undefined }) {
  const pct = contactedPct(assigned, contacted)
  if (pct == null) return <span className="dw-none">{'–'}</span>
  return <span className="dwr-pct"><span className="dwb-meter" aria-hidden="true"><i style={{ width: `${pct}%` }} /></span><b className="tnum">{pctText(pct)}</b></span>
}

/** Leads only: how many are on the sheet, who owns them, who has contacted how many and what the leads said. Every figure ties to the Lead sheet screen. */
export function LeadSheetView({ report, roster, scope, staff, onPickStaff, onDrill }: ViewProps) {
  const sheet = report.leadSheet
  const cols = leadCols(report)
  const period = hasLeadPeriod(report)
  const team = report.scope === 'team'
  const people = report.staff.map(row => ({ row, cells: leadCells(row) })).sort((a, b) => (b.cells.contacted ?? 0) - (a.cells.contacted ?? 0) || a.row.name.localeCompare(b.row.name))
  const totals = sumCells(people.map(p => p.cells))
  const contacted = sheet?.contacted ?? report.leadFeedback.contacted
  const assigned = sheet?.assigned ?? totals.assigned
  const pct = contactedPct(assigned, contacted)
  const unassigned = unassignedInfo(sheet)
  const status = statusRows(sheet)
  const never = sheet?.neverContacted
  const tiles: StatTileData[] = period && sheet && never !== undefined
    ? [
      { key: 'total', label: 'Total leads', value: numberText(sheet.totalLeads), icon: Layers, title: 'All the leads on the sheet, whatever the period', ...opens(sheet.totalLeads, () => onDrill('leadsTotal', null)) },
      { key: 'new', label: 'New leads', value: numberText(sheet.newInPeriod), icon: PlusCircle, ...opens(sheet.newInPeriod, () => onDrill('leadsNewInPeriod', null)) },
      { key: 'assigned', label: 'Assigned', value: numberText(sheet.assignedInPeriod), icon: UserCheck, ...opens(sheet.assignedInPeriod, () => onDrill('leadsAssignedInPeriod', null)) },
      { key: 'contacted', label: 'Contacted', value: numberText(contacted), icon: PhoneCall, ...opens(contacted, () => onDrill('leadsContacted', null)) },
      { key: 'never', label: 'Not contacted', value: numberText(never), icon: Clock3, title: 'Leads never contacted, whatever the period: the same number as the Lead sheet screen', ...opens(never, () => onDrill('leadsNotContacted', null)) },
    ]
    : period && sheet
      ? [
        { key: 'new', label: 'New leads', value: numberText(sheet.newInPeriod), icon: PlusCircle, ...opens(sheet.newInPeriod, () => onDrill('leadsNewInPeriod', null)) },
        { key: 'assigned', label: 'Assigned', value: numberText(sheet.assignedInPeriod), icon: UserCheck, ...opens(sheet.assignedInPeriod, () => onDrill('leadsAssignedInPeriod', null)) },
        { key: 'contacted', label: 'Contacted', value: numberText(contacted), icon: PhoneCall, ...opens(contacted, () => onDrill('leadsContacted', null)) },
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
