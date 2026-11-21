import type { ReactNode } from 'react'
import { ListChecks, PieChart } from 'lucide-react'
import { cn } from '@/shared/lib/cn'
import { ScrollEdges } from '@/screens/tasks/daily-table'
import { leadStatusTable, numberText, stageProgress } from './daily-report-view'
import type { DailyReport } from './daily-report-types'

const NEEDS_SERVER = 'This part needs the latest server version.'

interface SimpleCol { id: string; head: string; info?: string }
interface SimpleRow { id: string; name: string; cells: Record<string, ReactNode> }

/** A figure of a section table: a button when a list stands behind it, plain text otherwise; zero is a light dash and a missing figure (older server) too. */
function Figure({ value, label, onOpen }: { value: number | null; label: string; onOpen?: () => void }) {
  if (value == null) return <span className="dw-none" aria-label="not available">{'–'}</span>
  if (value <= 0) return <span className="dw-none" aria-label="0">{'–'}</span>
  return onOpen
    ? <button type="button" className="dwr-link" onClick={onOpen} aria-label={`${numberText(value)} ${label}. Show the list`}>{numberText(value)}</button>
    : <span className="dwr-plain">{numberText(value)}</span>
}

/** A slim share bar with its percentage; a dash when there is nothing to share. */
function Share({ pct, empty }: { pct: number; empty: boolean }) {
  if (empty) return <span className="dw-none" aria-label="not available">{'–'}</span>
  return <span className="dwr-pct is-share"><span className="dwb-meter" aria-hidden="true"><i style={{ width: `${pct}%` }} /></span><b className="tnum">{pct}%</b></span>
}

/**
 * A small table in the report tables' own style (the same classes as the figures table: dw-table dw-main dwr-table, the name heading left, the
 * figure columns centred, row stripes from report-tables.css, the Tasks scroll frame) and, below 1080px of panel, the same labelled cards.
 */
function SectionTable({ caption, nameHeader, cols, rows, total, widths }: { caption: string; nameHeader: string; cols: SimpleCol[]; rows: SimpleRow[]; total: SimpleRow; widths: number[] }) {
  return <ScrollEdges label={caption}>
    <table className="dw-table dw-main dwr-table" aria-label={caption}>
      <colgroup><col style={{ width: 'auto' }} />{widths.map((width, i) => <col key={i} style={{ width: `${width}px` }} />)}</colgroup>
      <thead><tr>
        <th scope="col" className="is-left dwr-name-head">{nameHeader}</th>
        {cols.map(col => <th key={col.id} scope="col" className="is-num" title={col.info}>{col.head}</th>)}
      </tr></thead>
      <tbody>
        {rows.map((row, index) => <tr key={row.id} className={cn('dw-staff-row', index % 2 === 1 && 'is-alt')}>
          <th scope="row" className="dwr-name"><span className="dwr-who">{row.name}</span></th>
          {cols.map(col => <td key={col.id} data-label={col.head} className="is-num">{row.cells[col.id]}</td>)}
        </tr>)}
      </tbody>
      <tfoot>
        <tr className="dwr-total">
          <th scope="row" className="dwr-name">{total.name}</th>
          {cols.map(col => <td key={col.id} data-label={col.head} className="is-num">{total.cells[col.id]}</td>)}
        </tr>
      </tfoot>
    </table>
  </ScrollEdges>
}

/**
 * Task progress by stage: one row per stage 1 to 13 (and "0 · Not on an order yet" when candidates are there): how many candidates are at the stage
 * now, how many stage records were completed in the period and how many of those ended the placement, with a slim bar for the share now. A number
 * that has a list behind it (now at the stage) opens it. The report is already narrowed by the recruiter and client filters.
 */
export function StageProgressPanel({ report, onDrill }: { report: Pick<DailyReport, 'stages' | 'stageFlow'>; onDrill: (metric: string, staffId: string | null) => void }) {
  const progress = stageProgress(report)
  const cols: SimpleCol[] = [
    { id: 'now', head: 'Now at this stage', info: 'Candidates whose current stage this is, right now.' },
    { id: 'completed', head: 'Completed in this period', info: 'Stage records completed in the period.' },
    { id: 'stopped', head: 'Stopped here in this period', info: 'Of those completed in the period, how many ended the placement at this stage.' },
    { id: 'share', head: 'Share now', info: 'This stage as a share of the candidates now at a stage.' },
  ]
  const rows: SimpleRow[] = progress.rows.map(row => ({
    id: String(row.no), name: row.name,
    cells: {
      now: <Figure value={row.now} label={`now at ${row.name}`} onOpen={row.drill ? () => onDrill(row.drill!, null) : undefined} />,
      completed: <Figure value={row.completed} label={`completed at ${row.name}`} />,
      stopped: <Figure value={row.stopped} label={`stopped at ${row.name}`} />,
      share: <Share pct={row.share} empty={(row.now ?? 0) <= 0} />,
    },
  }))
  const total: SimpleRow = {
    id: 'total', name: 'Total',
    cells: { now: <Figure value={progress.total.now} label="now at a stage" />, completed: <Figure value={progress.total.completed} label="completed" />, stopped: <Figure value={progress.total.stopped} label="stopped" />, share: <Share pct={100} empty={(progress.total.now ?? 0) <= 0} /> },
  }
  return <section className="dw-panel dwr-stages" aria-label="Task progress by stage">
    <div className="dw-panel-head"><div><h2><ListChecks size={16} aria-hidden="true" /> Task progress by stage</h2></div></div>
    <SectionTable caption="Task progress by stage: candidates now at each stage, and the stages completed and stopped in the period" nameHeader="Stage" cols={cols} rows={rows} total={total} widths={[150, 150, 150, 190]} />
  </section>
}

/** Lead sheet by status: one row per lead status with its leads and share, and a total. Without the lead sheet (an older server) one short line. */
export function LeadStatusPanel({ report }: { report: Pick<DailyReport, 'leadSheet'> }) {
  const table = leadStatusTable(report.leadSheet)
  if (!table) return <p className="dwr-quiet">{NEEDS_SERVER}</p>
  const cols: SimpleCol[] = [
    { id: 'leads', head: 'Leads', info: 'Leads at this status right now.' },
    { id: 'share', head: 'Share %', info: 'This status as a share of all the leads.' },
  ]
  const rows: SimpleRow[] = table.rows.map(row => ({
    id: row.key, name: row.label,
    cells: { leads: <Figure value={row.count} label={row.label} />, share: <Share pct={row.pct} empty={row.count <= 0} /> },
  }))
  const total: SimpleRow = { id: 'total', name: 'Total', cells: { leads: <Figure value={table.total} label="leads" />, share: <Share pct={table.totalPct} empty={table.total <= 0} /> } }
  return <section className="dw-panel dwr-leadstatus" aria-label="Lead sheet by status">
    <div className="dw-panel-head"><div><h2><PieChart size={16} aria-hidden="true" /> Lead sheet by status</h2></div></div>
    {table.rows.length === 0
      ? <p className="dwb-none">No leads on the sheet.</p>
      : <SectionTable caption="Lead sheet by status: the leads at each status and their share" nameHeader="Status" cols={cols} rows={rows} total={total} widths={[170, 220]} />}
  </section>
}
