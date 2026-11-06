import { useMemo } from 'react'
import { AlertTriangle, Users } from 'lucide-react'
import { cn } from '@/shared/lib/cn'
import { useStore } from '@/shared/state/crm-store'
import { candidatesCell, clockTime, msUntil, percentDone, spanLabel, staffNeedingAttention, type DailyStaffSummary, type DailySummary } from '@/features/daily-work/daily-work-model'
import { ScrollEdges } from './daily-table'

const BOARD_COLUMNS = [
  { title: 'Staff', width: 150 }, { title: 'Candidates', width: 250 }, { title: 'Morning', width: 84 }, { title: 'Evening', width: 84 },
  { title: 'Forwarded', width: 96 }, { title: 'Stage moves', width: 104 }, { title: 'Postponed', width: 96 }, { title: 'Overdue', width: 84 }, { title: 'Next deadline', width: 150 },
]

/** Supervisor view: every recruiter's progress, worst first; a row filters the table below. */
export function StaffBoard({ now, staff, selected, onSelect }: { now: number; staff: DailyStaffSummary[]; selected: string; onSelect: (id: string) => void }) {
  const sorted = useMemo(() => staffNeedingAttention(staff), [staff])
  return (
    <section className="dw-panel dw-board" aria-label="Staff progress">
      <div className="dw-panel-head">
        <div><h2><Users size={16} aria-hidden="true" /> Staff progress today</h2><p>Staff who are furthest behind are first. Select a row to see only their candidates.</p></div>
      </div>
      <ScrollEdges label="Progress by staff, scroll sideways for more columns">
        <table className="dw-table dw-staff" aria-label="Progress by staff" style={{ minWidth: BOARD_COLUMNS.reduce((sum, c) => sum + c.width, 0) }}>
          <colgroup>{BOARD_COLUMNS.map((c) => <col key={c.title} style={{ width: c.width }} />)}</colgroup>
          <thead><tr>{BOARD_COLUMNS.map((c) => <th key={c.title} scope="col">{c.title}</th>)}</tr></thead>
          <tbody>
            {sorted.length === 0 && <tr><td colSpan={BOARD_COLUMNS.length} className="dw-none">No staff recruiters yet.</td></tr>}
            {sorted.map((s, index) => <StaffRow key={s.userId} now={now} s={s} alt={index % 2 === 1} active={selected === s.userId} onSelect={() => onSelect(selected === s.userId ? 'all' : s.userId)} />)}
          </tbody>
        </table>
      </ScrollEdges>
    </section>
  )
}

function StaffRow({ s, now, alt, active, onSelect }: { now: number; s: DailyStaffSummary; alt: boolean; active: boolean; onSelect: () => void }) {
  const store = useStore()
  const sum: DailySummary = s.summary
  const cell = candidatesCell(sum, s.shortfall, s.offToday)
  return (
    <tr className={cn('dw-staff-row', alt && 'is-alt', active && 'is-active', sum.overdue > 0 && 'is-late', cell.warn && 'is-short')} tabIndex={0} onClick={onSelect}
      onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onSelect() } }} aria-pressed={active}>
      <td data-label="Staff"><b className="dw-staff-name">{s.name}</b>{!s.active && <small className="dw-sub">inactive</small>}</td>
      <td data-label="Candidates">
        <div className="dw-cand">
          <span className="dw-cand-main">
            {cell.warn && <AlertTriangle size={14} aria-hidden="true" />}
            <b className="tnum">{cell.text}</b>
            {!cell.warn && sum.total > 0 && <span className="dw-bar dw-bar-sm" aria-hidden="true"><i style={{ width: `${percentDone(sum)}%` }} /></span>}
          </span>
          {cell.note && <small className={cn('dw-cand-note', cell.warn && 'is-warn')} title={cell.note}>{cell.note}</small>}
        </div>
      </td>
      <td data-label="Morning" className="tnum">{sum.morning.done}/{sum.morning.total}</td>
      <td data-label="Evening" className="tnum">{sum.evening.done}/{sum.evening.total}</td>
      <td data-label="Forwarded" className="tnum">{s.goals ? `${s.goals.forwardedToClient.actual}/${s.goals.forwardedToClient.target}` : '-'}</td>
      <td data-label="Stage moves" className="tnum">{s.goals ? `${s.goals.stageMoves.actual}/${s.goals.stageMoves.target}` : '-'}</td>
      <td data-label="Postponed"><span className={cn('dw-chip', sum.postponed > 0 && 'is-warn')}>{sum.postponed}</span></td>
      <td data-label="Overdue"><span className={cn('dw-chip', sum.overdue > 0 && 'is-late')}>{sum.overdue}</span></td>
      <td data-label="Next deadline" className="tnum">{sum.total === 0 ? 'No work' : sum.complete ? 'All done' : sum.nextDueAt ? `${clockTime(sum.nextDueAt, store.agencyTimezone)} (${spanLabel(msUntil(sum.nextDueAt, now))})` : 'Overdue'}</td>
    </tr>
  )
}
