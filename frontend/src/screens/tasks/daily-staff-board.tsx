import { useMemo } from 'react'
import { Users } from 'lucide-react'
import { cn } from '@/shared/lib/cn'
import { staffByWorkLeft, staffSummaryLine, type DailyStaffSummary } from '@/features/daily-work/daily-work-model'
import { ScrollEdges } from './daily-table'

const BOARD_COLUMNS = [{ title: 'Recruiter', width: 190 }, { title: 'Candidates', width: 300 }]

/** Supervisor view: every recruiter's candidates as "N to work · M done today", most to work first; a row filters the table below. */
export function StaffBoard({ staff, selected, onSelect }: { staff: DailyStaffSummary[]; selected: string; onSelect: (id: string) => void }) {
  const sorted = useMemo(() => staffByWorkLeft(staff), [staff])
  return (
    <section className="dw-panel dw-board" aria-label="Recruiter candidates">
      <div className="dw-panel-head">
        <div><h2><Users size={16} aria-hidden="true" /> Candidates by recruiter</h2><p>Recruiters with the most candidates to work are first. Select a row to see only their candidates.</p></div>
      </div>
      <ScrollEdges label="Candidates by recruiter, scroll sideways for more columns">
        <table className="dw-table dw-staff" aria-label="Candidates by recruiter" style={{ minWidth: BOARD_COLUMNS.reduce((sum, c) => sum + c.width, 0) }}>
          <colgroup>{BOARD_COLUMNS.map((c) => <col key={c.title} style={{ width: c.width }} />)}</colgroup>
          <thead><tr>{BOARD_COLUMNS.map((c) => <th key={c.title} scope="col">{c.title}</th>)}</tr></thead>
          <tbody>
            {sorted.length === 0 && <tr><td colSpan={BOARD_COLUMNS.length} className="dw-none">No recruiters yet.</td></tr>}
            {sorted.map((s, index) => <StaffRow key={s.userId} s={s} alt={index % 2 === 1} active={selected === s.userId} onSelect={() => onSelect(selected === s.userId ? 'all' : s.userId)} />)}
          </tbody>
        </table>
      </ScrollEdges>
    </section>
  )
}

function StaffRow({ s, alt, active, onSelect }: { s: DailyStaffSummary; alt: boolean; active: boolean; onSelect: () => void }) {
  return (
    <tr className={cn('dw-staff-row', alt && 'is-alt', active && 'is-active')} tabIndex={0} onClick={onSelect}
      onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onSelect() } }} aria-pressed={active}>
      <td data-label="Recruiter"><b className="dw-staff-name">{s.name}</b>{!s.active && <small className="dw-sub">inactive</small>}</td>
      <td data-label="Candidates" className={cn('tnum', s.summary.total === 0 && 'dw-none')}>{staffSummaryLine(s.summary)}</td>
    </tr>
  )
}
