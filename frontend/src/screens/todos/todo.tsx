import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { format } from 'date-fns'
import { CalendarCheck2, CalendarClock, CheckCheck, LockKeyhole, Pencil, Plus, TimerOff, Trash2 } from 'lucide-react'
import { api } from '@/shared/api/client'
import { cn } from '@/shared/lib/cn'
import { dateTimeLabel, isSameDay } from '@/shared/lib/format'
import { useStore } from '@/shared/state/crm-store'
import type { Priority, TodoItem } from '@/shared/model/types'
import { Badge, EmptyState, PageHeader, Panel, PanelHeader } from '@/shared/ui/display'
import { SummaryCards } from '@/shared/ui/workspace'
import { Button, Checkbox, Field, Input, Select, Textarea } from '@/shared/ui/primitives'
import { Dialog, Tabs } from '@/shared/ui/overlay'
import { DatePicker } from '@/shared/ui/date-picker'
import { useDeadlineClock, useWorkspaceRead } from '@/features/staff/use-workspace-read'

type TabKey = 'today' | 'upcoming' | 'overdue' | 'done'

const PRIORITY_TONE: Record<Priority, 'stamp' | 'neutral' | 'navy'> = {
  high: 'stamp',
  normal: 'neutral',
  low: 'navy',
}

export default function TodoPage() {
  const store = useStore()
  const todoRead = useWorkspaceRead()
  const clock = useDeadlineClock()
  const [tab, setTab] = useState<TabKey>('today')
  const [adding, setAdding] = useState(false)
  const [editing, setEditing] = useState<TodoItem | null>(null)

  const mine = useMemo(
    () => store.todos.filter((t) => t.ownerId === store.me.id),
    [store.todos, store.me.id],
  )

  const buckets = useMemo(() => {
    const now = new Date(clock)
    const open = mine.filter((t) => !t.done)
    return {
      today: open.filter((t) => isSameDay(t.dueAt, now) && new Date(t.dueAt) >= now),
      upcoming: open.filter((t) => new Date(t.dueAt) > now && !isSameDay(t.dueAt, now)),
      overdue: open.filter((t) => new Date(t.dueAt) < now),
      done: mine.filter((t) => t.done),
    }
  }, [mine, clock])

  const rows = [...buckets[tab]].sort((a, b) => +new Date(a.dueAt) - +new Date(b.dueAt))
  const completion = mine.length ? Math.round((buckets.done.length / mine.length) * 100) : 0

  if (todoRead.loading || todoRead.error) return <div className="todo-screen">
    <PageHeader title="To-do List" eyebrow="Your personal workspace" description="Plan your day, track follow-ups, and keep every commitment in view." />
    <Panel>{todoRead.error ? <div className="space-y-3"><p role="alert" className="text-sm text-stamp-700">{todoRead.error}</p><Button onClick={todoRead.refresh}>Retry to-do list</Button></div>
      : <p role="status" className="text-sm text-ink-500">Loading your current reminders…</p>}</Panel>
  </div>

  return (
    <div className="todo-screen">
      <PageHeader
        title="To-do List"
        eyebrow="Your personal workspace"
        description="Plan your day, track follow-ups, and keep every commitment in view."
        actions={
          <><Button onClick={todoRead.refresh}>Refresh list</Button>
          <Button variant="primary" icon={<Plus className="h-4 w-4" />} onClick={() => setAdding(true)}>
            Add item
          </Button></>
        }
      />

      <SummaryCards
        items={[
          {
            label: 'Today',
            value: buckets.today.length,
            detail: 'On your agenda for today',
            icon: CalendarCheck2,
          },
          {
            label: 'Upcoming',
            value: buckets.upcoming.length,
            detail: 'Planned for the days ahead',
            icon: CalendarClock,
            tone: 'violet',
          },
          {
            label: 'Overdue',
            value: buckets.overdue.length,
            detail: 'Ready for your attention',
            icon: TimerOff,
            tone: 'amber',
          },
          {
            label: 'Completed',
            value: buckets.done.length,
            detail: 'One less thing to think about',
            icon: CheckCheck,
            tone: 'green',
          },
        ]}
      />

      <div className="todo-layout">
        <Panel flush className="todo-list-panel">
          <PanelHeader title="My to-dos" meta="Your reminders, organised by due date" action={<Badge tone="navy"><LockKeyhole size={14} /> Private</Badge>} />
          <Tabs
            tabs={[
              { key: 'today', label: 'Today', count: buckets.today.length },
              { key: 'upcoming', label: 'Upcoming', count: buckets.upcoming.length },
              { key: 'overdue', label: 'Overdue', count: buckets.overdue.length, tone: 'stamp' },
              { key: 'done', label: 'Done', count: buckets.done.length },
            ]}
            active={tab}
            onChange={(k) => setTab(k as TabKey)}
            className="px-2"
          />

          {rows.length === 0 ? (
            <EmptyState
              icon={<CalendarCheck2 size={30} strokeWidth={1.6} />}
              title={
                {
                  today: 'Nothing due today',
                  upcoming: 'Nothing scheduled ahead',
                  overdue: 'Nothing is overdue',
                  done: 'Nothing ticked off yet',
                }[tab]
              }
              description={
                tab === 'done'
                  ? 'Completed items stay here as a record.'
                  : 'Add an item and it shows up in your list and on your dashboard.'
              }
              action={
                tab !== 'done' && (
                  <Button variant="primary" onClick={() => setAdding(true)}>
                    Add item
                  </Button>
                )
              }
            />
          ) : (
            <ul className="workspace-todo-list">
              {rows.map((t) => (
                <Row key={t.id} item={t} onEdit={() => setEditing(t)} />
              ))}
            </ul>
          )}
        </Panel>
        <Panel className="todo-progress-panel">
          <span className="workspace-eyebrow">Your progress</span>
          <h2>Stay on track</h2>
          <p>See what you’ve completed and what still needs your attention.</p>
          <div
            className="todo-completion-ring"
            style={{ background: `conic-gradient(#4169e1 ${completion}%, #edf1fc 0)` }}
            role="img"
            aria-label={`${completion}% of your to-dos completed`}
          >
            <div>
              <strong className="tnum">{completion}%</strong>
              <span>completed</span>
            </div>
          </div>
          <dl>
            <div>
              <dt>Completed</dt>
              <dd className="tnum">{buckets.done.length}</dd>
            </div>
            <div>
              <dt>Still on your list</dt>
              <dd className="tnum">{mine.length - buckets.done.length}</dd>
            </div>
          </dl>
          <div className="todo-privacy-note">
            <LockKeyhole size={16} />
            <span>This list is private to you. Team tasks live in the Tasks page.</span>
          </div>
        </Panel>
      </div>

      {(adding || editing) && <AddDialog key={editing?.id || 'new'} open item={editing} onClose={() => { setAdding(false); setEditing(null) }} />}
    </div>
  )
}

function Row({ item, onEdit }: { item: TodoItem; onEdit: () => void }) {
  const store = useStore()
  const [busy, setBusy] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const perform = async (action: () => Promise<unknown>) => {
    if (busy) return
    setBusy(true)
    try {
      await action()
      try { await store.refresh({ keepCurrent: true }) }
      catch { store.notify('Saved. Refresh the list to see the latest reminders.', 'warn') }
    } catch (e) { store.notify(e instanceof Error ? e.message : 'Could not save the change. Refresh the list before trying again.', 'warn') }
    finally { setBusy(false) }
  }
  const overdue = !item.done && new Date(item.dueAt) < new Date()

  return (
    <><li
      className={cn(
        'workspace-todo-row flex items-start gap-3 border-b border-rule-soft px-4 py-3 last:border-0',
        overdue && 'bg-stamp-50/40',
      )}
    >
      <div className="pt-0.5">
        <Checkbox
          aria-label={`${item.done ? 'Reopen' : 'Complete'} ${item.title}`}
          checked={item.done}
          disabled={busy}
          onChange={() => void perform(() => api.patch(`/todos/${item.id}`, { done: !item.done }))}
        />
      </div>

      <div className="min-w-0 flex-1">
        <p className={cn('text-sm font-medium', item.done ? 'text-ink-400 line-through' : 'text-ink')}>
          {item.title}
        </p>
        {item.notes && <p className="mt-0.5 text-xs leading-snug text-ink-500">{item.notes}</p>}
        <div className="mt-1.5 flex flex-wrap items-center gap-2">
          <span className={cn('text-micro tnum', overdue ? 'font-medium text-stamp-600' : 'text-ink-400')}>
            {overdue ? 'Overdue — ' : ''}
            {dateTimeLabel(item.dueAt)}
          </span>
          {item.priority !== 'normal' && (
            <Badge tone={PRIORITY_TONE[item.priority]}>{item.priority} priority</Badge>
          )}
          {item.linkTo && (
            <Link
              to={item.linkTo}
              className="text-micro font-medium text-navy-700 hover:text-navy-600"
            >
              {item.linkLabel}
            </Link>
          )}
        </div>
      </div>

      <button
        onClick={onEdit}
        disabled={busy}
        aria-label={`Edit ${item.title}`}
        className="shrink-0 rounded-control p-1.5 text-ink-500 hover:bg-field"
      ><Pencil className="h-3.5 w-3.5" /></button>
      <button
        disabled={busy}
        onClick={() => setDeleting(true)}
        aria-label={`Delete ${item.title}`}
        className="shrink-0 rounded-control p-1.5 text-ink-300 hover:bg-stamp-50 hover:text-stamp-600"
      >
        <Trash2 className="h-3.5 w-3.5" />
      </button>
    </li>
      <Dialog open={deleting} onClose={() => { if (!busy) setDeleting(false) }} title="Delete this to-do?"
        description="This removes the reminder from your private list. Linked candidate, client and order records remain available."
        footer={<><Button disabled={busy} variant="ghost" onClick={() => setDeleting(false)}>Keep item</Button><Button disabled={busy} variant="danger" onClick={() => void perform(async () => { await api.del(`/todos/${item.id}`); setDeleting(false) })}>{busy ? 'Deleting…' : 'Delete item'}</Button></>}>
        <p className="text-sm font-medium">{item.title}</p><p className="mt-2 text-sm text-ink-500">This cannot be undone.</p>
      </Dialog>
    </>
  )
}

function AddDialog({ open, onClose, item }: { open: boolean; onClose: () => void; item?: TodoItem | null }) {
  const store = useStore()
  const todayAt = (() => {
    const d = new Date()
    d.setHours(d.getHours() + 2, 0, 0, 0)
    return format(d, "yyyy-MM-dd'T'HH:mm")
  })()

  const [form, setForm] = useState({ title: item?.title || '', notes: item?.notes || '', dueAt: item ? format(new Date(item.dueAt), "yyyy-MM-dd'T'HH:mm") : todayAt, priority: item?.priority || 'normal', link: item?.linkTo || '' })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const set = (k: string, v: string) => setForm((f) => ({ ...f, [k]: v }))

  const linkOptions = [
    ...store.clients.map((c) => ({ value: `/clients/${c.id}`, label: `Client — ${c.name}` })),
    ...store.orders.map((o) => ({ value: `/orders/${o.id}`, label: `Order — ${o.ref}` })),
    ...store.candidates.map((c) => ({ value: `/candidates/${c.id}`, label: `Candidate — ${c.name}` })),
  ]

  return (
    <Dialog
      open={open}
      onClose={() => { if (!busy) onClose() }}
      title={item ? 'Edit to-do' : 'Add a to-do'}
      description="Only you see this item. You will get a reminder in the notification bell when it is due."
      footer={
        <>
          <Button variant="ghost" disabled={busy} onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant="primary"
            disabled={busy || !form.title.trim() || !form.dueAt}
            onClick={async () => {
              if (busy) return
              const due = new Date(form.dueAt)
              if (!Number.isFinite(due.getTime())) { setError('Choose a valid due date and time.'); return }
              setBusy(true); setError('')
              const chosen = linkOptions.find((o) => o.value === form.link)
              const payload = {
                title: form.title.trim(),
                notes: form.notes.trim() || undefined,
                dueAt: due.toISOString(),
                priority: form.priority,
                linkTo: chosen?.value,
                linkLabel: chosen?.label.split(' — ')[1],
              }
              try {
                if (item) { await api.patch(`/todos/${item.id}`, payload); try { await store.refresh({ keepCurrent: true }) } catch { store.notify('Saved. Refresh to see the latest list.', 'warn') } }
                else await store.addTodo(payload)
                onClose()
              } catch (e) { setError(e instanceof Error ? e.message : 'To-do could not be saved') }
              finally { setBusy(false) }
            }}
          >
            {busy ? 'Saving…' : item ? 'Save changes' : 'Add item'}
          </Button>
        </>
      }
    >
      {error && <p role="alert" className="mb-3 text-sm text-stamp-700">{error}</p>}
      <fieldset disabled={busy} className="grid min-w-0 gap-4 sm:grid-cols-2">
        <Field label="Title" required className="sm:col-span-2">
          <Input
            value={form.title}
            maxLength={300}
            autoFocus
            onChange={(e) => set('title', e.target.value)}
            placeholder="Call the client about the interview slot"
          />
        </Field>
        <Field label="Due date and time">
          <DatePicker withTime required value={form.dueAt} onChange={(value) => set('dueAt', value)} />
        </Field>
        <Field label="Priority">
          <Select value={form.priority} onChange={(e) => set('priority', e.target.value)}>
            <option value="high">High</option>
            <option value="normal">Normal</option>
            <option value="low">Low</option>
          </Select>
        </Field>
        <Field label="Link to a record" hint="Optional" className="sm:col-span-2">
          <Select value={form.link} onChange={(e) => set('link', e.target.value)}>
            <option value="">No link</option>
            {linkOptions.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Notes" className="sm:col-span-2">
          <Textarea rows={2} value={form.notes} onChange={(e) => set('notes', e.target.value)} />
        </Field>
      </fieldset>
    </Dialog>
  )
}
