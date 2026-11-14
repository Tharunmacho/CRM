import { useMemo, type ReactNode } from 'react'
import { CircleAlert, CircleCheck, Clock, Minus, Users, X, type LucideIcon } from 'lucide-react'
import { cn } from '@/shared/lib/cn'
import { initials } from '@/shared/lib/format'
import {
  STATUS_LABEL, donePercent, followUpByCount, recruiterStatus, staffByAttention,
  type DailyStaffSummary, type RecruiterStatus,
} from '@/features/daily-work/daily-work-model'

const STATUS_ICON: Record<RecruiterStatus, LucideIcon> = { idle: CircleAlert, behind: Clock, 'on-track': CircleCheck, none: Minus }

/** A deterministic avatar colour per name (the same hue classes as every other avatar in the app). */
function hueOf(name: string) {
  return [...name].reduce((total, character) => total + character.charCodeAt(0), 0) % 6
}

function Avatar({ name }: { name: string }) {
  return <span className={cn('record-avatar dwb-avatar', `avatar-hue-${hueOf(name)}`)} aria-hidden="true">{initials(name)}</span>
}

/** The "All recruiters" reset chip plus, while a recruiter is picked, who is showing. */
function BoardHead({ title, hint, selected, onSelect, children }: {
  title: string; hint: string; selected: string; onSelect: (id: string) => void; children?: ReactNode
}) {
  return (
    <div className="dw-panel-head dwb-head">
      <div><h2><Users size={16} aria-hidden="true" /> {title}</h2><p>{hint}</p></div>
      <div className="dwb-head-tools">
        {children}
        <button type="button" className={cn('tk-chip', selected === 'all' && 'is-active')} aria-pressed={selected === 'all'} onClick={() => onSelect('all')}>All recruiters</button>
      </div>
    </div>
  )
}

/**
 * Admin and manager: one card per recruiter, the ones who need attention first (Idle, then Behind, then On track, then no
 * candidates; the status rule is `recruiterStatus` in daily-work-model). A card filters the candidate list below to that
 * recruiter (the ?staff= parameter); pressing it again, or "All recruiters", clears the filter. `idleOnly` narrows the cards to
 * recruiters with nothing done yet (the "Nothing done yet" tile sets it).
 */
export function StaffBoard({ staff, selected, onSelect, idleOnly, onClearIdle }: {
  staff: DailyStaffSummary[]; selected: string; onSelect: (id: string) => void; idleOnly: boolean; onClearIdle: () => void
}) {
  const sorted = useMemo(() => staffByAttention(staff), [staff])
  const shown = idleOnly ? sorted.filter((s) => recruiterStatus(s.summary) === 'idle') : sorted
  return (
    <section className="dw-panel dwb-board" id="recruiters" aria-label="Recruiters">
      <BoardHead title="Recruiters" selected={selected} onSelect={onSelect}
        hint="Recruiters who need attention are first. Select one to see only their candidates below.">
        {idleOnly && <button type="button" className="tk-chip is-active dwb-chip-x" onClick={onClearIdle}>Nothing done yet<X size={14} aria-hidden="true" /><span className="sr-only">Show every recruiter</span></button>}
      </BoardHead>
      {shown.length === 0
        ? <p className="dwb-none">{staff.length === 0 ? 'No recruiters yet.' : 'Every recruiter has done something today.'}</p>
        : <ul className="dwb-cards">{shown.map((s) => <li key={s.userId}><StaffCard s={s} active={selected === s.userId} onSelect={() => onSelect(selected === s.userId ? 'all' : s.userId)} /></li>)}</ul>}
    </section>
  )
}

function StaffCard({ s, active, onSelect }: { s: DailyStaffSummary; active: boolean; onSelect: () => void }) {
  const { toWork, doneToday, total } = s.summary
  const status = recruiterStatus(s.summary)
  const Icon = STATUS_ICON[status]
  const percent = donePercent(s.summary)
  return (
    <button type="button" className={cn('dwb-card', `is-${status}`, active && 'is-active')} aria-pressed={active}
      aria-label={`${s.name}: ${STATUS_LABEL[status]}, ${toWork} to work, ${doneToday} done today. ${active ? 'Showing their candidates. Press to show everyone.' : 'Show their candidates.'}`} onClick={onSelect}>
      <span className="dwb-card-top">
        <Avatar name={s.name} />
        <span className="dwb-card-name"><b title={s.name}>{s.name}</b>{!s.active && <small>Inactive</small>}</span>
        <span className={cn('dwb-chip', `is-${status}`)}><Icon size={13} strokeWidth={2} aria-hidden="true" />{STATUS_LABEL[status]}</span>
      </span>
      {total === 0
        ? <span className="dwb-card-empty">Nothing assigned today</span>
        : <>
          <span className="dwb-card-numbers tnum"><span><b>{toWork}</b> to work</span><i aria-hidden="true">·</i><span><b>{doneToday}</b> done</span><em>{percent}%</em></span>
          <span className="dwb-stack" aria-hidden="true">
            {doneToday > 0 && <i className="is-done" style={{ flexGrow: doneToday }} />}
            {toWork > 0 && <i className="is-todo" style={{ flexGrow: toWork }} />}
          </span>
        </>}
    </button>
  )
}

/** Follow-up counterpart: one card per recruiter with how many follow-up candidates they hold (bar = share of the largest list). */
export function FollowUpBoard({ staff, selected, onSelect }: {
  staff: { userId: string; name: string; active: boolean; summary: { total: number } }[]; selected: string; onSelect: (id: string) => void
}) {
  const sorted = useMemo(() => followUpByCount(staff), [staff])
  const largest = Math.max(1, ...sorted.map((s) => s.summary.total))
  return (
    <section className="dw-panel dwb-board" id="recruiters" aria-label="Recruiters">
      <BoardHead title="Recruiters" selected={selected} onSelect={onSelect}
        hint="Who holds the most follow-up candidates is first. Select one to see only their candidates below." />
      {sorted.length === 0
        ? <p className="dwb-none">No recruiters yet.</p>
        : <ul className="dwb-cards">{sorted.map((s) => {
          const active = selected === s.userId
          const n = s.summary.total
          return (
            <li key={s.userId}>
              <button type="button" className={cn('dwb-card', n === 0 && 'is-none', active && 'is-active')} aria-pressed={active}
                aria-label={`${s.name}: ${n} follow-up ${n === 1 ? 'candidate' : 'candidates'}. ${active ? 'Press to show everyone.' : 'Show their candidates.'}`}
                onClick={() => onSelect(active ? 'all' : s.userId)}>
                <span className="dwb-card-top">
                  <Avatar name={s.name} />
                  <span className="dwb-card-name"><b title={s.name}>{s.name}</b>{!s.active && <small>Inactive</small>}</span>
                </span>
                {n === 0
                  ? <span className="dwb-card-empty">No follow-up candidates</span>
                  : <>
                    <span className="dwb-card-numbers tnum"><span><b>{n}</b> {n === 1 ? 'candidate' : 'candidates'}</span></span>
                    <span className="dwb-stack" aria-hidden="true"><i className="is-todo" style={{ flex: '0 0 auto', width: `${Math.max(4, Math.round((n / largest) * 100))}%` }} /></span>
                  </>}
              </button>
            </li>
          )
        })}</ul>}
    </section>
  )
}
