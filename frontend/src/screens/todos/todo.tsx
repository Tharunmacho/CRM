import { useMemo, useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { addDays, format, nextMonday, setHours, setMinutes } from 'date-fns'
import {
  AlertCircle, CalendarCheck2, CalendarClock, CheckCheck, Check, Clock3, ListChecks, LockKeyhole, Pencil, Plus,
  RefreshCw, Sparkles, TimerOff, Trash2,
} from 'lucide-react'
import { api } from '@/shared/api/client'
import { cn } from '@/shared/lib/cn'
import { isSameDay } from '@/shared/lib/format'
import { useStore } from '@/shared/state/crm-store'
import type { TodoItem } from '@/shared/model/types'
import { Badge, EmptyState, PageHeader, Panel, PanelHeader } from '@/shared/ui/display'
import { SummaryCards } from '@/shared/ui/workspace'
import { Button, Checkbox, Input, Select } from '@/shared/ui/primitives'
import { Dialog, Tabs } from '@/shared/ui/overlay'
import { useDeadlineClock, useWorkspaceRead } from '@/features/staff/use-workspace-read'
import { TodoDialog } from './todo-dialog'
import {
  Flag, LINK_KINDS, LinkIcon, PRIORITIES, dayDate, dayLabel, dueText, groupByDay, lateText, linkIdOf, linkKindOf, untilText,
  type TabKey,
} from './todo-meta'
import '@/styles/todo-v2.css'

const EMPTY: Record<TabKey, { title: string; description: string }> = {
  today: { title: 'Nothing due today', description: 'Type a to-do in the quick add box above, or use Add item to add notes, a priority and a link to a candidate, client or order.' },
  upcoming: { title: 'Nothing scheduled ahead', description: 'Add a follow-up for a candidate, client or order. It will wait here until its day.' },
  overdue: { title: 'Nothing is overdue', description: 'A to-do shows here in red when its time has passed, until you tick it off.' },
  done: { title: 'Nothing ticked off yet', description: 'Finished to-dos stay here. Tick the box on a to-do to finish it.' },
}

const PRIORITY_TONE = { high: 'amber', normal: 'neutral', low: 'green' } as const

export default function TodoPage() {
  const store = useStore()
  const todoRead = useWorkspaceRead()
  const clock = useDeadlineClock()
  const [tab, setTab] = useState<TabKey>('today')
  const [dialog, setDialog] = useState<{ item: TodoItem | null; title: string } | null>(null)

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

  const openItems = useMemo(() => mine.filter((t) => !t.done), [mine])
  const nextUp = useMemo(
    () => [...openItems].filter((t) => +new Date(t.dueAt) >= clock).sort((a, b) => +new Date(a.dueAt) - +new Date(b.dueAt))[0],
    [openItems, clock],
  )
  const byPriority = useMemo(() => ({
    high: openItems.filter((t) => t.priority === 'high').length,
    normal: openItems.filter((t) => t.priority === 'normal').length,
    low: openItems.filter((t) => t.priority === 'low').length,
  }), [openItems])

  const groups = useMemo(() => {
    // Finished work shows newest first; everything else is soonest first.
    const rows = [...buckets[tab]].sort((a, b) => tab === 'done' ? +new Date(b.dueAt) - +new Date(a.dueAt) : +new Date(a.dueAt) - +new Date(b.dueAt))
    return groupByDay(rows)
  }, [buckets, tab])
  const completion = mine.length ? Math.round((buckets.done.length / mine.length) * 100) : 0
  const openDialog = (item: TodoItem | null = null, title = '') => setDialog({ item, title })

  if (todoRead.loading || todoRead.error) return <div className="todo-screen td">
    <PageHeader title="To-do List" description="Plan your day and keep track of your follow-ups." />
    {todoRead.error ? (
      <Panel className="td-state"><div className="td-state-body">
        <AlertCircle size={20} aria-hidden="true" />
        <div>
          <p role="alert" className="td-state-title">We could not load your to-do list</p>
          <p className="td-state-text">{todoRead.error}</p>
        </div>
        <Button icon={<RefreshCw />} onClick={todoRead.refresh}>Try again</Button>
      </div></Panel>
    ) : (
      <div className="td-skeleton" role="status" aria-label="Loading your to-do list">
        <div className="td-skel-tiles">{[0, 1, 2, 3].map((i) => <span key={i} />)}</div>
        <div className="td-skel-body"><span /><span /></div>
      </div>
    )}
  </div>

  return (
    <div className="todo-screen td">
      <PageHeader
        title="To-do List"
        description="Plan your day and keep track of your follow-ups."
        actions={
          <>
            <Button icon={<RefreshCw />} onClick={todoRead.refresh}>Refresh list</Button>
            <Button variant="primary" icon={<Plus />} onClick={() => openDialog()}>Add item</Button>
          </>
        }
      />

      <SummaryCards
        items={[
          { label: 'Today', value: buckets.today.length, detail: 'To do before the day ends', icon: CalendarCheck2, onSelect: () => setTab('today'), active: tab === 'today' },
          { label: 'Upcoming', value: buckets.upcoming.length, detail: 'Planned for the days ahead', icon: CalendarClock, onSelect: () => setTab('upcoming'), active: tab === 'upcoming' },
          { label: 'Overdue', value: buckets.overdue.length, detail: buckets.overdue.length ? 'Needs your attention now' : 'Nothing is running late', icon: TimerOff, tone: 'red', onSelect: () => setTab('overdue'), active: tab === 'overdue' },
          { label: 'Completed', value: buckets.done.length, detail: 'Ticked off', icon: CheckCheck, tone: 'green', onSelect: () => setTab('done'), active: tab === 'done' },
        ]}
      />

      <div className="td-layout">
        <Panel flush className="td-list-panel">
          <PanelHeader
            title="My to-dos"
            meta="Your reminders, grouped by day"
            action={<span className="td-private"><LockKeyhole size={13} aria-hidden="true" /> Private to you</span>}
          />
          <Tabs
            tabs={[
              { key: 'today', label: 'Today' },
              { key: 'upcoming', label: 'Upcoming' },
              { key: 'overdue', label: 'Overdue' },
              { key: 'done', label: 'Done' },
            ]}
            active={tab}
            onChange={(k) => setTab(k as TabKey)}
          />

          {tab !== 'done' && <QuickAdd key={tab} tab={tab} now={clock} onMore={(title) => openDialog(null, title)} />}

          {groups.length === 0 ? (
            <div className="td-empty">
              <EmptyState
                icon={<CalendarCheck2 size={22} strokeWidth={1.6} />}
                title={EMPTY[tab].title}
                description={EMPTY[tab].description}
                action={tab !== 'done' && <Button variant="primary" icon={<Plus />} onClick={() => openDialog()}>Add item</Button>}
              />
            </div>
          ) : (
            <div className="td-scroll">
              <ol className="td-timeline" aria-label={`${tab} to-dos`}>
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
          )}
        </Panel>

        <Panel flush className="td-progress-panel">
          <PanelHeader title="Your progress" meta="What you have finished and what is left" />
          <div className="td-progress-body">
            <div className="td-ring-wrap">
              <div className="td-ring" role="img" aria-label={`${completion}% of your to-dos completed`}>
                <svg viewBox="0 0 120 120" aria-hidden="true">
                  <circle className="td-ring-track" cx="60" cy="60" r="52" />
                  {completion > 0 && <circle className="td-ring-fill" cx="60" cy="60" r="52" pathLength={100} strokeDasharray={`${completion} 100`} />}
                </svg>
                <div>
                  <strong className="tnum">{completion}%</strong>
                  <span>completed</span>
                </div>
              </div>
            </div>

            <dl className="td-figures">
              <div><dt>Completed</dt><dd className="tnum">{buckets.done.length}</dd></div>
              <div><dt>Still on your list</dt><dd className="tnum">{openItems.length}</dd></div>
            </dl>

            <section className="td-block" aria-label="Open items by priority">
              <h3>Open by priority</h3>
              {PRIORITIES.map((p) => (
                <div key={p.value} className="td-prio-row">
                  <span>{p.label}</span>
                  <span className="td-prio-bar"><i className={`is-${p.value}`} style={{ width: openItems.length ? `${(byPriority[p.value] / openItems.length) * 100}%` : '0%' }} /></span>
                  <b className="tnum">{byPriority[p.value]}</b>
                </div>
              ))}
            </section>

            <section className="td-block td-next" aria-label="Next up">
              <h3><Sparkles size={13} aria-hidden="true" /> Next up</h3>
              {nextUp ? (
                <p><strong title={nextUp.title}>{nextUp.title}</strong><span>{dueText(nextUp, clock)}</span></p>
              ) : (
                <p><strong>All clear</strong><span>Nothing coming up on your list.</span></p>
              )}
            </section>

            <p className="td-privacy-note">
              <LockKeyhole size={15} aria-hidden="true" />
              <span>Only you can see this list. Team tasks are on the Tasks page.</span>
            </p>
          </div>
        </Panel>
      </div>

      {dialog && <TodoDialog key={dialog.item?.id || 'new'} item={dialog.item} initialTitle={dialog.title} onClose={() => setDialog(null)} />}
    </div>
  )
}

/* ------------------------------------------------------------- quick add */

type Preset = 'soon' | 'tomorrow' | 'week'

function presetDue(preset: Preset, now: number) {
  const base = new Date(now)
  if (preset === 'tomorrow') return setMinutes(setHours(addDays(base, 1), 9), 0)
  if (preset === 'week') return setMinutes(setHours(nextMonday(base), 9), 0)
  const soon = new Date(now)
  soon.setHours(soon.getHours() + 2, 0, 0, 0)
  // Keep "today" on today: late in the evening it lands at the last minutes of the day.
  if (!isSameDay(soon, base)) return new Date(base.getFullYear(), base.getMonth(), base.getDate(), 23, 59)
  return soon
}

function QuickAdd({ tab, now, onMore }: { tab: TabKey; now: number; onMore: (title: string) => void }) {
  const store = useStore()
  const [title, setTitle] = useState('')
  const [preset, setPreset] = useState<Preset>(tab === 'upcoming' ? 'tomorrow' : 'soon')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const label = (p: Preset) => {
    const due = presetDue(p, now)
    return `${p === 'soon' ? 'Today' : p === 'tomorrow' ? 'Tomorrow' : `Mon ${format(due, 'd MMM')}`}, ${format(due, 'HH:mm')}`
  }

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (busy) return
    if (!title.trim()) { setError('Type what needs doing, then press Enter.'); return }
    setBusy(true); setError('')
    try {
      await store.addTodo({ title: title.trim(), dueAt: presetDue(preset, now).toISOString(), priority: 'normal', linkTo: null, linkLabel: null })
      setTitle('')
    } catch (e) { setError(e instanceof Error ? e.message : 'We could not add the to-do. Please try again.') }
    finally { setBusy(false) }
  }

  return (
    <form className="td-quick" onSubmit={(e) => void submit(e)} noValidate>
      <div className="td-quick-row">
        <div className="td-quick-input">
          <ListChecks size={16} aria-hidden="true" />
          <Input
            value={title}
            maxLength={300}
            aria-label="Quick add a to-do"
            aria-invalid={!!error}
            aria-describedby={error ? 'td-quick-error' : undefined}
            placeholder="Quick add: what needs doing?"
            disabled={busy}
            onChange={(e) => { setTitle(e.target.value); if (error) setError('') }}
          />
        </div>
        <div className="td-quick-when">
          <Select value={preset} aria-label="Due" onChange={(e) => setPreset(e.target.value as Preset)} disabled={busy}>
            <option value="soon">{label('soon')}</option>
            <option value="tomorrow">{label('tomorrow')}</option>
            <option value="week">{label('week')}</option>
          </Select>
        </div>
        <Button type="submit" variant="primary" icon={<Plus />} disabled={busy}>{busy ? 'Adding…' : 'Add'}</Button>
        <Button type="button" disabled={busy} onClick={() => onMore(title.trim())}>More options</Button>
      </div>
      {error && <p id="td-quick-error" role="alert" className="td-quick-error">{error}</p>}
    </form>
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
