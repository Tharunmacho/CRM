import { useMemo, type ReactNode } from 'react'
import { CalendarClock, PlusCircle, ThumbsDown, ThumbsUp } from 'lucide-react'
import { cn } from '@/shared/lib/cn'
import { initials } from '@/shared/lib/format'
import { SummaryCards } from '@/shared/ui/workspace'
import { ScrollEdges } from '@/screens/tasks/daily-table'
import { hueOf } from './report-table'
import { CardNote } from './report-cards'
import { AgingLeadsPanel } from './report-lists'
import { leadSummary, trendOf } from './report-management'
import { numberText } from './daily-report-view'
import { headLines, leadMatrix, type MatrixRow } from './lead-matrix'
import type { ViewProps } from './report-views'
import '@/styles/report-lead.css'

const NEEDS_SERVER = 'This part needs the latest server version.'

/** A figure: a button when there is a list behind it, plain when not, a dash for zero or not counted (an older server). */
function Figure({ value, label, onOpen, tone }: { value: number | null | undefined; label: string; onOpen?: () => void; tone?: 'stuck' }) {
  if (value == null || value <= 0) return <span className="dw-none" aria-label={value === 0 ? '0' : 'not counted'}>{'–'}</span>
  return onOpen
    ? <button type="button" className={cn('dwr-link', tone && `is-${tone}`)} onClick={onOpen} aria-label={`${numberText(value)} ${label}. Show the list`}>{numberText(value)}</button>
    : <span className={cn('dwr-plain', tone && `is-${tone}`)}>{numberText(value)}</span>
}

function Person({ row }: { row: MatrixRow }) {
  return <span className="dwr-person"><span className={cn('record-avatar', `avatar-hue-${hueOf(row.name)}`)} aria-hidden="true">{row.unassigned ? '–' : initials(row.name)}</span><span className="dwr-who">{row.name}</span></span>
}

/* The matrix fits its panel (fixed layout): the Recruiter column as wide as the names need, the statuses share the rest equally. Under the minimum the scroll box takes over. */
const NAME_WIDTH = 150
const ASSIGNED_WIDTH = 62
const CONTACTED_WIDTH = 68
const CONVERSION_WIDTH = 74
const AGING_WIDTH = 64
const STATUS_MIN = 44

function Heading({ label, title, className }: { label: string; title: string; className?: string }) {
  return <th scope="col" className={cn('is-num', className)} title={title}>{headLines(label).map((line, i) => <span key={i} className="dwl-h">{line}</span>)}</th>
}

/**
 * The Lead sheet report, read as a manager: seven cards (new, contacted, interested, not interested, follow-up, conversion, aging) with the previous period
 * beside them, the recruiter by status matrix with conversion and aging columns and a Total row, the status now, and the aging leads.
 */
export function LeadReportView({ report, roster, staff, today, onDrill }: ViewProps) {
  const sheet = report.leadSheet
  const team = report.scope === 'team'
  const picked = team ? staff : ''
  const matrix = useMemo(() => leadMatrix(report, picked, roster), [report, picked, roster])
  const summary = leadSummary(report, today)
  if (!sheet) return <section className="dw-panel dwm-panel" aria-label="Lead sheet report"><p className="dwr-quiet">{NEEDS_SERVER}</p></section>

  const line = summary?.staleDays ?? 3
  const minWidth = NAME_WIDTH + ASSIGNED_WIDTH + matrix.columns.length * STATUS_MIN + CONTACTED_WIDTH + CONVERSION_WIDTH + AGING_WIDTH
  const one = picked ? matrix.rows[0]?.name : undefined
  const drillContacted = (row: MatrixRow) => (!row.unassigned && (row.contacted ?? 0) > 0 ? () => onDrill('leadsContacted', row.id === 'total' ? picked || null : row.id) : undefined)
  const drillStale = (row: MatrixRow) => ((row.stale ?? 0) > 0 && !row.unassigned ? () => onDrill('leadsStale', row.id === 'total' ? picked || null : row.id) : undefined)

  const cards = summary ? [
    { label: 'New leads', value: numberText(summary.newLeads.value), icon: PlusCircle, tone: 'blue' as const,
      detail: <CardNote trend={trendOf(summary.newLeads.value, summary.newLeads.previous, summary.against, 'up')} />,
      ...(summary.newLeads.value > 0 ? { onSelect: () => onDrill('leadsNewInPeriod', picked || null) } : {}) },
    { label: 'Interested', value: numberText(summary.interested.value), icon: ThumbsUp, tone: 'green' as const,
      detail: <CardNote trend={trendOf(summary.interested.value, summary.interested.previous, summary.against, 'up')}>{summary.conversion.value == null ? undefined : `${summary.conversion.value}% of those contacted`}</CardNote> },
    { label: 'Not interested', value: numberText(summary.notInterested.value), icon: ThumbsDown, tone: 'blue' as const,
      detail: <CardNote trend={trendOf(summary.notInterested.value, summary.notInterested.previous, summary.against, 'none')} /> },
    { label: 'Needs follow-up', value: numberText(summary.followUp), icon: CalendarClock, tone: 'amber' as const,
      detail: <CardNote />,
      ...(summary.followUp > 0 ? { onSelect: () => onDrill('leadsFollowUp', picked || null) } : {}) },
  ] : []

  const cells = (row: MatrixRow): ReactNode => <>
    <td className="is-num"><Figure value={row.assigned} label="assigned" /></td>
    {matrix.columns.map(c => <td key={c.key} className="is-num"><Figure value={row.counts[c.key]} label={c.label} /></td>)}
    <td className="is-num"><Figure value={row.contacted} label="contacted in the period" onOpen={drillContacted(row)} /></td>
    <td className="is-num">{row.conversion == null ? <span className="dw-none" aria-label="nobody contacted">{'–'}</span> : <span className="dwr-plain rp-conv" title={`${numberText(row.interested)} interested of ${numberText(row.contacted)} contacted`}>{row.conversion}%</span>}</td>
    <td className="is-num"><Figure value={row.stale} tone="stuck" label={`not contacted for ${line} days`} onOpen={drillStale(row)} /></td>
  </>
  return <>
    {cards.length > 0 && <div className="rp-cards" data-count={cards.length}><SummaryCards items={cards} /></div>}

    <section className="dw-panel dwm-panel dwl-panel rp-panel" aria-label="Leads by status, recruiter by recruiter">
      <div className="dw-panel-head">
        <div><h2>{one ? `${one}: leads by status` : 'Leads by status'}</h2></div>
      </div>
      {matrix.rows.length === 0
        ? <p className="dwb-none">Nobody in this selection.</p>
        : <ScrollEdges label="Leads by status, by recruiter. Scroll sideways for more statuses">
          <table className="dwr-table dwm-table dwl-table is-matrix" style={{ minWidth }}>
            <caption className="rp-sr">Leads by status, by recruiter, with conversion to interested and the leads not contacted for more than {line} days</caption>
            <colgroup><col style={{ width: NAME_WIDTH }} /><col style={{ width: ASSIGNED_WIDTH }} />{matrix.columns.map(c => <col key={c.key} />)}<col style={{ width: CONTACTED_WIDTH }} /><col style={{ width: CONVERSION_WIDTH }} /><col style={{ width: AGING_WIDTH }} /></colgroup>
            <thead><tr>
              <th scope="col" className="dwr-name-head">Recruiter</th>
              <Heading label="Assigned" title="Assigned: leads the recruiter holds right now, whatever their status" />
              {matrix.columns.map(c => <Heading key={c.key} label={c.label} title={`${c.label}: leads currently in this status`} />)}
              <Heading label="Contacted in period" title="Contacted in this period: leads the recruiter called or whose status they changed in the chosen period" />
              <Heading label="Conversion %" title="Of the leads contacted in the period, the share whose last answer was interested" />
              <Heading label={`Aging ${line}+ days`} title={`Leads never contacted since they arrived, waiting more than ${line} days`} className="is-stuck" />
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

    {report.leadInsight && <AgingLeadsPanel rows={report.leadInsight.aging} line={line} />}
  </>
}
