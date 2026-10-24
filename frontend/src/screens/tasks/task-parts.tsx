import { useState } from 'react'
import { MessageSquarePlus } from 'lucide-react'
import { cn } from '@/shared/lib/cn'
import { slaState, smartTime } from '@/shared/lib/format'
import { taskClockStart } from '@/shared/lib/task-time'
import { useStore } from '@/shared/state/crm-store'
import type { Task } from '@/shared/model/types'
import { Button, Textarea } from '@/shared/ui/primitives'
import { SlaRing } from './task-identity'

export type ClockTone = 'ok' | 'soon' | 'over' | 'esc' | 'done'

export interface TaskClock {
  tone: ClockTone
  /** Short chip text, for example "31h left" or "2d 4h overdue". */
  label: string
  /** Second line under the chip. */
  note: string
  /** Share of the 48-hour window used, 0 to 1. */
  fill: number
}

/** "63h" reads badly; show days once the span passes 48 hours. */
export function span(hours: number) {
  const whole = Math.max(0, Math.floor(hours))
  if (whole >= 48) {
    const days = Math.floor(whole / 24)
    const rest = whole % 24
    return rest ? `${days}d ${rest}h` : `${days}d`
  }
  return `${whole}h`
}

/** Where a task sits against its 48-hour clock, as a chip the table and cards can share. */
export function clockOf(task: Task, now: Date): TaskClock {
  if (task.completedAt) return { tone: 'done', label: 'Completed', note: smartTime(task.completedAt), fill: 1 }
  const sla = slaState(taskClockStart(task), now)
  if (sla.overdue) {
    const hours = sla.hoursOverdue
    return {
      tone: task.escalated ? 'esc' : 'over',
      label: hours < 1 ? `${Math.max(1, Math.round(hours * 60))}m overdue` : `${span(hours)} overdue`,
      note: task.escalated ? 'Escalated' : '',
      fill: 1,
    }
  }
  const left = sla.hoursLeft
  const label = left < 1 ? `${Math.max(1, Math.round(left * 60))}m left` : `${span(left)} left`
  if (task.escalated) return { tone: 'esc', label, note: 'Escalated', fill: sla.fill }
  return { tone: sla.urgency === 'due' ? 'soon' : 'ok', label, note: sla.urgency === 'due' ? 'Due soon' : 'On time', fill: sla.fill }
}

/** Time remaining: the 48-hour ring beside the time left (or overdue) and where the task stands. */
export function ClockCell({ clock }: { clock: TaskClock }) {
  return (
    <div className={cn('tk-clock', `is-${clock.tone}`)}>
      <SlaRing fill={clock.fill} tone={clock.tone} label={clock.tone === 'done' ? 'Completed' : `${Math.round(clock.fill * 100)} percent of the 48-hour limit used`} />
      <span className="tk-clock-text">
        <b>{clock.label}</b>
        {clock.note && <small>{clock.note}</small>}
      </span>
    </div>
  )
}

/**
 * Daily updates. Remarks are permanent: a write box over a read-only history, never an edit form.
 */
export function RemarkPanel({ task, onReassign, onReset }: { task: Task; onReassign?: () => void; onReset?: () => void }) {
  const store = useStore()
  const [body, setBody] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const remarks = store.remarksFor(task.id)
  const open = !task.completedAt && !task.cancelledAt
  const canWork = open && (store.can('work_any_candidate') || task.ownerId === store.me.id)
  const missing = open && !store.updatedToday(task.id)

  const save = async () => {
    const text = body.trim()
    if (busy || !canWork || !text) return
    setBusy(true); setError('')
    try { await store.addRemark(task.id, text); setBody('') }
    catch (e) { setError(e instanceof Error ? e.message : 'We could not save the remark. Please try again.') }
    finally { setBusy(false) }
  }

  return (
    <div className="tk-remarks" onClick={(event) => event.stopPropagation()}>
      <div className="tk-remarks-write">
        <h4>Daily update</h4>
        {missing && canWork && <p className="tk-remarks-flag">No remark has been added today. Each open task needs one update a day.</p>}
        {canWork ? (
          <>
            <Textarea
              rows={3}
              value={body}
              onChange={(e) => setBody(e.target.value)}
              placeholder="What happened today? e.g. Called, candidate will send resume tomorrow"
              maxLength={10000}
              disabled={busy}
              aria-label="Add remark"
            />
            {error && <p role="alert" className="tk-remarks-error">{error}</p>}
            <div className="tk-remarks-actions">
              <Button variant="primary" size="sm" disabled={busy || !body.trim()} onClick={save}><MessageSquarePlus size={14} aria-hidden="true" />{busy ? 'Saving…' : 'Add remark'}</Button>
              {onReassign && <Button size="sm" variant="ghost" onClick={onReassign}>Reassign</Button>}
              {onReset && <Button size="sm" variant="ghost" onClick={onReset}>Restart 48-hour clock</Button>}
            </div>
          </>
        ) : (
          <p className="tk-remarks-note">{open ? 'Only the person this task is assigned to, or a manager or admin, can add remarks.' : 'This task is closed, so no more remarks are needed.'}</p>
        )}
      </div>
      <div className="tk-remarks-history">
        <h4>History{remarks.length ? <span>{remarks.length}</span> : null}</h4>
        {remarks.length ? (
          <ul>
            {remarks.map((r) => (
              <li key={r.id}>
                <p>{r.body}</p>
                <small>{store.user(r.authorId)?.name ?? 'Unknown'} · {smartTime(r.createdAt)}</small>
              </li>
            ))}
          </ul>
        ) : (
          <p className="tk-remarks-note">No remarks yet. Each remark shows when it was added and cannot be changed or deleted.</p>
        )}
      </div>
    </div>
  )
}
