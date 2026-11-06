import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ExternalLink } from 'lucide-react'
import { Sheet } from '@/shared/ui/overlay'
import { useStore } from '@/shared/state/crm-store'
import { LEAD_REPORT_TOPICS, useReportData } from '@/features/reports/report-api'
import { staffReportPath } from './staff-report-api'
import {
  CLOSURE_LABEL, STATE_LABEL, clockTime, filterCandidates, formatHours, formatPercent, formatSeconds, leadsCalledText, rangeLabel, scoreTone, stateTone,
  weekdayName, type CandidateFilter, type CandidateRow, type StaffRow, type StaffWeeklyReport,
} from './staff-report-model'

const FILTERS: { key: CandidateFilter; label: string }[] = [
  { key: 'all', label: 'All' }, { key: 'active', label: 'In progress' }, { key: 'stuck', label: 'Stuck' },
  { key: 'moved', label: 'Moved this period' }, { key: 'closed', label: 'Closed' }, { key: 'deployed', label: 'Deployed' },
]

/** The eleven stages as a row of marks: filled up to the furthest stage reached, the current one outlined. */
function Track({ item, labels }: { item: CandidateRow; labels: string[] }) {
  return <ol className={`sw-track is-${item.status}`} aria-label={`Reached stage ${item.reached} of 11, ${item.stageLabel}`}>
    {labels.map((label, index) => {
      const stage = index + 1
      const state = item.status === 'deployed' || stage < item.reached ? 'done' : stage === item.reached ? (item.status === 'closed' ? 'closed' : 'now') : 'todo'
      return <li key={stage} className={`is-${state}`} title={`Stage ${stage}: ${label}`} />
    })}
  </ol>
}

function Parts({ row }: { row: StaffRow }) {
  return <ul className="sw-parts">
    {row.scoreParts.map(part => <li key={part.key}>
      <span>{part.label}<em>{part.weight}%</em></span>
      <i className="sw-meter" aria-hidden="true"><b style={{ width: `${Math.round((part.ratio ?? 0) * 100)}%` }} /></i>
      <small>{part.ratio == null ? `Not measured: ${part.detail}` : `${Math.round(part.ratio * 100)}% - ${part.detail}`}</small>
    </li>)}
  </ul>
}

export function StaffDrawer({ row, report, onClose }: { row: StaffRow; report: StaffWeeklyReport; onClose: () => void }) {
  const store = useStore()
  const navigate = useNavigate()
  const range = { from: report.range.from, to: report.range.to }
  // The detail read follows the same live feed as the page, so a stage move or clock-in updates the open drawer too.
  const { data, error } = useReportData<StaffWeeklyReport>(staffReportPath(range, row.id), store.me.id, '', LEAD_REPORT_TOPICS)
  const detail = data?.staff[0] ?? row
  const [filter, setFilter] = useState<CandidateFilter>('all')
  const labels = report.funnel.map(item => item.label)
  const candidates = data?.candidates
  const shown = candidates ? filterCandidates(candidates.items, filter, report.thresholds.stuckDays) : []
  const zone = report.timezone
  const own = report.highlights.filter(item => item.staffId === row.id)
  const a = detail.attendance
  const t = detail.tasks
  return <Sheet open onClose={onClose} width="xl" title={detail.name} description={`${rangeLabel(range)} - ${detail.email}`}>
    <div className="sw-drawer">
      <section className="sw-drawer-top" aria-label="Summary">
        <div className={`sw-score is-${scoreTone(detail.score)}`}><strong>{detail.score ?? '-'}</strong><span>{detail.score == null ? 'No score' : 'out of 100'}</span></div>
        <Parts row={detail} />
        <dl className="sw-facts">
          <div><dt>Hours worked</dt><dd>{formatHours(a.hoursWorked)}<small>of {formatHours(a.expectedHours)} expected</small></dd></div>
          <div><dt>Active in the CRM</dt><dd>{formatHours(a.activeHours)}<small>browser open and in use</small></dd></div>
          <div><dt>Days</dt><dd>{a.workedDays} worked, {a.absentDays} absent<small>{a.leaveDays} leave, {a.offDays} off</small></dd></div>
          <div><dt>Daily tasks</dt><dd>{t.available ? `${t.completed} of ${t.assigned}` : '-'}<small>{t.available ? `${formatPercent(t.completionPercent)} done, ${t.postponed} postponed, ${t.overdue} overdue` : 'No task data yet'}</small></dd></div>
          <div><dt>Stage moves</dt><dd>{detail.stages.moves}<small>goal {detail.goals.stageMovesPerDay} a day</small></dd></div>
          <div><dt>Forwarded to client</dt><dd>{detail.outcomes.forwarded}<small>goal {detail.goals.forwardedPerDay} a day</small></dd></div>
        </dl>
      </section>

      {own.length > 0 && <section aria-label="Questions to ask">
        <h3>Questions to ask</h3>
        <ul className="sw-questions">{own.map(item => <li key={item.id} className={`is-${item.severity}`}><b>{item.title}</b><span>{item.detail}</span></li>)}</ul>
      </section>}

      {detail.leads?.available && <LeadBlock leads={detail.leads} />}

      <section aria-label="Day by day">
        <h3>Day by day</h3>
        <div className="sw-scroll"><table className="sw-table sw-days">
          <thead><tr><th>Day</th><th>Attendance</th><th>In / out</th><th className="num">Hours</th><th className="num">Active</th><th className="num">Tasks done</th><th className="num">Missed</th><th className="num">Stage moves</th></tr></thead>
          <tbody>{detail.days.map(day => <tr key={day.date} className={day.state === 'absent' ? 'is-flag' : undefined}>
            <td><b>{weekdayName(day.date)}</b> {day.date.slice(8)}</td>
            <td><span className={`sw-chip is-${stateTone(day.state)}`}>{STATE_LABEL[day.state]}</span>{day.missingCheckout && <span className="sw-chip is-warn">No check-out</span>}</td>
            <td>{day.firstIn ? `${clockTime(day.firstIn, zone)} - ${day.lastOut ? clockTime(day.lastOut, zone) : 'now'}` : ''}</td>
            <td className="num">{day.workedSeconds ? formatSeconds(day.workedSeconds) : ''}</td>
            <td className="num">{day.activeSeconds ? formatSeconds(day.activeSeconds) : ''}</td>
            <td className="num">{day.tasks.assigned ? `${day.tasks.done} of ${day.tasks.assigned}` : ''}</td>
            <td className={`num${day.tasks.missed ? ' is-bad' : ''}`}>{day.tasks.missed || ''}</td>
            <td className="num">{day.stageMoves || ''}</td>
          </tr>)}</tbody>
        </table></div>
      </section>

      <section aria-label="Stages">
        <h3>Stage by stage</h3>
        <div className="sw-scroll"><table className="sw-table sw-stages">
          <thead><tr><th />{labels.map((label, index) => <th key={label} className="num" title={label}>{index + 1}</th>)}</tr></thead>
          <tbody>
            <tr><th>Waiting in stage now</th>{labels.map((label, index) => <td key={label} className="num">{detail.stages.inStageNow[index + 1] || ''}</td>)}</tr>
            <tr><th>Moved this period</th>{labels.map((label, index) => <td key={label} className="num">{detail.stages.movesByStage[index + 1] || ''}</td>)}</tr>
            <tr><th>Ever reached</th>{labels.map((label, index) => <td key={label} className="num">{detail.stages.reached[index + 1] || ''}</td>)}</tr>
          </tbody>
        </table></div>
      </section>

      <section aria-label="Candidates end to end">
        <div className="sw-section-head">
          <h3>Candidates, prescreening to visa and deployment</h3>
          <div className="sw-pills" role="group" aria-label="Show candidates">
            {FILTERS.map(item => <button key={item.key} type="button" aria-pressed={filter === item.key} onClick={() => setFilter(item.key)}>{item.label}</button>)}
          </div>
        </div>
        {error && !candidates && <p className="sw-empty" role="alert">{error}</p>}
        {!candidates && !error && <p className="sw-empty">Loading candidates...</p>}
        {candidates && <>
          <p className="sw-note">{candidates.total > candidates.shown ? `Showing ${candidates.shown} of ${candidates.total}: in-progress candidates first, then deployed and closed.` : `${candidates.total} candidate${candidates.total === 1 ? '' : 's'} placed on orders.`}</p>
          {shown.length === 0 ? <p className="sw-empty">No candidates match this filter.</p> : <div className="sw-scroll"><table className="sw-table sw-candidates">
            <thead><tr><th>Candidate</th><th>Order</th><th>Progress through the 11 stages</th><th>Now</th><th className="num">Days there</th><th className="num">Moved</th></tr></thead>
            <tbody>{shown.map(item => <tr key={item.applicationId} className={item.status === 'active' && (item.daysInStage ?? 0) >= report.thresholds.stuckDays ? 'is-flag' : undefined}>
              <td><button type="button" className="sw-link" onClick={() => navigate(`/candidates/${item.candidateId}`)}>{item.candidate}<ExternalLink size={12} aria-hidden="true" /></button><small>{item.ref}</small></td>
              <td>{item.order}<small>{item.designation} - {item.client}</small></td>
              <td><Track item={item} labels={labels} /></td>
              <td>{item.status === 'deployed' ? <span className="sw-chip is-good">Deployed</span> : item.status === 'closed' ? <span className="sw-chip is-bad" title={item.closureReason ? CLOSURE_LABEL[item.closureReason] : undefined}>Closed</span> : item.stageLabel}
                {item.status === 'closed' && item.closureReason && <small>{CLOSURE_LABEL[item.closureReason] ?? item.closureReason}</small>}</td>
              <td className="num">{item.daysInStage ?? ''}</td>
              <td className="num">{item.movedInRange || ''}</td>
            </tr>)}</tbody>
          </table></div>}
        </>}
      </section>
    </div>
  </Sheet>
}

/** The person's Lead sheet numbers for the period. The score counts them only as the optional fourth part above. */
function LeadBlock({ leads }: { leads: NonNullable<StaffRow['leads']> }) {
  return <section aria-label="Lead sheet">
    <h3>Lead sheet</h3>
    <dl className="sw-facts sw-lead-facts">
      <div><dt>Leads assigned</dt><dd>{leads.assigned}<small>{leadsCalledText(leads)}</small></dd></div>
      <div><dt>Calls logged</dt><dd>{leads.calls}<small>{leads.worked} {leads.worked === 1 ? 'lead' : 'leads'} worked</small></dd></div>
      <div><dt>Interested</dt><dd>{leads.interested}<small>marked interested</small></dd></div>
      <div><dt>Resumes received</dt><dd>{leads.resumesReceived}<small>on their leads</small></dd></div>
      <div><dt>Converted</dt><dd>{leads.converted}<small>{leads.conversionPercent == null ? 'no rate yet' : `${formatPercent(leads.conversionPercent)} of leads worked`}</small></dd></div>
      <div><dt>Follow-ups overdue</dt><dd className={leads.followUpsOverdue ? 'is-bad' : undefined}>{leads.followUpsOverdue}<small>{leads.open} open leads held now</small></dd></div>
    </dl>
    <p className="sw-lead-note">Credited to whoever logged the call; resumes and conversions to the lead&rsquo;s owner. Overdue follow-ups are as of now.</p>
  </section>
}
