import { CheckCircle2, UserX } from 'lucide-react'
import { cn } from '@/shared/lib/cn'
import { initials } from '@/shared/lib/format'
import { SummaryCards } from '@/shared/ui/workspace'
import { ScrollEdges } from '@/screens/tasks/daily-table'
import { hueOf } from './report-table'
import { LongestWaitingPanel } from './report-lists'
import { flowByStage, daysText, stuckOf, taskSummary, waitingByStage } from './report-management'
import { buildStageMatrix, numberText, reportPeople, shownOverdue, type MatrixRow } from './daily-report-view'
import type { ViewProps } from './report-views'

/** A figure: a button when there is a list behind it, plain when not, a dash for zero or not counted. */
function Figure({ value, label, onOpen, tone }: { value: number | null | undefined; label: string; onOpen?: () => void; tone?: 'stuck' }) {
  if (value == null || value <= 0) return <span className="dw-none" aria-label={value === 0 ? '0' : 'not counted'}>{'–'}</span>
  return onOpen
    ? <button type="button" className={cn('dwr-link', tone && `is-${tone}`)} onClick={onOpen} aria-label={`${numberText(value)} ${label}. Show the list`}>{numberText(value)}</button>
    : <span className={cn('dwr-plain', tone && `is-${tone}`)}>{numberText(value)}</span>
}

/**
 * The matrix fits its panel: fixed layout, the Recruiter column as wide as the names need, the 14 figure columns (No job, stages 1 to 13) share the rest
 * equally, Total and Stuck are a little wider. Under MATRIX_MIN the table stops shrinking and the scroll box (ScrollEdges) takes over.
 */
const NAME_WIDTH = 150
const TOTAL_WIDTH = 56
const STUCK_WIDTH = 62
const MATRIX_MIN = NAME_WIDTH + TOTAL_WIDTH + STUCK_WIDTH + 14 * 44

function Person({ name }: { name: string }) {
  return <span className="dwr-person"><span className={cn('record-avatar', `avatar-hue-${hueOf(name)}`)} aria-hidden="true">{initials(name)}</span><span className="dwr-who">{name}</span></span>
}

/**
 * The Task report, read as a manager: two cards at the top (stages completed, stopped or lost in the period), the 13 stages recruiter by recruiter with a Stuck column, stage by stage with its waits, where candidates are lost and who has waited longest.
 * Waiting is what is true now; completed and lost follow the period; the recruiter filter narrows all of it to one person.
 */
export function TaskReportView({ report, roster, staff, onDrill }: ViewProps) {
  const team = report.scope === 'team'
  const picked = team ? staff : ''
  const matrix = buildStageMatrix(report, picked)
  const insight = report.taskInsight
  const summary = taskSummary(report)
  const waits = waitingByStage(report)
  const people = reportPeople(roster.length ? roster : report.staff)
  const shown = new Set(reportPeople(report.staff).filter(row => !picked || row.id === picked).map(row => row.id))
  const flow = flowByStage(report, shown)
  const overdue = shownOverdue(report)
  const line = insight?.stuckDays ?? 7
  const drill = (no: number, id: string | null) => ((matrix.total[String(no)] ?? 0) > 0 ? () => onDrill(`stage:${no}`, id && id !== 'other' ? id : null) : undefined)
  const one = picked ? people.find(row => row.id === picked)?.name : undefined

  const cards = summary ? [
    { label: 'Stages completed', value: numberText(summary.completed), icon: CheckCircle2, tone: 'green' as const },
    { label: 'Stopped or lost', value: numberText(summary.stopped), icon: UserX, tone: 'amber' as const },
  ] : []

  const row = (item: MatrixRow) => <tr key={item.id} className="dwm-row">
    <th scope="row" className="dwr-name"><Person name={item.name} /></th>
    <td className="is-num"><Figure value={item.cells['0']} label="with no job" onOpen={drill(0, item.id)} /></td>
    {matrix.stages.map(stage => <td key={stage.no} className="is-num"><Figure value={item.cells[String(stage.no)]} label={stage.name} onOpen={drill(stage.no, item.id)} /></td>)}
    <td className="is-num is-strong"><Figure value={item.cells.total} label="in total" /></td>
    <td className="is-num"><Figure value={stuckOf(report, item.id)} tone="stuck" label={`stuck over ${line} days`} onOpen={(stuckOf(report, item.id) ?? 0) > 0 && item.id !== 'other' ? () => onDrill('stuck', item.id) : undefined} /></td>
  </tr>

  const flowTitle = one ? `${one}: stage by stage` : 'Stage by stage'
  const stageRows = matrix.stages.map(stage => {
    const wait = waits.get(stage.no)
    const waiting = insight ? wait?.count ?? 0 : matrix.total[String(stage.no)] ?? 0
    return { stage, waiting, wait, done: flow?.get(stage.no) }
  })
  const waitingTotal = stageRows.reduce((sum, item) => sum + item.waiting, 0)
  const stuckTotal = stageRows.reduce((sum, item) => sum + (item.wait?.stuck ?? 0), 0)
  const doneTotal = stageRows.reduce((sum, item) => sum + (item.done?.completed ?? 0), 0)
  const lostTotal = stageRows.reduce((sum, item) => sum + (item.done?.stopped ?? 0), 0)

  return <>
    {cards.length > 0 && <div className="rp-cards" data-count={cards.length}><SummaryCards items={cards} /></div>}

    <section className="dw-panel dwm-panel rp-panel" aria-label="The stages, recruiter by recruiter">
      <div className="dw-panel-head">
        <div><h2>{one ? `${one}: candidates waiting at each stage` : 'Candidates waiting at each stage'}</h2></div>
        {overdue != null && overdue > 0 && <button type="button" className="rp-link" onClick={() => onDrill('tasksOverdue', null)}>{numberText(overdue)} overdue {overdue === 1 ? 'task' : 'tasks'}</button>}
      </div>
      {matrix.rows.length === 0
        ? <p className="dwb-none">Nobody in this selection.</p>
        : <ScrollEdges label="Candidates waiting at each stage, by recruiter. Scroll sideways for more stages">
          <table className="dwr-table dwm-table is-matrix" style={{ minWidth: MATRIX_MIN }}>
            <caption className="rp-sr">Candidates waiting at each stage now, by recruiter, with the number stuck for more than {line} days</caption>
            <colgroup><col style={{ width: NAME_WIDTH }} />{Array.from({ length: 14 }, (_, i) => <col key={i} />)}<col style={{ width: TOTAL_WIDTH }} /><col style={{ width: STUCK_WIDTH }} /></colgroup>
            <thead><tr>
              <th scope="col" className="dwr-name-head">Recruiter</th>
              <th scope="col" className="is-num" title="No job yet: candidates who are on the recruiter's list but not yet matched to a job (order) of a client."><span className="dwm-h-no">{'–'}</span><span className="dwm-h-name">No job</span></th>
              {matrix.stages.map(stage => <th key={stage.no} scope="col" className="is-num" title={`Stage ${stage.no}: ${stage.name}`}><span className="dwm-h-no">{stage.no}</span><span className="dwm-h-name">{stage.short}</span></th>)}
              <th scope="col" className="is-num"><span className="dwm-h-no">&Sigma;</span><span className="dwm-h-name">Total</span></th>
              <th scope="col" className="is-num is-stuck" title={`Stuck: candidates whose current stage has waited more than ${line} days`}><span className="dwm-h-no">&gt;{line}d</span><span className="dwm-h-name">Stuck</span></th>
            </tr></thead>
            <tbody>{matrix.rows.map(row)}</tbody>
            <tfoot><tr className="dwr-total dwm-row">
              <th scope="row" className="dwr-name">Total</th>
              <td className="is-num"><Figure value={matrix.total['0']} label="with no job" onOpen={drill(0, null)} /></td>
              {matrix.stages.map(stage => <td key={stage.no} className="is-num"><Figure value={matrix.total[String(stage.no)]} label={stage.name} onOpen={drill(stage.no, null)} /></td>)}
              <td className="is-num is-strong"><Figure value={matrix.total.total} label="in total" /></td>
              <td className="is-num"><Figure value={insight?.waiting.stuck} tone="stuck" label={`stuck over ${line} days`} onOpen={(insight?.waiting.stuck ?? 0) > 0 ? () => onDrill('stuck', picked || null) : undefined} /></td>
            </tr></tfoot>
          </table>
        </ScrollEdges>}
    </section>

    <section className="dw-panel dwm-panel rp-panel" aria-label={flowTitle}>
      <div className="dw-panel-head"><div><h2>{flowTitle}</h2></div></div>
      <ScrollEdges label="Stage by stage: waiting now, how long, completed and stopped in the period">
        <table className="dwr-table dwm-table is-flow is-stages" style={{ minWidth: 560 }}>
          <caption className="rp-sr">Stage by stage: candidates waiting now, how long they have waited, and stage records completed and stopped in the period</caption>
          <colgroup><col style={{ width: 'auto' }} /><col style={{ width: 110 }} /><col style={{ width: 120 }} /><col style={{ width: 110 }} /><col style={{ width: 110 }} /><col style={{ width: 110 }} /></colgroup>
          <thead><tr>
            <th scope="col" className="dwr-name-head">Stage</th>
            <th scope="col" className="is-num">Waiting now</th>
            <th scope="col" className="is-num" title="The average number of days the candidates now at this stage have been waiting">Average wait</th>
            <th scope="col" className="is-num" title={`Candidates who have waited more than ${line} days`}>Stuck &gt;{line}d</th>
            <th scope="col" className="is-num" title="Stage records completed in the period, whatever the outcome">Completed</th>
            <th scope="col" className="is-num" title="Of those, the ones that ended the placement">Stopped</th>
          </tr></thead>
          <tbody>{stageRows.map(({ stage, waiting, wait, done }) => <tr key={stage.no} className="dwm-row">
            <th scope="row" className="dwr-name"><span className="dwr-who">{stage.no} {'·'} {stage.name}</span></th>
            <td className="is-num"><Figure value={waiting} label={`waiting at ${stage.name}`} onOpen={waiting > 0 ? () => onDrill(`stage:${stage.no}`, picked || null) : undefined} /></td>
            <td className="is-num">{wait?.avgDays != null ? <span className="dwr-plain">{daysText(wait.avgDays)}</span> : <span className="dw-none" aria-label="nobody waiting">{'–'}</span>}</td>
            <td className="is-num"><Figure value={wait?.stuck} tone="stuck" label={`stuck at ${stage.name}`} onOpen={(wait?.stuck ?? 0) > 0 ? () => onDrill(`stuck:${stage.no}`, picked || null) : undefined} /></td>
            <td className="is-num"><Figure value={done?.completed ?? (flow ? 0 : null)} label={`completed at ${stage.name}`} /></td>
            <td className="is-num"><Figure value={done?.stopped ?? (flow ? 0 : null)} label={`stopped at ${stage.name}`} /></td>
          </tr>)}</tbody>
          <tfoot><tr className="dwr-total dwm-row">
            <th scope="row" className="dwr-name">Total</th>
            <td className="is-num"><Figure value={waitingTotal} label="waiting now" /></td>
            <td className="is-num"><span className="dw-none" aria-hidden="true">{'–'}</span></td>
            <td className="is-num"><Figure value={stuckTotal} tone="stuck" label="stuck" onOpen={stuckTotal > 0 ? () => onDrill('stuck', picked || null) : undefined} /></td>
            <td className="is-num"><Figure value={flow ? doneTotal : null} label="completed" /></td>
            <td className="is-num"><Figure value={flow ? lostTotal : null} label="stopped" /></td>
          </tr></tfoot>
        </table>
      </ScrollEdges>
    </section>

    {insight && <LongestWaitingPanel rows={insight.longest} line={line} />}
  </>
}
