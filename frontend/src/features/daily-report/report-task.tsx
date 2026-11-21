import { CheckCircle2, Clock3, Hourglass, OctagonX } from 'lucide-react'
import { useState } from 'react'
import { cn } from '@/shared/lib/cn'
import { initials } from '@/shared/lib/format'
import { Select } from '@/shared/ui/primitives'
import { ScrollEdges } from '@/screens/tasks/daily-table'
import type { StatTileData } from '@/screens/tasks/daily-stat-strip'
import { Tiles, opens, type ViewProps } from './report-views'
import { hueOf } from './report-table'
import { buildStageMatrix, buildStageTable, numberText, STAGE_MODES, taskStageTotals, type MatrixRow, type StageMode } from './daily-report-view'

const MODE_KEY = 'crm.task-report.mode'
function readMode(): StageMode {
  try { return window.localStorage.getItem(MODE_KEY) === 'waiting' ? 'waiting' : 'completed' } catch { return 'completed' }
}

/** A figure: a button when there is a list behind it, plain when not, a dash for zero or not counted. */
function Figure({ value, label, onOpen }: { value: number | null | undefined; label: string; onOpen?: () => void }) {
  if (value == null || value <= 0) return <span className="dw-none" aria-label={value === 0 ? '0' : 'not counted'}>{'–'}</span>
  return onOpen
    ? <button type="button" className="dwr-link" onClick={onOpen} aria-label={`${numberText(value)} ${label}. Show the list`}>{numberText(value)}</button>
    : <span className="dwr-plain">{numberText(value)}</span>
}

/** Column widths of the matrix in px: each at least as wide as the longest word of its heading. The table fills the panel when it is wider than their sum. */
const STAGE_WIDTHS = [112, 92, 96, 76, 76, 80, 84, 80, 64, 80, 76, 88, 76]
const NAME_WIDTH = 188
const NOT_ON_ORDER = 92
const TOTAL_WIDTH = 80
const MATRIX_MIN = NAME_WIDTH + NOT_ON_ORDER + STAGE_WIDTHS.reduce((all, width) => all + width, 0) + TOTAL_WIDTH

function Person({ name }: { name: string }) {
  return <span className="dwr-person"><span className={cn('record-avatar', `avatar-hue-${hueOf(name)}`)} aria-hidden="true">{initials(name)}</span><span className="dwr-who">{name}</span></span>
}

/**
 * The Task report: the 13 stages as one table, ONE ROW PER RECRUITER. A switch decides what the cells count (stage records completed in the
 * period, or candidates waiting at the stage now), the recruiter filter narrows the table and "Stage by stage" below it to one person.
 */
export function TaskReportView({ report, roster, staff, onPickStaff, onDrill, onHistory }: ViewProps) {
  const [mode, setMode] = useState<StageMode>(readMode)
  const choose = (next: StageMode) => { setMode(next); try { window.localStorage.setItem(MODE_KEY, next) } catch { /* the choice is just not remembered */ } }
  const team = report.scope === 'team'
  const matrix = buildStageMatrix(report, mode, team ? staff : '')
  const flow = buildStageTable(report)
  const overdue = taskStageTotals(report).overdue
  const waiting = mode === 'waiting'
  const people = roster.length ? roster : report.staff
  const drill = (no: number, id: string | null) => (waiting && (matrix.total[String(no)] ?? 0) > 0 ? () => onDrill(`stage:${no}`, id && id !== 'other' ? id : null) : undefined)

  const tiles: StatTileData[] = [
    { key: 'waiting', label: 'Waiting now', value: numberText(flow.total.waiting), icon: Hourglass, title: 'Candidates at stages 1 to 13 right now, whatever the period' },
    { key: 'completed', label: 'Completed in this period', value: numberText(flow.total.completed), icon: CheckCircle2, tone: 'green', title: 'Stage records completed in this period' },
    { key: 'stopped', label: 'Stopped in this period', value: numberText(flow.total.stopped), icon: OctagonX, title: 'Completed stage records that ended the placement' },
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
        <div><h2>{waiting ? 'Candidates waiting at each stage' : 'Stages completed in this period'}</h2></div>
        <div className="dwr-head-tools">
          {team && onHistory && <button type="button" className="dwr-textlink" onClick={onHistory}>Submitted reports</button>}
          <div className="dwr-seg is-small" role="group" aria-label="What the table counts">
            {STAGE_MODES.map(item => <button key={item.key} type="button" aria-pressed={mode === item.key} onClick={() => choose(item.key)}>{item.label}</button>)}
          </div>
          {team && <Select value={staff || 'all'} onChange={event => onPickStaff(event.target.value === 'all' ? '' : event.target.value)} className="tk-select" aria-label="Filter by recruiter">
            <option value="all">All recruiters</option>
            {[...people].sort((a, b) => a.name.localeCompare(b.name)).map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
          </Select>}
        </div>
      </div>
      {matrix.rows.length === 0
        ? <p className="dwb-none">Nobody in this selection.</p>
        : <ScrollEdges label={waiting ? 'Candidates waiting at each stage, by recruiter. Scroll sideways for more stages' : 'Stages completed in the period, by recruiter. Scroll sideways for more stages'}>
          <table className="dwr-table dwm-table" aria-label={waiting ? 'Candidates waiting at each stage, by recruiter' : 'Stage records completed in the period, by recruiter'} style={{ minWidth: MATRIX_MIN }}>
            <colgroup><col style={{ width: NAME_WIDTH }} /><col style={{ width: NOT_ON_ORDER }} />{STAGE_WIDTHS.map((width, i) => <col key={i} style={{ width }} />)}<col style={{ width: TOTAL_WIDTH }} /></colgroup>
            <thead><tr>
              <th scope="col" className="dwr-name-head">Recruiter</th>
              <th scope="col" className="is-num" title="Not on an order yet: candidates who are not matched to an order. Counted only in Waiting now."><span className="dwm-h-no">–</span><span className="dwm-h-name">No order</span></th>
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
      <ScrollEdges label="Stage by stage: waiting now, completed and stopped in the period">
        <table className="dwr-table dwm-table is-flow" aria-label="Stage by stage" style={{ minWidth: 640 }}>
          <colgroup><col style={{ width: 'auto' }} /><col style={{ width: 150 }} /><col style={{ width: 170 }} /><col style={{ width: 170 }} /><col style={{ width: 110 }} /></colgroup>
          <thead><tr>
            <th scope="col" className="dwr-name-head">Stage</th>
            <th scope="col" className="is-num">Waiting now</th>
            <th scope="col" className="is-num">Completed in this period</th>
            <th scope="col" className="is-num">Stopped here in this period</th>
            <th scope="col" className="is-num" title="Each stage's part of the candidates waiting now">Share</th>
          </tr></thead>
          <tbody>{flow.rows.map(item => <tr key={item.no} className="dwm-row">
            <th scope="row" className="dwr-name"><span className="dwr-who">{item.name}</span></th>
            <td className="is-num"><Figure value={item.waiting} label={`waiting at ${item.name}`} onOpen={item.drill ? () => onDrill(item.drill!, team && staff ? staff : null) : undefined} /></td>
            <td className="is-num"><Figure value={item.completed} label={`completed at ${item.name}`} /></td>
            <td className="is-num"><Figure value={item.stopped} label={`stopped at ${item.name}`} /></td>
            <td className="is-num">{item.waiting ? <span className="dwr-plain">{item.share}%</span> : <span className="dw-none">{'–'}</span>}</td>
          </tr>)}</tbody>
          <tfoot><tr className="dwr-total dwm-row">
            <th scope="row" className="dwr-name">Total</th>
            <td className="is-num"><Figure value={flow.total.waiting} label="waiting now" /></td>
            <td className="is-num"><Figure value={flow.total.completed} label="completed" /></td>
            <td className="is-num"><Figure value={flow.total.stopped} label="stopped" /></td>
            <td className="is-num">{flow.total.waiting ? <span className="dwr-plain">100%</span> : <span className="dw-none">{'–'}</span>}</td>
          </tr></tfoot>
        </table>
      </ScrollEdges>
    </section>
  </>
}
