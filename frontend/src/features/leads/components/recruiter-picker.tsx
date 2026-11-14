import type { User } from '@/shared/model/types'
import { initials } from '@/shared/lib/format'
import { cn } from '@/shared/lib/cn'
import { CheckboxInput } from '@/shared/ui/primitives'
import { availableRecruiters, splitSummary, toggleRecruiter } from '../lead-bulk-model'

/** An extra first row that excludes the recruiters, e.g. "Nobody yet (assign later)". */
export interface PickerExclusive { label: string; /** The line under the list while this row is on. */ summary: string; active: boolean; onChange: (active: boolean) => void }

/**
 * Tick the recruiters who share the leads equally. Nothing ticked is the "spread evenly between available recruiters" shortcut;
 * one or more ticked replaces it. People on leave stay in the list, disabled. The summary line below says how many leads each
 * person gets (the same rule as the server: backend lead_import.split_equally).
 */
export function RecruiterPicker({ users, selected, onChange, total, disabled = false, label, exclusive }: {
  users: User[]
  selected: string[]
  onChange: (ids: string[]) => void
  /** How many leads are being handed out; null when it is not known yet. */
  total: number | null
  disabled?: boolean
  label: string
  exclusive?: PickerExclusive
}) {
  const staff = users.filter((user) => user.active && user.role === 'staff')
  const available = availableRecruiters(staff)
  const choose = (ids: string[]) => { onChange(ids); if (exclusive?.active && ids.length) exclusive.onChange(false) }
  const off = Boolean(exclusive?.active)
  const picked = off ? 0 : selected.length
  return (
    <fieldset className="ld-pick" disabled={disabled} aria-label={label}>
      <div className="ld-pick-head">
        <span>{label}</span>
        <span className="ld-pick-acts">
          <button type="button" className="ld-pick-link" disabled={disabled || available.length === 0 || (!off && selected.length === available.length)} onClick={() => choose(available.map((user) => user.id))}>Select all available</button>
          <button type="button" className="ld-pick-link" disabled={disabled || (!off && selected.length === 0)} onClick={() => { onChange([]); if (exclusive?.active) exclusive.onChange(false) }}>Clear</button>
        </span>
      </div>
      <div className="ld-pick-list" role="group" aria-label={label}>
        {exclusive && (
          <label className={cn('ld-pick-row is-none', off && 'is-on')}>
            <CheckboxInput checked={off} disabled={disabled} aria-label={exclusive.label} onChange={(on) => { exclusive.onChange(on); if (on) onChange([]) }} />
            <span className="ld-pick-name">{exclusive.label}</span>
          </label>
        )}
        {staff.map((user, index) => {
          const checked = !off && selected.includes(user.id)
          return (
            <label key={user.id} className={cn('ld-pick-row', checked && 'is-on', user.onLeave && 'is-leave')}>
              <CheckboxInput checked={checked} disabled={disabled || user.onLeave} aria-label={`${user.name}${user.onLeave ? ' (on leave)' : ''}`}
                onChange={(on) => choose(toggleRecruiter(off ? [] : selected, user.id, on))} />
              <span className={cn('ld-pick-avatar', `avatar-hue-${index % 6}`)} aria-hidden="true">{initials(user.name)}</span>
              <span className="ld-pick-name">{user.name}{user.onLeave ? <em> (on leave)</em> : null}</span>
            </label>
          )
        })}
        {staff.length === 0 && <p className="ld-pick-empty">There are no active recruiters yet.</p>}
      </div>
      <p className="ld-pick-sum" role="status" aria-live="polite">{off && exclusive ? exclusive.summary : splitSummary(total, picked, available.length)}</p>
    </fieldset>
  )
}
