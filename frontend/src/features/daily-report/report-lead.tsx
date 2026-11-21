import { useMemo, type ReactNode } from 'react'
import { cn } from '@/shared/lib/cn'
import { initials } from '@/shared/lib/format'
import { Select } from '@/shared/ui/primitives'
import { ScrollEdges } from '@/screens/tasks/daily-table'
import { hueOf } from './report-table'
import { numberText, pctText, reportPeople } from './daily-report-view'
import { headLines, leadMatrix, statusNow, type MatrixRow } from './lead-matrix'
import type { ViewProps } from './report-views'
import '@/styles/report-lead.css'

const NEEDS_SERVER = 'This part needs the latest server version.'

/** A figure: a button when there is a list behind it, plain when not, a dash for zero or not counted (an older server). */
function Figure({ value, label, onOpen }: { value: number | null | undefined; label: string; onOpen?: () => void }) {
  if (value == null || value <= 0) return <span className="dw-none" aria-label={value === 0 ? '0' : 'not counted'}>{'–'}</span>
  return onOpen
    ? <button type="button" className="dwr-link" onClick={onOpen} aria-label={`${numberText(value)} ${label}. Show the list`}>{numberText(value)}</button>
    : <span className="dwr-plain">{numberText(value)}</span>
}

function PctTail({ pct }: { pct: number | null }) {
  if (pct == null) return <span className="dw-none" aria-label="not counted">{'–'}</span>
  return <span className="dwr-pct"><span className="dwb-meter" aria-hidden="true"><i style={{ width: `${pct}%` }} /></span><b className="tnum">{pctText(pct)}</b></span>
}

function Person({ row }: { row: MatrixRow }) {
  return <span className="dwr-person"><span className={cn('record-avatar', `avatar-hue-${hueOf(row.name)}`)} aria-hidden="true">{row.unassigned ? '–' : initials(row.name)}</span><span className="dwr-who">{row.name}</span></span>
}

/* The matrix fits its panel (fixed layout): the Recruiter column as wide as the names need, the statuses share the rest equally. Under the minimum the scroll box takes over. */
const NAME_WIDTH = 160
const ASSIGNED_WIDTH = 72
const CONTACTED_WIDTH = 82
const PCT_WIDTH = 104
const STATUS_MIN = 52

function Heading({ label, title }: { label: string; title: string }) {
  return <th scope="col" className="is-num" title={title}>{headLines(label).map((line, i) => <span key={i} className="dwl-h">{line}</span>)}</th>
}

/**
 * The Lead sheet report, built like the Task report: ONE ROW PER RECRUITER, one column per lead status in the Lead sheet's own order. A cell is the
 * leads the person holds right now in that status; the Unassigned row holds the leads nobody owns; the Total ties to the Lead sheet screen.
 */
export function LeadReportView({ report, roster, staff, onPickStaff, onDrill }: ViewProps) {
  const sheet = report.leadSheet
  const team = report.scope === 'team'
  const matrix = useMemo(() => leadMatrix(report, team ? staff : '', roster), [report, team, staff, roster])
  const status = useMemo(() => statusNow(matrix, report, team ? staff : ''), [matrix, report, team, staff])
  if (!sheet) return <section className="dw-panel dwm-panel" aria-label="Lead sheet report"><p className="dwr-quiet">{NEEDS_SERVER}</p></section>

  const minWidth = NAME_WIDTH + ASSIGNED_WIDTH + matrix.columns.length * STATUS_MIN + CONTACTED_WIDTH + PCT_WIDTH
  const people = reportPeople(roster.length ? roster : report.staff)
  const one = team && staff ? people.find(item => item.id === staff)?.name : undefined
  const shownTotal = status.reduce((sum, item) => sum + item.count, 0)
  const drillContacted = (row: MatrixRow) => (!row.unassigned && (row.contacted ?? 0) > 0 ? () => onDrill('leadsContacted', row.id === 'total' ? null : row.id) : undefined)
  const cells = (row: MatrixRow): ReactNode => <>
    <td className="is-num"><Figure value={row.assigned} label="assigned" /></td>
    {matrix.columns.map(c => <td key={c.key} className="is-num"><Figure value={row.counts[c.key]} label={c.label} /></td>)}
    <td className="is-num"><Figure value={row.contacted} label="contacted in the period" onOpen={drillContacted(row)} /></td>
    <td className="is-num"><PctTail pct={row.pct} /></td>
  </>
  return <>
    <section className="dw-panel dwm-panel dwl-panel" aria-label="Leads by status, recruiter by recruiter">
      <div className="dw-panel-head">
        <div><h2>{one ? `${one}: leads by status` : 'Leads by status'}</h2></div>
        <div className="dwr-head-tools">
          {team && <Select value={staff || 'all'} onChange={event => onPickStaff(event.target.value === 'all' ? '' : event.target.value)} className="tk-select" aria-label="Filter by recruiter">
            <option value="all">All recruiters</option>
            {[...people].sort((a, b) => a.name.localeCompare(b.name)).map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
          </Select>}
        </div>
      </div>
      {matrix.rows.length === 0
        ? <p className="dwb-none">Nobody in this selection.</p>
        : <ScrollEdges label="Leads by status, by recruiter. Scroll sideways for more statuses">
          <table className="dwr-table dwm-table dwl-table is-matrix" aria-label="Leads by status, by recruiter" style={{ minWidth }}>
            <colgroup><col style={{ width: NAME_WIDTH }} /><col style={{ width: ASSIGNED_WIDTH }} />{matrix.columns.map(c => <col key={c.key} />)}<col style={{ width: CONTACTED_WIDTH }} /><col style={{ width: PCT_WIDTH }} /></colgroup>
            <thead><tr>
              <th scope="col" className="dwr-name-head">Recruiter</th>
              <Heading label="Assigned" title="Assigned: leads the recruiter holds right now, whatever their status" />
              {matrix.columns.map(c => <Heading key={c.key} label={c.label} title={`${c.label}: leads currently in this status`} />)}
              <Heading label="Contacted in period" title="Contacted in this period: leads the recruiter called or whose status they changed in the chosen period" />
              <Heading label="Contacted %" title="Contacted in the period as a share of the leads the recruiter holds now (never above 100%)" />
            </tr></thead>
            <tbody>{matrix.rows.map(row => <tr key={row.id} className={cn('dwm-row', row.idle && 'is-idle')}>
              <th scope="row" className="dwr-name"><Person row={row} /></th>
              {cells(row)}
            </tr>)}</tbody>
            {matrix.total && <tfoot><tr className="dwr-total dwm-row">
              <th scope="row" className="dwr-name">Total</th>
              {cells(matrix.total)}
            </tr></tfoot>}
          </table>
        </ScrollEdges>}
      {!matrix.hasCounts && <p className="dwr-quiet">{NEEDS_SERVER}</p>}
    </section>

    <section className="dw-panel dwm-panel dwl-panel" aria-label="Lead status now">
      <div className="dw-panel-head"><div><h2>{one ? `${one}: lead status now` : 'Lead status now'}</h2></div></div>
      {status.length === 0 ? <p className="dwb-none">No leads to show.</p>
        : <ScrollEdges label="Lead status now">
          <table className="dwr-table dwm-table is-flow" aria-label="Lead status now" style={{ minWidth: 420 }}>
            <colgroup><col style={{ width: 'auto' }} /><col style={{ width: 150 }} /><col style={{ width: 150 }} /></colgroup>
            <thead><tr><th scope="col" className="dwr-name-head">Status</th><th scope="col" className="is-num">Leads</th><th scope="col" className="is-num">Share %</th></tr></thead>
            <tbody>{status.map(item => <tr key={item.key} className="dwm-row">
              <th scope="row" className="dwr-name"><span className="dwr-who">{item.label}</span></th>
              <td className="is-num"><Figure value={item.count} label={item.label} /></td>
              <td className="is-num">{item.count > 0 ? <span className="dwr-plain">{`${item.pct}%`}</span> : <span className="dw-none" aria-label="0">{'–'}</span>}</td>
            </tr>)}</tbody>
            <tfoot><tr className="dwr-total dwm-row">
              <th scope="row" className="dwr-name">Total</th>
              <td className="is-num"><Figure value={shownTotal} label="in total" /></td>
              <td className="is-num">{shownTotal > 0 ? <span className="dwr-plain">100%</span> : <span className="dw-none">{'–'}</span>}</td>
            </tr></tfoot>
          </table>
        </ScrollEdges>}
      <p className="dwr-lead-calls">
        <span><b className="tnum">{numberText(report.leadFeedback.contacted)}</b> contacted in this period</span>
        <span><b className="tnum">{numberText(report.leadFeedback.callbacksDue)}</b> call-backs due</span>
      </p>
    </section>
  </>
}
