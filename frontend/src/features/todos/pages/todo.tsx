import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { format } from 'date-fns'
import { CalendarCheck2, CalendarClock, CheckCheck, LockKeyhole, Plus, TimerOff, Trash2 } from 'lucide-react'
import { cn } from '@/shared/lib/cn'
import { dateTimeLabel, isSameDay, smartTime } from '@/shared/lib/format'
import { useStore } from '@/shared/state/crm-store'
import type { Priority, TodoItem } from '@/shared/model/types'
import { Badge, EmptyState, PageHeader, Panel, PanelHeader } from '@/shared/ui/display'
import { SummaryCards } from '@/shared/ui/workspace'
import { Button, Checkbox, Field, Input, Select, Textarea } from '@/shared/ui/primitives'
import { Dialog, Tabs } from '@/shared/ui/overlay'
import { DatePicker } from '@/shared/ui/date-picker'

type TabKey = 'today' | 'upcoming' | 'overdue' | 'done'

const PRIORITY_TONE: Record<Priority, 'stamp' | 'neutral' | 'navy'> = {
  high: 'stamp',
  normal: 'neutral',
  low: 'navy',
}

export default function TodoPage() {
  const store = useStore()
  const [tab, setTab] = useState<TabKey>('today')
  const [adding, setAdding] = useState(false)

  const mine = useMemo(
    () => store.todos.filter((t) => t.ownerId === store.me.id),
    [store.todos, store.me.id],
  )

  const buckets = useMemo(() => {
    const now = new Date()
    const open = mine.filter((t) => !t.done)
    return {
      today: open.filter((t) => isSameDay(t.dueAt, now) && new Date(t.dueAt) >= now),
      upcoming: open.filter((t) => new Date(t.dueAt) > now && !isSameDay(t.dueAt, now)),
      overdue: open.filter((t) => new Date(t.dueAt) < now),
      done: mine.filter((t) => t.done),
    }
  }, [mine])

  const rows = [...buckets[tab]].sort((a, b) => +new Date(a.dueAt) - +new Date(b.dueAt))
  const completion = mine.length ? Math.round((buckets.done.length / mine.length) * 100) : 0

  return (
    <>
      <PageHeader
        title="To-do List"
        eyebrow="Your personal workspace"
        description="A little more organised, a lot more focused. Keep your own reminders and follow-ups on track."
        actions={
          <Button variant="primary" icon={<Plus className="h-4 w-4" />} onClick={() => setAdding(true)}>
            Add item
          </Button>
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
          <PanelHeader title="My to-dos" meta="Make room for what matters next" action={<Badge tone="navy"><LockKeyhole size={12} /> Only you</Badge>} />
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
                <Row key={t.id} item={t} />
              ))}
            </ul>
          )}
        </Panel>
        <Panel className="todo-progress-panel">
          <span className="workspace-eyebrow">Your progress</span>
          <h2>A clearer day ahead</h2>
          <p>Small steps add up. Here is where your personal list stands.</p>
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

      <AddDialog open={adding} onClose={() => setAdding(false)} />
    </>
  )
}

function Row({ item }: { item: TodoItem }) {
  const store = useStore()
  const overdue = !item.done && new Date(item.dueAt) < new Date()

  return (
    <li
      className={cn(
        'workspace-todo-row flex items-start gap-3 border-b border-rule-soft px-4 py-3 last:border-0',
        overdue && 'bg-stamp-50/40',
      )}
    >
      <div className="pt-0.5">
        <Checkbox
          aria-label={`Complete ${item.title}`}
          checked={item.done}
          onChange={() => void store.toggleTodo(item.id)}
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
        onClick={() => void store.deleteTodo(item.id)}
        aria-label={`Delete ${item.title}`}
        className="shrink-0 rounded-control p-1.5 text-ink-300 hover:bg-stamp-50 hover:text-stamp-600"
      >
        <Trash2 className="h-3.5 w-3.5" />
      </button>
    </li>
  )
}

function AddDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const store = useStore()
  const todayAt = (() => {
    const d = new Date()
    d.setHours(d.getHours() + 2, 0, 0, 0)
    return format(d, "yyyy-MM-dd'T'HH:mm")
  })()

  const [form, setForm] = useState({ title: '', notes: '', dueAt: todayAt, priority: 'normal', link: '' })
  const set = (k: string, v: string) => setForm((f) => ({ ...f, [k]: v }))

  const linkOptions = [
    ...store.clients.map((c) => ({ value: `/clients/${c.id}`, label: `Client — ${c.name}` })),
    ...store.orders.slice(0, 10).map((o) => ({ value: `/orders/${o.id}`, label: `Order — ${o.ref}` })),
  ]

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Add a to-do"
      description="Only you see this item. You will get a reminder in the notification bell when it is due."
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant="primary"
            disabled={!form.title.trim()}
            onClick={() => {
              const chosen = linkOptions.find((o) => o.value === form.link)
              void store.addTodo({
                title: form.title.trim(),
                notes: form.notes.trim() || undefined,
                dueAt: new Date(form.dueAt).toISOString(),
                priority: form.priority,
                linkTo: chosen?.value,
                linkLabel: chosen?.label.split(' — ')[1],
              })
              setForm({ title: '', notes: '', dueAt: todayAt, priority: 'normal', link: '' })
              onClose()
            }}
          >
            Add item
          </Button>
        </>
      }
    >
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Title" required className="sm:col-span-2">
          <Input
            value={form.title}
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
      </div>
    </Dialog>
  )
}
