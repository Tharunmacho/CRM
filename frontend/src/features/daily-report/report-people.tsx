import { CircleCheck, Clock, Minus, Users, type LucideIcon } from 'lucide-react'
import { cn } from '@/shared/lib/cn'
import { initials } from '@/shared/lib/format'
import { actionsLine, actionsOf, personStatus, sharePct, staffByAttention, type PersonStatus } from './daily-report-view'
import type { StaffRow } from './daily-report-types'

const ICON: Record<PersonStatus['tone'], LucideIcon> = { behind: Clock, 'on-track': CircleCheck, none: Minus }

/** A deterministic avatar colour per name: the same hue classes as every other avatar in the app (and the Tasks board). */
function hueOf(name: string) {
  return [...name].reduce((total, character) => total + character.charCodeAt(0), 0) % 6
}

/**
 * The Team card, built like the Tasks "Recruiters" card (same components' CSS: dw-panel, dwb-card, dwb-chip): one card
 * per person, the ones who have not reported first. A card filters the table below to that person; pressing it again, or
 * "All recruiters", shows everyone. The bar is the person's share of the team's actions.
 */
export function PeopleBoard({ staff, single, todayOnly, selected, onSelect }: {
  staff: StaffRow[]; single: boolean; todayOnly: boolean; selected: string; onSelect: (id: string) => void
}) {
  const sorted = staffByAttention(staff, single)
  const teamTotal = staff.reduce((all, row) => all + actionsOf(row), 0)
  return <section className="dw-panel dwb-board" aria-label="Team">
    <div className="dw-panel-head dwb-head">
      <div><h2><Users size={16} aria-hidden="true" /> Team</h2></div>
      <div className="dwb-head-tools">
        <button type="button" className={cn('tk-chip', selected === 'all' && 'is-active')} aria-pressed={selected === 'all'} onClick={() => onSelect('all')}>All recruiters</button>
      </div>
    </div>
    {sorted.length === 0
      ? <p className="dwb-none">There is nobody in this selection.</p>
      : <ul className="dwb-cards">{sorted.map(row => {
        const status = personStatus(row, single)
        const Icon = ICON[status.tone]
        const n = actionsOf(row)
        const active = selected === row.id
        const share = sharePct(n, teamTotal)
        return <li key={row.id}>
          <button type="button" className={cn('dwb-card', `is-${status.tone}`, active && 'is-active')} aria-pressed={active}
            aria-label={`${row.name}: ${status.label}, ${actionsLine(n, todayOnly)}. ${active ? 'Showing their figures. Press to show everyone.' : 'Show their figures.'}`}
            onClick={() => onSelect(active ? 'all' : row.id)}>
            <span className="dwb-card-top">
              <span className={cn('record-avatar dwb-avatar', `avatar-hue-${hueOf(row.name)}`)} aria-hidden="true">{initials(row.name)}</span>
              <span className="dwb-card-name"><b title={row.name}>{row.name}</b></span>
              <span className={cn('dwb-chip', `is-${status.tone}`)}><Icon size={13} strokeWidth={2} aria-hidden="true" />{status.label}</span>
            </span>
            {n === 0
              ? <span className="dwb-card-empty">{single ? 'Nothing recorded today' : 'Nothing recorded in this period'}</span>
              : <>
                <span className="dwb-card-numbers tnum"><span><b>{n.toLocaleString('en-IN')}</b> {n === 1 ? 'action' : 'actions'}{todayOnly ? ' today' : ''}</span><em>{share}%</em></span>
                <span className="dwb-stack" aria-hidden="true"><i className="is-todo" style={{ flex: '0 0 auto', width: `${Math.max(4, share)}%` }} /></span>
              </>}
          </button>
        </li>
      })}</ul>}
  </section>
}
