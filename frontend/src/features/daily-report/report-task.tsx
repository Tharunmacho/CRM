import { Clock3, Hourglass } from 'lucide-react'
import { cn } from '@/shared/lib/cn'
import { initials } from '@/shared/lib/format'
import { Select } from '@/shared/ui/primitives'
import { ScrollEdges } from '@/screens/tasks/daily-table'
import type { StatTileData } from '@/screens/tasks/daily-stat-strip'
import { Tiles, opens, type ViewProps } from './report-views'
import { hueOf } from './report-table'
import { buildStageMatrix, buildStageTable, numberText, reportPeople, shownOverdue, type MatrixRow } from './daily-report-view'

/** A figure: a button when there is a list behind it, plain when not, a dash for zero or not counted. */
function Figure({ value, label, onOpen }: { value: number | null | undefined; label: string; onOpen?: () => void }) {
  if (value == null || value <= 0) return <span className="dw-none" aria-label={value === 0 ? '0' : 'not counted'}>{'–'}</span>
  return onOpen
    ? <button type="button" className="dwr-link" onClick={onOpen} aria-label={`${numberText(value)} ${label}. Show the list`}>{numberText(value)}</button>
    : <span className="dwr-plain">{numberText(value)}</span>
}

/**
 * The matrix fits its panel: fixed layout, the Recruiter column as wide as the names need, the 14 figure columns (No order, stages 1 to 13) share the rest
 * equally and Total is a little wider. Under MATRIX_MIN the table stops shrinking and the scroll box (ScrollEdges) takes over.
 */
const NAME_WIDTH = 160
const TOTAL_WIDTH = 64
const MATRIX_MIN = NAME_WIDTH + TOTAL_WIDTH + 14 * 50

function Person({ name }: { name: string }) {
  return <span className="dwr-person"><span className={cn('record-avatar', `avatar-hue-${hueOf(name)}`)} aria-hidden="true">{initials(name)}</span><span className="dwr-who">{name}</span></span>
}

/**
 * The Task report: the 13 stages as one table, ONE ROW PER PERSON (recruiters and the MD), counting the candidates waiting at each stage now.
 * The recruiter filter narrows the table and "Stage by stage" below it to one person. What is waiting now does not depend on the period.
 */
export function TaskReportView({ report, roster, staff, onPickStaff, onDrill, onHistory }: ViewProps) {
  const team = report.scope === 'team'
  const matrix = buildStageMatrix(report, team ? staff : '')
  const flow = buildStageTable(report, team ? staff : '')
  const overdue = shownOverdue(report)
  const people = reportPeople(roster.length ? roster : report.staff)
  const drill = (no: number, id: string | null) => ((matrix.total[String(no)] ?? 0) > 0 ? () => onDrill(`stage:${no}`, id && id !== 'other' ? id : null) : undefined)

  const tiles: StatTileData[] = [
    { key: 'waiting', label: 'Waiting now', value: numberText(flow.total.waiting), icon: Hourglass, title: 'Candidates at stages 1 to 13 right now, whatever the period' },
    { key: 'overdue', label: 'Overdue tasks', value: numberText(overdue), icon: Clock3, tone: (overdue ?? 0) > 0 ? 'red' : 'green', title: 'Open now and past their time, whatever the period', ...opens(overdue, () => onDrill('tasksOverdue', null)) },
  ]
  const one = team && staff ? people.find(row => row.id === staff)?.name : undefined
  const row = (item: MatrixRow) => <tr key={item.id} className="dwm-row">
    <th scope="row" className="dwr-name"><Person name={item.name} /></th>
    <td className="is-num"><Figure value={item.cells['0']} label="not on an order" onOpen={drill(0, item.id)} /></td>
    {matrix.stages.map(stage => <td key={stage.no} className="is-num"><Figure value={item.cells[String(stage.no)]} label={stage.name} onOpen={drill(stage.no, item.id)} /></td>)}
    <td className="is-num is-strong"><Figure value={item.cells.total} label="in total" /></td>
  </tr>
  const flowTitle = one ? `${one}: stage by stage` : 'Stage by stage'
  return <>
    <Tiles tiles={tiles} label="The stages at a glance" />
    <section className="dw-panel dwm-panel" aria-label="The stages, recruiter by recruiter">
      <div className="dw-panel-head">
        <div><h2>Candidates waiting at each stage</h2></div>
        <div className="dwr-head-tools">
          {team && onHistory && <button type="button" className="dwr-textlink" onClick={onHistory}>Submitted reports</button>}
          {team && <Select value={staff || 'all'} onChange={event => onPickStaff(event.target.value === 'all' ? '' : event.target.value)} className="tk-select" aria-label="Filter by recruiter">
            <option value="all">All recruiters</option>
            {[...people].sort((a, b) => a.name.localeCompare(b.name)).map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
          </Select>}
        </div>
      </div>
      {matrix.rows.length === 0
        ? <p className="dwb-none">Nobody in this selection.</p>
        : <ScrollEdges label="Candidates waiting at each stage, by recruiter. Scroll sideways for more stages">
          <table className="dwr-table dwm-table is-matrix" aria-label="Candidates waiting at each stage, by recruiter" style={{ minWidth: MATRIX_MIN }}>
            <colgroup><col style={{ width: NAME_WIDTH }} />{Array.from({ length: 14 }, (_, i) => <col key={i} />)}<col style={{ width: TOTAL_WIDTH }} /></colgroup>
            <thead><tr>
              <th scope="col" className="dwr-name-head">Recruiter</th>
              <th scope="col" className="is-num" title="Not on an order yet: candidates who are not matched to an order."><span className="dwm-h-no">–</span><span className="dwm-h-name">No order</span></th>
              {matrix.stages.map(stage => <th key={stage.no} scope="col" className="is-num" title={`Stage ${stage.no}: ${stage.name}`}><span className="dwm-h-no">{stage.no}</span><span className="dwm-h-name">{stage.short}</span></th>)}
              <th scope="col" className="is-num"><span className="dwm-h-no">&Sigma;</span><span className="dwm-h-name">Total</span></th>
            </tr></thead>
            <tbody>{matrix.rows.map(row)}</tbody>
            <tfoot><tr className="dwr-total dwm-row">
              <th scope="row" className="dwr-name">Total</th>
              <td className="is-num"><Figure value={matrix.total['0']} label="not on an order" onOpen={drill(0, null)} /></td>
              {matrix.stages.map(stage => <td key={stage.no} className="is-num"><Figure value={matrix.total[String(stage.no)]} label={stage.name} onOpen={drill(stage.no, null)} /></td>)}
              <td className="is-num is-strong"><Figure value={matrix.total.total} label="in total" /></td>
            </tr></tfoot>
          </table>
        </ScrollEdges>}
    </section>

    <section className="dw-panel dwm-panel" aria-label={flowTitle}>
      <div className="dw-panel-head"><div><h2>{flowTitle}</h2></div></div>
      <ScrollEdges label="Stage by stage: candidates waiting now">
        <table className="dwr-table dwm-table is-flow" aria-label="Stage by stage" style={{ minWidth: 420 }}>
          <colgroup><col style={{ width: 'auto' }} /><col style={{ width: 150 }} /><col style={{ width: 150 }} /></colgroup>
          <thead><tr>
            <th scope="col" className="dwr-name-head">Stage</th>
            <th scope="col" className="is-num">Waiting now</th>
            <th scope="col" className="is-num" title="Each stage's part of the candidates waiting now">Share</th>
          </tr></thead>
          <tbody>{flow.rows.map(item => <tr key={item.no} className="dwm-row">
            <th scope="row" className="dwr-name"><span className="dwr-who">{item.name}</span></th>
            <td className="is-num"><Figure value={item.waiting} label={`waiting at ${item.name}`} onOpen={item.drill ? () => onDrill(item.drill!, team && staff ? staff : null) : undefined} /></td>
            <td className="is-num">{item.waiting ? <span className="dwr-plain">{item.share}%</span> : <span className="dw-none">{'–'}</span>}</td>
          </tr>)}</tbody>
          <tfoot><tr className="dwr-total dwm-row">
            <th scope="row" className="dwr-name">Total</th>
            <td className="is-num"><Figure value={flow.total.waiting} label="waiting now" /></td>
            <td className="is-num">{flow.total.waiting ? <span className="dwr-plain">100%</span> : <span className="dw-none">{'–'}</span>}</td>
          </tr></tfoot>
        </table>
      </ScrollEdges>
    </section>
  </>
}
