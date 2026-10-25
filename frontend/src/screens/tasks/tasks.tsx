import { Fragment, useEffect, useMemo, useState, type KeyboardEvent } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { ArrowRight, CalendarClock, CheckCheck, ClipboardList, MessageSquare, MessageSquarePlus, MessageSquareOff, RefreshCw, ShieldAlert, Sparkles, TimerOff, TriangleAlert } from 'lucide-react'
import { cn } from '@/shared/lib/cn'
import { api } from '@/shared/api/client'
import { agencyDay, taskClockStart } from '@/shared/lib/task-time'
import { format } from 'date-fns'
import { dateTimeLabel, slaState, smartTime } from '@/shared/lib/format'
import { useStore } from '@/shared/state/crm-store'
import { parseStageNumber, stageByNo } from '@/shared/workflow/stages'
import type { Task } from '@/shared/model/types'
import { EmptyState, PageHeader } from '@/shared/ui/display'
import { Button, Field, SearchInput, Select, Textarea, Input } from '@/shared/ui/primitives'
import { Dialog, Tabs } from '@/shared/ui/overlay'
import { StageModal } from '@/features/workflow/components/stage-modal'
import { RecordIdentity, SummaryCards } from '@/shared/ui/workspace'
import { ReassignCandidatesDialog } from '@/features/staff/reassign-candidates-dialog'
import { completedInPeriod, taskViewFromParams, validDateFilter, type TaskView } from '@/features/staff/task-filters'
import { useDeadlineClock, useWorkspaceRead } from '@/features/staff/use-workspace-read'
import { ClockCell, RemarkPanel, clockOf } from './task-parts'
import { Flag, OwnerTag, StageTrack } from './task-identity'
import '@/styles/tasks-page.css'

type TabKey = TaskView
type SortKey = 'due' | 'newest' | 'candidate' | 'owner'

const PAGE_SIZE = 20
const COLUMNS = [
  { key: 'candidate', header: 'Candidate', width: 17 },
  { key: 'order', header: 'Order', width: 13 },
  { key: 'stage', header: 'Stage', width: 12 },
  { key: 'owner', header: 'Assigned to', width: 9 },
  { key: 'created', header: 'Created', width: 7 },
  { key: 'due', header: 'Due', width: 7 },
  { key: 'remaining', header: 'Time left', width: 12 },
  { key: 'update', header: 'Daily update', width: 14 },
  { key: 'actions', header: 'Actions', width: 9 },
] as const

/** Two lines: the day and the time, so the date columns stay narrow. */
function Stamp({ value, empty }: { value?: string | Date; empty?: boolean }) {
  if (!value || empty) return <span className="tk-muted">—</span>
  const date = new Date(value)
  return <span className="tk-stamp" title={dateTimeLabel(date)}><b>{format(date, 'd MMM')}</b><small>{format(date, 'HH:mm')}</small></span>
}

export default function TasksPage() {
  const store = useStore()
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()
  const stageFilter = parseStageNumber(params.get('stage'))

  const [openApp, setOpenApp] = useState<string | null>(null)
  const [openStage, setOpenStage] = useState<number | undefined>()
  const [expanded, setExpanded] = useState<string | null>(null)
  const [resetting, setResetting] = useState<Task | null>(null)
  const [reassigning, setReassigning] = useState<Task | null>(null)
  const [query, setQuery] = useState('')
  const [sort, setSort] = useState<SortKey>('due')
  const [shown, setShown] = useState(PAGE_SIZE)
  const taskRead = useWorkspaceRead(params.toString())
  const clock = useDeadlineClock()
  const taskReadReady = !taskRead.loading && !taskRead.error
  const taskReadError = taskRead.error

  const isSupervisor = store.can('all_reports')
  const notificationTask = store.visibleTasks.find(task => task.id === params.get('task'))
  const tab = taskViewFromParams(params, isSupervisor, notificationTask)
  const staffFilter = isSupervisor ? params.get('staff') || 'all' : 'all'
  const completedFrom = validDateFilter(params.get('from'))
  const completedTo = validDateFilter(params.get('to'))
  const historyDay = isSupervisor ? params.get('date') : null
  const [historicalIds, setHistoricalIds] = useState<string[]>([])
  const [historyError, setHistoryError] = useState('')
  const [historyLoading, setHistoryLoading] = useState(false)
  const updateFilter = (name: string, value: string) => setParams(previous => {
    const next = new URLSearchParams(previous)
    if (value && value !== 'all') next.set(name, value)
    else next.delete(name)
    return next
  })
  const changeTab = (view: string) => setParams(previous => {
    const next = new URLSearchParams(previous)
    next.set('view', view)
    if (view !== 'history') next.delete('date')
    return next
  })
  useEffect(() => {
    if (!historyDay) return
    const controller = new AbortController()
    setHistoryLoading(true); setHistoryError(''); setHistoricalIds([])
    const query = new URLSearchParams({ day: historyDay, staff: staffFilter })
    void api.get<{ taskIds: string[] }>(`/tasks/missed-updates?${query}`, { signal: controller.signal })
      .then((data) => { if (!controller.signal.aborted) setHistoricalIds(data.taskIds) })
      .catch((error) => { if (!controller.signal.aborted) setHistoryError(error.message) })
      .finally(() => { if (!controller.signal.aborted) setHistoryLoading(false) })
    return () => controller.abort()
  }, [historyDay, staffFilter, taskRead.revision])

  const scope = useMemo(() => {
    let list = store.visibleTasks
    if (!historyDay && isSupervisor && staffFilter !== 'all') list = list.filter((t) => t.ownerId === staffFilter)
    if (stageFilter !== null) list = list.filter((t) => t.stage === stageFilter)
    if (params.get('task')) list = list.filter((t) => t.id === params.get('task'))
    if (historyDay) return list.filter((t) => historicalIds.includes(t.id))
    return list.filter((t) => !t.cancelledAt)
  }, [store.visibleTasks, staffFilter, stageFilter, isSupervisor, params, historyDay, historicalIds])

  const buckets = useMemo(() => {
    const open = scope.filter((t) => !t.completedAt && !t.cancelledAt)
    const now = new Date(clock)
    return {
      open,
      history: scope,
      due: open.filter((t) => {
        const s = slaState(taskClockStart(t), now)
        return !s.overdue && agencyDay(s.dueAt, store.agencyTimezone) === agencyDay(now, store.agencyTimezone)
      }),
      overdue: open.filter((t) => slaState(taskClockStart(t), now).overdue),
      escalated: open.filter((t) => t.escalated),
      initial: open.filter((t) => t.kind === 'initial_call'),
      missing: open.filter((t) => !store.updatedToday(t.id)),
      completed: scope.filter((t) => completedInPeriod(t.completedAt, completedFrom, completedTo, store.agencyTimezone)),
    }
  }, [scope, completedFrom, completedTo, store.agencyTimezone, store.updatedToday, clock])

  const orderOf = (t: Task) => {
    const app = store.applications.find((a) => a.id === t.applicationId)
    return app ? store.order(app.orderId) ?? null : null
  }
  const term = query.trim().toLowerCase()
  const matches = (task: Task) => {
    if (!term) return true
    const order = orderOf(task)
    return [task.title, store.candidate(task.candidateId)?.name, order ? store.orderLabel(order.id) : '', store.user(task.ownerId)?.name]
      .some((value) => value?.toLowerCase().includes(term))
  }
  const dueMs = (t: Task) => +new Date(taskClockStart(t)) + 48 * 3600_000
  const rows = buckets[tab].filter(matches).sort((a, b) => {
    if (tab === 'completed') return +new Date(b.completedAt!) - +new Date(a.completedAt!)
    if (sort === 'newest') return +new Date(b.createdAt) - +new Date(a.createdAt)
    if (sort === 'candidate') return (store.candidate(a.candidateId)?.name ?? '').localeCompare(store.candidate(b.candidateId)?.name ?? '')
    if (sort === 'owner') return (store.user(a.ownerId)?.name ?? '').localeCompare(store.user(b.ownerId)?.name ?? '') || dueMs(a) - dueMs(b)
    return dueMs(a) - dueMs(b)
  })
  useEffect(() => { setShown(PAGE_SIZE) }, [tab, term, staffFilter, sort, stageFilter])

  const tabs = [
    ...(historyDay ? [{ key: 'history', label: 'No update on this date' }] : []),
    { key: 'open', label: 'Open' },
    { key: 'due', label: 'Due today' },
    { key: 'overdue', label: 'Overdue' },
    ...(isSupervisor ? [{ key: 'escalated', label: 'Escalated' }] : []),
    { key: 'completed', label: 'Completed' },
  ]
  const notUpdated = buckets.open.filter((t) => !store.updatedToday(t.id)).length
  const canReassign = store.can('reassign')
  const canReset = store.me.role === 'admin'

  // How many closed tasks were finished inside their 48 hours (real completion times only).
  const closed = scope.filter((t) => t.completedAt)
  const onTime = closed.filter((t) => +new Date(t.completedAt!) <= dueMs(t)).length
  const onTimeRate = closed.length ? Math.round((onTime / closed.length) * 100) : null

  if (!taskReadReady || (historyDay && (historyLoading || historyError))) return <div className="tk">
    <PageHeader title="Tasks" description="Each stage a candidate reaches becomes a task with a 48-hour limit." />
    <section className="tk-panel tk-state" aria-busy={!taskReadError && !historyError}>
      {taskReadError || historyError ? <>
        <TriangleAlert size={22} aria-hidden="true" />
        <p role="alert" className="tk-state-title">{taskReadError || historyError}</p>
        <p>We could not load your tasks, so the counts are hidden. Check your internet and try again.</p>
        <Button variant="primary" onClick={taskRead.refresh}>Try again</Button>
      </> : <>
        <div className="tk-skeleton" aria-hidden="true"><i /><i /><i /><i /></div>
        <p role="status">Loading your tasks…</p>
      </>}
    </section>
  </div>

  const tiles = [
    { key: 'open', label: 'Open tasks', value: buckets.open.length, detail: 'Waiting for the next step', icon: ClipboardList, tone: 'blue' as const },
    { key: 'due', label: 'Due today', value: buckets.due.length, detail: 'Time runs out today', icon: CalendarClock, tone: 'amber' as const },
    { key: 'overdue', label: 'Overdue', value: buckets.overdue.length, detail: 'Past 48 hours', icon: TimerOff, tone: 'red' as const },
    ...(isSupervisor ? [{ key: 'escalated', label: 'Escalated', value: buckets.escalated.length, detail: 'Reported to admin and managers', icon: ShieldAlert, tone: 'red' as const }] : []),
    { key: 'missing', label: 'Not updated today', value: notUpdated, detail: 'No remark added yet', icon: MessageSquareOff, tone: 'amber' as const },
    { key: 'completed', label: 'Completed', value: buckets.completed.length, detail: completedFrom || completedTo ? 'In the selected period' : 'Stage finished', icon: CheckCheck, tone: 'green' as const },
  ]

  const openTask = (t: Task) => { if (t.applicationId) { setOpenStage(t.stage || undefined); setOpenApp(t.applicationId) } else navigate(`/candidates/${t.candidateId}`) }
  const onRowKey = (event: KeyboardEvent<HTMLElement>, t: Task) => {
    if (event.target !== event.currentTarget) return
    if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); openTask(t) }
  }
  const now = new Date(clock)
  const visible = rows.slice(0, shown)
  const nextUp = [...buckets.open].sort((a, b) => dueMs(a) - dueMs(b))[0]
  const nextName = nextUp ? store.candidate(nextUp.candidateId)?.name ?? nextUp.title : ''
  const quickViews = [
    { key: 'initial', label: 'Initial calls' },
    { key: 'missing', label: 'Not updated today' },
  ]
  const view = viewInfo(tab, historyDay)

  return (
    <div className="tk">
      <PageHeader
        title="Tasks"
        description="Each time a candidate moves to a new stage, a task is created with a 48-hour limit. The task closes when that stage is finished. Add one remark a day to every open task."
        actions={<>
          <Button variant="primary" disabled={!nextUp} onClick={() => nextUp && openTask(nextUp)} title={nextUp ? `Open the task closest to its deadline: ${nextName}` : 'No open tasks'}>
            <Sparkles size={15} aria-hidden="true" />Open next task<ArrowRight size={15} aria-hidden="true" />
          </Button>
          <Button onClick={taskRead.refresh}><RefreshCw size={15} aria-hidden="true" />Refresh</Button>
        </>}
      />

      <SummaryCards items={tiles.map(({ key, label, value, detail, icon, tone }) => ({ label, value, detail, icon, tone, active: tab === key, onSelect: () => changeTab(key) }))} />

      <div className={cn('tk-strips', store.me.role === 'admin' && 'has-two')}>
        <div className="tk-meter">
          <span className="tk-meter-icon"><CheckCheck size={18} aria-hidden="true" /></span>
          <div className="tk-meter-body">
            <p>On-time completion</p>
            {onTimeRate === null
              ? <small>Shows after tasks are finished. A task is on time if its stage is finished within 48 hours.</small>
              : <small><b className="tnum">{onTimeRate}%</b> of {closed.length} finished {closed.length === 1 ? 'task was' : 'tasks were'} done within 48 hours</small>}
            <span className="tk-meter-track" role="img" aria-label={onTimeRate === null ? 'No completed tasks yet' : `${onTimeRate} percent completed on time`}>
              <i className={cn(onTimeRate !== null && onTimeRate < 50 && 'is-low', onTimeRate !== null && onTimeRate >= 50 && onTimeRate < 80 && 'is-mid')} style={{ width: `${onTimeRate ?? 0}%` }} />
            </span>
          </div>
        </div>
        {store.me.role === 'admin' && <AutomationSettings />}
      </div>

      <div className="tk-notices">
        {historyDay && <p className="is-warn">Tasks with no daily update on {historyDay}, for the recruiter in charge that day. <button onClick={() => navigate('/tasks')}>Show current tasks</button></p>}
        {params.get('task') && notificationTask && <p>Showing the task from your notification. <button onClick={() => navigate('/tasks')}>Show all tasks</button></p>}
        {notificationTask?.completedAt && <p>Completed {dateTimeLabel(notificationTask.completedAt)}. {tab !== 'completed' && <button onClick={() => setParams(previous => { const next = new URLSearchParams(previous); next.set('view', 'completed'); next.delete('from'); next.delete('to'); next.delete('date'); return next })}>View completed task</button>}</p>}
        {notificationTask?.cancelledAt && <p>Cancelled {dateTimeLabel(notificationTask.cancelledAt)}. <button onClick={() => navigate(`/candidates/${notificationTask.candidateId}`)}>View candidate history</button></p>}
        {params.get('task') && !notificationTask && <p role="status">You can no longer see this task.</p>}
        {stageFilter !== null && <p>Stage {stageFilter}: {stageByNo(stageFilter).title}. <button onClick={() => navigate('/tasks')}>Clear</button></p>}
        {notUpdated > 0 && tab !== 'completed' && tab !== 'history' && tab !== 'missing' && <p className="is-warn"><TriangleAlert size={15} aria-hidden="true" />{notUpdated} open {notUpdated === 1 ? 'task has' : 'tasks have'} no remark today. Each open task needs a daily update. <button onClick={() => changeTab('missing')}>Show them</button></p>}
      </div>

      <section className="tk-panel">
        <div className="tk-panel-head">
          <div className="tk-panel-title">
            <h2>{view.title}</h2>
            <p>{view.description}</p>
          </div>
          <SearchInput value={query} onChange={setQuery} placeholder="Search candidate, order or task" className="tk-search" />
        </div>
        <Tabs tabs={tabs} active={tab} onChange={changeTab} />
        <div className="tk-toolbar">
          {isSupervisor && (
            <Select value={staffFilter} onChange={(e) => updateFilter('staff', e.target.value)} className="tk-select" aria-label="Filter by staff">
              <option value="all">All staff</option>
              {store.users
                .filter((u) => u.role !== 'admin' || u.id === staffFilter)
                .map((u) => <option key={u.id} value={u.id}>{u.name}{!u.active ? ' (inactive)' : ''}</option>)}
            </Select>
          )}
          {tab !== 'completed' && (
            <Select value={sort} onChange={(e) => setSort(e.target.value as SortKey)} className="tk-select" aria-label="Sort tasks">
              <option value="due">Due soonest</option>
              <option value="newest">Newest first</option>
              <option value="candidate">Candidate A-Z</option>
              <option value="owner">Assigned to</option>
            </Select>
          )}
          {tab !== 'completed' && <div className="tk-chips" role="group" aria-label="Quick views">
            {quickViews.map((item) => (
              <button key={item.key} type="button" className={cn('tk-chip', tab === item.key && 'is-active')} aria-pressed={tab === item.key} onClick={() => changeTab(tab === item.key ? 'open' : item.key)}>
                {item.label}
              </button>
            ))}
          </div>}
          {tab === 'completed' && <>
            <label className="tk-date">From<Input type="date" value={completedFrom} max={completedTo || undefined} onChange={event => updateFilter('from', event.target.value)} aria-label="Completed from" /></label>
            <label className="tk-date">To<Input type="date" value={completedTo} min={completedFrom || undefined} onChange={event => updateFilter('to', event.target.value)} aria-label="Completed to" /></label>
          </>}
          <span className="tk-count" role="status">{rows.length} {rows.length === 1 ? 'task' : 'tasks'}</span>
        </div>

        {rows.length === 0 ? (
          <EmptyState title={term ? 'No matching tasks' : emptyTitle(tab)} description={term ? 'Try a different search.' : emptyBody(tab)} />
        ) : (
          <div className="tk-table-wrap">
            <table className="tk-table" aria-label="Tasks">
              <colgroup>{COLUMNS.map((c) => <col key={c.key} style={{ width: `${c.width}%` }} />)}</colgroup>
              <thead>
                <tr>{COLUMNS.map((c) => <th key={c.key} scope="col" className={cn(c.key === 'actions' && 'is-actions')}><span>{c.header}</span></th>)}</tr>
              </thead>
              <tbody>
                {visible.map((t) => {
                  const clockState = clockOf(t, now)
                  const open = !t.completedAt && !t.cancelledAt
                  const dueAt = new Date(dueMs(t))
                  const remarks = store.remarksFor(t.id)
                  const updated = store.updatedToday(t.id)
                  const isOpen = expanded === t.id
                  const order = orderOf(t)
                  const candidateName = store.candidate(t.candidateId)?.name ?? t.title
                  const owner = store.user(t.ownerId)
                  return (
                    <Fragment key={t.id}>
                      <tr
                        className={cn('tk-row', `is-${clockState.tone}`, isOpen && 'is-expanded')}
                        tabIndex={0}
                        onClick={() => openTask(t)}
                        onKeyDown={(event) => onRowKey(event, t)}
                        aria-label={`${candidateName}, ${t.title}`}
                      >
                        <td className="tk-cell-candidate" data-label="">
                          <RecordIdentity name={candidateName} detail={t.title} />
                        </td>
                        <td data-label="Order">
                          {order
                            ? <span className="tk-order" title={store.orderLabel(order.id)}>
                                <Flag country={order.country} />
                                <span className="tk-order-text"><b>{store.designation(order.designationId)?.title ?? 'Job'}</b><small>{store.company(order.companyId)?.name ?? 'Company'}</small></span>
                              </span>
                            : <span className="tk-muted">Not matched to an order</span>}
                        </td>
                        <td data-label="Stage"><StageTrack no={t.stage} /></td>
                        <td data-label="Assigned to">
                          <OwnerTag name={owner?.name} />
                          {open && canReassign && (t.escalated || clockState.tone === 'over') && <button type="button" className="tk-link" onClick={(event) => { event.stopPropagation(); setReassigning(t) }}>Reassign</button>}
                        </td>
                        <td data-label="Created"><Stamp value={t.createdAt} /></td>
                        <td data-label="Due"><Stamp value={dueAt} empty={!!t.cancelledAt} /></td>
                        <td data-label="Time left"><ClockCell clock={clockState} /></td>
                        <td data-label="Daily update">
                          {open ? (
                            <span className={cn('tk-pill', updated ? 'is-ok' : 'is-warn')}>{updated ? 'Updated today' : 'Not updated today'}</span>
                          ) : <span className="tk-pill is-muted">{t.completedAt ? 'Closed' : 'Cancelled'}</span>}
                          {remarks.length > 0 && <small className="tk-sub">{remarks.length} {remarks.length === 1 ? 'remark' : 'remarks'}</small>}
                                                  </td>
                        <td className="tk-cell-actions" data-label="">
                          <span className="tk-acts">
                            <button
                              type="button"
                              className={cn('tk-act', isOpen && 'is-on')}
                              aria-expanded={isOpen}
                              aria-label={`${open ? 'Add remark for' : 'Remarks for'} ${candidateName}, ${remarks.length} so far`}
                              title={`${open ? 'Add a daily remark' : 'View remarks'} (${remarks.length} so far)`}
                              onClick={(event) => { event.stopPropagation(); setExpanded(isOpen ? null : t.id) }}
                            >
                              {open ? <MessageSquarePlus size={16} aria-hidden="true" /> : <MessageSquare size={16} aria-hidden="true" />}
                              <span className="tk-act-label">{open ? 'Add remark' : 'Remarks'}</span>
                            </button>
                            <button
                              type="button"
                              className="tk-act"
                              aria-label={t.applicationId ? `Open stage for ${candidateName}` : `Open ${candidateName}`}
                              title={t.applicationId ? 'Open this stage' : 'Open candidate'}
                              onClick={(event) => { event.stopPropagation(); openTask(t) }}
                            >
                              <ArrowRight size={16} aria-hidden="true" />
                              <span className="tk-act-label">Open</span>
                            </button>
                          </span>
                        </td>
                      </tr>
                      {isOpen && (
                        <tr className="tk-expand-row">
                          <td colSpan={COLUMNS.length}>
                            <RemarkPanel
                              task={t}
                              onReassign={open && canReassign ? () => setReassigning(t) : undefined}
                              onReset={open && canReset ? () => setResetting(t) : undefined}
                            />
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
        {rows.length > 0 && (
          <div className="tk-foot">
            <span className="tnum" aria-live="polite">Showing {Math.min(shown, rows.length)} of {rows.length}</span>
            {rows.length > shown && <Button size="sm" onClick={() => setShown((n) => n + PAGE_SIZE)}>Show more ({rows.length - shown} left)</Button>}
          </div>
        )}
      </section>

      {resetting && <ResetDeadline task={resetting} onClose={() => setResetting(null)} />}
      {reassigning && <ReassignCandidatesDialog key={reassigning.candidateId} candidateIds={[reassigning.candidateId]} onClose={() => setReassigning(null)} description={`${store.candidate(reassigning.candidateId)?.name || 'Candidate'} · ${reassigning.title}`} />}
      <StageModal applicationId={openApp} open={!!openApp} focusStage={openStage} onClose={() => setOpenApp(null)} />
    </div>
  )
}

function viewInfo(tab: TabKey, historyDay: string | null) {
  return {
    history: { title: 'Tasks with no daily update', description: historyDay ? `Open tasks with no remark on ${historyDay}.` : 'Open tasks with no remark on the chosen day.' },
    open: { title: 'Open tasks', description: 'All tasks waiting for the next step. Closest deadline first.' },
    due: { title: 'Due today', description: 'Tasks whose 48 hours run out today.' },
    overdue: { title: 'Overdue', description: 'Tasks past their 48-hour limit. Give them to someone else or finish the stage.' },
    escalated: { title: 'Escalated', description: 'Overdue tasks that were reported to the admin and managers.' },
    initial: { title: 'Initial calls', description: 'First calls waiting for a result before a stage starts.' },
    missing: { title: 'Not updated today', description: 'Open tasks that still need today\'s remark.' },
    completed: { title: 'Completed', description: 'Tasks closed by finishing their stage. Newest first.' },
  }[tab]
}

function emptyTitle(tab: TabKey) {
  return {
    history: 'No missing updates for this date',
    open: 'No open tasks',
    due: 'Nothing due today',
    overdue: 'Nothing is overdue',
    escalated: 'No escalations',
    initial: 'No open initial calls',
    missing: 'Every open task has an update today',
    completed: 'No completed tasks yet',
  }[tab]
}

function emptyBody(tab: TabKey) {
  return {
    history: 'No task was missing a daily update on this date.',
    open: 'Tasks show here as candidates are assigned and move to new stages.',
    due: 'Every open task still has time left.',
    overdue: 'Everything is on time. Keep adding daily remarks.',
    escalated: 'No task has been reported to the admin and managers.',
    initial: 'Initial call tasks show here when a candidate is assigned. They close when the call result is saved.',
    missing: 'All open tasks here have a remark today.',
    completed: 'A task closes when its stage is completed.',
  }[tab]
}

function AutomationSettings() {
  const store = useStore()
  const [settings, setSettings] = useState<{ escalationRepeatHours: 0 | 24; lastRunAt?: string } | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    const controller = new AbortController()
    void api.get<typeof settings>('/workflow/settings', { signal: controller.signal }).then(setSettings).catch((e) => { if (!controller.signal.aborted) setError(e.message) })
    return () => controller.abort()
  }, [])
  return <div className="tk-automation">
    <span className="tk-meter-icon"><ShieldAlert size={18} aria-hidden="true" /></span>
    <div className="tk-automation-text"><p>Task monitoring</p><small>48-hour limit · checked every 15 minutes · {settings?.lastRunAt ? `Last checked ${smartTime(settings.lastRunAt)}` : 'Waiting for the first check'}</small>
    <Select className="tk-auto-select" aria-label="Alerts for overdue tasks" disabled={!settings || busy} value={settings?.escalationRepeatHours ?? 0} onChange={async (e) => {
      const hours = Number(e.target.value) as 0 | 24
      setBusy(true); setError('')
      try { await api.patch('/workflow/settings', { escalationRepeatHours: hours }); setSettings((old) => old ? { ...old, escalationRepeatHours: hours } : old); store.notify('Saved') }
      catch (e) { setError(e instanceof Error ? e.message : 'We could not save this setting. Please try again.') }
      finally { setBusy(false) }
    }}><option value={0}>Tell the admin once per task</option><option value={24}>Tell the admin every 24 hours</option></Select>
    </div>
    {error && <p className="tk-automation-error" role="alert">{error}</p>}
  </div>
}

function ResetDeadline({ task, onClose }: { task: Task; onClose: () => void }) {
  const store = useStore()
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  return <Dialog open onClose={() => { if (!busy) onClose() }} title="Restart the 48-hour clock" description="This gives the task a fresh 48 hours. Earlier overdue alerts and your reason stay in the history." footer={<><Button variant="ghost" disabled={busy} onClick={onClose}>Cancel</Button><Button variant="primary" disabled={busy || reason.trim().length < 3} onClick={async () => {
    setBusy(true); setError('')
    try {
      await api.post(`/tasks/${task.id}/reset-sla`, { reason: reason.trim() })
      try { await store.refresh({ keepCurrent: true }) } catch { store.notify('The clock was restarted. Refresh the list to see it.', 'warn') }
      onClose()
    } catch (e) { setError(e instanceof Error ? e.message : 'We could not restart the clock. Please try again.') }
    finally { setBusy(false) }
  }}>{busy ? 'Saving…' : 'Restart clock'}</Button></>}>
    {error && <p role="alert" className="mb-3 text-sm text-stamp-700">{error}</p>}
    <Field label="Reason" required hint="Saved in the task history. At least 3 characters."><Textarea className="tk-reset-reason" value={reason} onChange={(e) => setReason(e.target.value)} rows={3} maxLength={2000} /></Field>
  </Dialog>
}
