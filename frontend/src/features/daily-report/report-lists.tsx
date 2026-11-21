import { Link } from 'react-router-dom'
import { cn } from '@/shared/lib/cn'
import { ScrollEdges } from '@/screens/tasks/daily-table'
import { shortDay } from './daily-report-view'
import { waitTone } from './report-management'
import type { AgingLead, LongestWaiting } from './daily-report-types'

/** "12 Nov": the day a wait started, from a moment or a day. */
const sinceText = (value: string | null) => (value ? shortDay(value.slice(0, 10)) : '–')

function Empty({ children }: { children: string }) {
  return <p className="dwb-none rp-empty">{children}</p>
}

/** The days a candidate or lead has waited, tinted when it is past the line (amber) or past twice the line (red). */
function Days({ days, line }: { days: number; line: number }) {
  return <span className={cn('rp-days', `is-${waitTone(days, line)}`)}><b className="tnum">{days}</b> {days === 1 ? 'day' : 'days'}</span>
}

/** "Longest waiting": the ten candidates whose current stage has waited longest, with the recruiter, the stage and a link to the candidate. */
export function LongestWaitingPanel({ rows, line }: { rows: LongestWaiting[]; line: number }) {
  return <section className="dw-panel dwm-panel rp-panel" aria-label="Longest waiting">
    <div className="dw-panel-head"><div><h2>Longest waiting</h2></div></div>
    {rows.length === 0 ? <Empty>Nobody is waiting at a stage right now.</Empty>
      : <ScrollEdges label="The ten candidates who have waited longest at their stage">
        <table className="dwr-table dwm-table is-list is-longest" style={{ minWidth: 460 }}>
          <caption className="rp-sr">The ten candidates who have waited longest at their current stage</caption>
          <colgroup><col style={{ width: 'auto' }} /><col style={{ width: 118 }} /><col style={{ width: 128 }} /><col style={{ width: 78 }} /></colgroup>
          <thead><tr>
            <th scope="col" className="dwr-name-head">Candidate</th>
            <th scope="col" className="is-num">Recruiter</th>
            <th scope="col" className="is-num">Stage</th>
            <th scope="col" className="is-num" title={`Past ${line} days counts as stuck`}>Waiting</th>
          </tr></thead>
          <tbody>
            {rows.map(item => <tr key={item.candidateId} className="dwm-row">
              <th scope="row" className="dwr-name">
                <Link to={`/candidates/${encodeURIComponent(item.candidateId)}`} className="rp-name-link" title={`Open ${item.name}`}>{item.name}</Link>
                {item.careId && <small className="rp-sub">{item.careId}</small>}
              </th>
              <td className="is-num"><span className="rp-wrap">{item.staffName}</span></td>
              <td className="is-num"><span className="rp-wrap" title={`Stage ${item.stage}: ${item.stageLabel}`}>{item.stage} {'·'} {item.stageLabel}</span></td>
              <td className="is-num"><Days days={item.days} line={line} /></td>
            </tr>)}
          </tbody>
        </table>
      </ScrollEdges>}
  </section>
}

/** "Aging leads": the leads nobody has contacted for more than the aging days, the longest first, with the recruiter and a link to the lead. */
export function AgingLeadsPanel({ rows, line }: { rows: AgingLead[]; line: number }) {
  return <section className="dw-panel dwm-panel rp-panel" aria-label="Aging leads">
    <div className="dw-panel-head"><div><h2>Aging leads</h2></div></div>
    {rows.length === 0 ? <Empty>{`Every lead has been contacted within ${line} days.`}</Empty>
      : <ScrollEdges label={`Leads nobody has contacted for more than ${line} days`}>
        <table className="dwr-table dwm-table is-list is-aging" style={{ minWidth: 420 }}>
          <caption className="rp-sr">{`Leads nobody has contacted for more than ${line} days`}</caption>
          <colgroup><col style={{ width: 'auto' }} /><col style={{ width: 128 }} /><col style={{ width: 84 }} /><col style={{ width: 84 }} /></colgroup>
          <thead><tr>
            <th scope="col" className="dwr-name-head">Lead</th>
            <th scope="col" className="is-num">Recruiter</th>
            <th scope="col" className="is-num">Since</th>
            <th scope="col" className="is-num">Waiting</th>
          </tr></thead>
          <tbody>
            {rows.map(item => <tr key={item.leadId} className="dwm-row">
              <th scope="row" className="dwr-name"><Link to={`/leads?lead=${encodeURIComponent(item.leadId)}`} className="rp-name-link" title={`Open ${item.name}`}>{item.name}</Link></th>
              <td className="is-num"><span className={cn('rp-wrap', !item.staffId && 'rp-muted')}>{item.staffName}</span></td>
              <td className="is-num"><span className="dwr-plain">{sinceText(item.since)}</span></td>
              <td className="is-num"><Days days={item.days} line={line} /></td>
            </tr>)}
          </tbody>
        </table>
      </ScrollEdges>}
  </section>
}

