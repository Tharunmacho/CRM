import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { format } from 'date-fns'
import {
  AlertCircle, CheckCheck, Check, Clock3, Pencil,
  RefreshCw, Trash2,
} from 'lucide-react'
import { api } from '@/shared/api/client'
import { cn } from '@/shared/lib/cn'
import { useStore } from '@/shared/state/crm-store'
import type { TodoItem } from '@/shared/model/types'
import { Badge, PageHeader, Panel } from '@/shared/ui/display'
import { sections, sectionKey } from './organiser'
import { Button, Checkbox } from '@/shared/ui/primitives'
import { Dialog } from '@/shared/ui/overlay'
import { useDeadlineClock, useWorkspaceRead } from '@/features/staff/use-workspace-read'
import { DailyWorksheet } from './worksheet'
import { TodoDialog } from './todo-dialog'
import {
  Flag, LINK_KINDS, LinkIcon, dayDate, dayLabel, groupByDay, lateText, linkIdOf, linkKindOf,
  untilText,
} from './todo-meta'
import '@/styles/todo-v2.css'

const PRIORITY_TONE = { high: 'amber', normal: 'neutral', low: 'green' } as const

export default function TodoPage() {
  const store = useStore()
  const todoRead = useWorkspaceRead()
  const clock = useDeadlineClock()
  const [dialog, setDialog] = useState<{ item: TodoItem | null } | null>(null)
  const groups = useMemo(() => groupByDay(store.todos.filter(item => item.ownerId === store.me.id)
    .sort((a, b) => +new Date(b.dueAt) - +new Date(a.dueAt))), [store.todos, store.me.id])
  const openDialog = (item: TodoItem | null = null) => setDialog({ item })

  if (todoRead.loading || todoRead.error) return <div className="todo-screen td">
    <PageHeader title="To Do" />
    {todoRead.error ? (
      <Panel className="td-state"><div className="td-state-body">
        <AlertCircle size={20} aria-hidden="true" />
        <div>
          <p role="alert" className="td-state-title">We could not load To Do</p>
          <p className="td-state-text">{todoRead.error}</p>
        </div>
        <Button icon={<RefreshCw />} onClick={todoRead.refresh}>Try again</Button>
      </div></Panel>
    ) : (
      <div className="td-skeleton" role="status" aria-label="Loading To Do">
        <div className="td-skel-tiles">{[0, 1, 2, 3].map((i) => <span key={i} />)}</div>
        <div className="td-skel-body"><span /><span /></div>
      </div>
    )}
  </div>

  return (
    <div className="todo-screen td">
      <PageHeader
        title="To Do"
        actions={
          <>
            <Button icon={<RefreshCw />} onClick={todoRead.refresh}>Refresh</Button>

          </>
        }
      />

      <DailyWorksheet />
      {groups.length > 0 && <details className="td-organiser td-saved">
        <summary>My entries</summary>
        <Panel flush className="td-list-panel">
            <div className="td-scroll">
              <ol className="td-timeline" aria-label="Saved entries">
                {groups.map((g) => (
                  <li key={g.key} className="td-day">
                    <h3 className="td-day-head">
                      <span>{dayLabel(g.date, clock)}</span>
                      <time dateTime={g.key}>{dayDate(g.date, clock)}</time>
                    </h3>
                    <ul className="td-day-list">
                      {g.items.map((t) => <Row key={t.id} item={t} now={clock} onEdit={() => openDialog(t)} />)}
                    </ul>
                  </li>
                ))}
              </ol>
            </div>
        </Panel>

      </details>}

      {dialog && <TodoDialog key={dialog.item?.id || 'new'} item={dialog.item} onClose={() => setDialog(null)} />}
    </div>
  )
}

/* ------------------------------------------------------------------ rows */

function Row({ item, now, onEdit }: { item: TodoItem; now: number; onEdit: () => void }) {
  const store = useStore()
  const [busy, setBusy] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const perform = async (action: () => Promise<unknown>) => {
    if (busy) return
    setBusy(true)
    try {
      await action()
      try { await store.refresh({ keepCurrent: true }) }
      catch { store.notify('Saved. Refresh the list to see the latest changes.', 'warn') }
    } catch (e) { store.notify(e instanceof Error ? e.message : 'We could not save the change. Refresh the list and try again.', 'warn') }
    finally { setBusy(false) }
  }
  const due = new Date(item.dueAt)
  const overdue = !item.done && due.getTime() < now
  const kind = linkKindOf(item.linkTo)
  const country = useLinkedCountry(item.linkTo, kind)
  const priorityLabel = item.priority[0].toUpperCase() + item.priority.slice(1)
  const clockTime = format(due, 'HH:mm')

  return (
    <>
      <li className={cn('td-item', `is-${item.priority}`, overdue && 'is-late', item.done && 'is-done')}>
        <time className="td-time tnum" dateTime={item.dueAt} title={format(due, 'EEEE d MMMM yyyy, HH:mm')}>{clockTime}</time>
        <span className="td-rail" aria-hidden="true"><i>{item.done && <Check size={10} strokeWidth={3} />}</i></span>
        <article className="td-card">
          <div className="td-check">
            <Checkbox
              aria-label={`${item.done ? 'Reopen' : 'Complete'} ${item.title}`}
              checked={item.done}
              disabled={busy}
              onChange={() => void perform(() => api.patch(`/todos/${item.id}`, { done: !item.done }))}
            />
          </div>

          <div className="td-body">
            <p className="td-title">{item.title}</p>
            {item.section && <small>{sections[sectionKey(item.section)].label}</small>}
            {item.details && Object.keys(item.details).length > 0 && <dl className="td-entry-details">
              {Object.entries(item.details).map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}
            </dl>}
            {item.notes && <p className="td-notes">{item.notes}</p>}
            <div className="td-chips">
              {item.done ? (
                <span className="td-chip td-due is-done" title={format(due, 'EEEE d MMMM yyyy, HH:mm')}>
                  <CheckCheck size={13} aria-hidden="true" /> Completed
                </span>
              ) : overdue ? (
                <span className="td-chip td-due is-late" title={format(due, 'EEEE d MMMM yyyy, HH:mm')}>
                  <AlertCircle size={13} aria-hidden="true" />
                  <span><span className="td-chip-clock tnum">{clockTime} · </span>{lateText(due, now)}</span>
                </span>
              ) : (
                <span className="td-chip td-due" title={format(due, 'EEEE d MMMM yyyy, HH:mm')}>
                  <Clock3 size={13} aria-hidden="true" />
                  <span><span className="td-chip-clock tnum">{clockTime} · </span>Due {untilText(due, now)}</span>
                </span>
              )}
              <Badge tone={PRIORITY_TONE[item.priority]} variant="dot" className="td-prio-pill">{priorityLabel}</Badge>
              {item.linkTo && (
                <Link to={item.linkTo} className="td-chip td-link" title={`Open ${kind ? LINK_KINDS[kind].label.toLowerCase() : 'linked item'}: ${item.linkLabel || ''}`.trim()}>
                  <LinkIcon kind={kind} />
                  {kind && <b>{LINK_KINDS[kind].label}</b>}
                  <span>{item.linkLabel || 'Linked item'}</span>
                  {country !== undefined && <Flag country={country} />}
                </Link>
              )}
            </div>
          </div>

          <div className="td-actions">
            <button type="button" onClick={onEdit} disabled={busy} aria-label={`Edit ${item.title}`} title="Edit"><Pencil size={16} /><span className="td-act-label">Edit</span></button>
            <button type="button" className="is-danger" disabled={busy} onClick={() => setDeleting(true)} aria-label={`Delete ${item.title}`} title="Delete"><Trash2 size={16} /><span className="td-act-label">Delete</span></button>
          </div>
        </article>
      </li>
      <Dialog open={deleting} onClose={() => { if (!busy) setDeleting(false) }} title="Delete this to-do?"
        description="This removes the to-do from your list. The linked candidate, client or order is not deleted."
        footer={<><Button disabled={busy} variant="ghost" onClick={() => setDeleting(false)}>Keep it</Button><Button disabled={busy} variant="danger" onClick={() => void perform(async () => { await api.del(`/todos/${item.id}`); setDeleting(false) })}>{busy ? 'Deleting…' : 'Delete'}</Button></>}>
        <p className="text-sm font-medium">{item.title}</p><p className="mt-2 text-sm text-ink-500">This cannot be undone.</p>
      </Dialog>
    </>
  )
}

/**
 * The country of a linked client or order, for the flag on the link chip.
 * `undefined` = no flag (candidate or other link); `null` = a client/order with no recorded country (globe).
 */
function useLinkedCountry(linkTo: string | undefined | null, kind: ReturnType<typeof linkKindOf>) {
  const store = useStore()
  if (!linkTo || (kind !== 'client' && kind !== 'order')) return undefined
  const id = linkIdOf(linkTo, kind)
  if (kind === 'client') return store.clients.find((c) => c.id === id)?.country ?? null
  const order = store.orders.find((o) => o.id === id)
  return order?.country ?? (order ? store.companies.find((c) => c.id === order.companyId)?.country : undefined) ?? null
}
