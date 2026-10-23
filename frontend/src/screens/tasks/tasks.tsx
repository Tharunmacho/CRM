import { useEffect, useMemo, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { CalendarClock, CheckCheck, ClipboardList, MessageSquarePlus, TimerOff, UserRound } from 'lucide-react'
import { cn } from '@/shared/lib/cn'
import { api } from '@/shared/api/client'
import { agencyDay, taskClockStart } from '@/shared/lib/task-time'
import { dateTimeLabel, isSameDay, slaState, smartTime, urgencyText } from '@/shared/lib/format'
import { useStore } from '@/shared/state/crm-store'
import { parseStageNumber, stageByNo } from '@/shared/workflow/stages'
import type { Task } from '@/shared/model/types'
import {
  Badge,
  EmptyState,
  PageHeader,
  Panel,
  StageDot,
  TimeBar,
} from '@/shared/ui/display'
import { Button, Field, Input, SearchInput, Select, Textarea } from '@/shared/ui/primitives'
import { Dialog, Tabs } from '@/shared/ui/overlay'
import { DataTable, type Column } from '@/shared/ui/table'
import { StageModal } from '@/features/workflow/components/stage-modal'
import { RecordIdentity, SummaryCards } from '@/shared/ui/workspace'
import { ReassignCandidatesDialog } from '@/features/staff/reassign-candidates-dialog'
import { completedInPeriod, taskViewFromParams, validDateFilter, type TaskView } from '@/features/staff/task-filters'
import { useDeadlineClock, useWorkspaceRead } from '@/features/staff/use-workspace-read'
import '@/styles/tasks-page.css'

type TabKey = TaskView

export default function TasksPage() {
  const store = useStore()
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()
  const stageFilter = parseStageNumber(params.get('stage'))

  const [openApp, setOpenApp] = useState<string | null>(null)
  const [remarking, setRemarking] = useState<string | null>(null)
  const [resetting, setResetting] = useState<Task | null>(null)
  const [reassigning, setReassigning] = useState<Task | null>(null)
  const [query, setQuery] = useState('')
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
    return {
      open,
      history: scope,
      due: open.filter((t) => {
        const s = slaState(taskClockStart(t))
        return !s.overdue && agencyDay(s.dueAt, store.agencyTimezone) === agencyDay(new Date(), store.agencyTimezone)
      }),
      overdue: open.filter((t) => slaState(taskClockStart(t)).overdue),
      escalated: open.filter((t) => t.escalated),
      initial: open.filter((t) => t.kind === 'initial_call'),
      missing: open.filter((t) => !store.updatedToday(t.id)),
      completed: scope.filter((t) => completedInPeriod(t.completedAt, completedFrom, completedTo, store.agencyTimezone)),
    }
  }, [scope, completedFrom, completedTo, store.agencyTimezone, store.updatedToday, clock])

  const term = query.trim().toLowerCase()
  const matches = (task: Task) => {
    if (!term) return true
    const app = store.applications.find((a) => a.id === task.applicationId)
    return [task.title, store.candidate(task.candidateId)?.name, app ? store.orderLabel(app.orderId) : '', store.user(task.ownerId)?.name]
      .some((value) => value?.toLowerCase().includes(term))
  }
  const rows = buckets[tab].filter(matches).sort((a, b) =>
    tab === 'completed'
      ? +new Date(b.completedAt!) - +new Date(a.completedAt!)
      : +new Date(taskClockStart(a)) - +new Date(taskClockStart(b)),
  )

  const tabs = [
    ...(historyDay ? [{ key: 'history', label: 'Missing updates on this date' }] : []),
    { key: 'open', label: 'Open' },
    { key: 'due', label: 'Due today' },
    { key: 'overdue', label: 'Overdue' },
    { key: 'completed', label: 'Completed' },
  ]
  const moreViews = [
    { key: 'initial', label: 'Initial calls' },
    { key: 'missing', label: 'No update today' },
    ...(isSupervisor ? [{ key: 'escalated', label: 'Escalated' }] : []),
  ]

  const notUpdated = buckets.open.filter((t) => !store.updatedToday(t.id)).length

  if (!taskReadReady || (historyDay && (historyLoading || historyError))) return <>
    <PageHeader title="Tasks" description="Follow-ups and deadlines across the recruitment workflow." />
    <Panel><div className="space-y-3 p-5">
      {taskReadError || historyError ? <><p role="alert" className="text-sm text-stamp-700">{taskReadError || historyError}</p><p className="text-sm text-ink-500">Refresh this view to load current tasks. Previous counts are hidden until the check succeeds.</p><Button onClick={taskRead.refresh}>Retry task refresh</Button></>
        : <p role="status" className="text-sm text-ink-500">Loading current ownership and task status…</p>}
    </div></Panel>
  </>

  const orderOf = (t: Task) => {
    const app = store.applications.find((a) => a.id === t.applicationId)
    return app ? store.orderLabel(app.orderId) : null
  }
  const statusOf = (t: Task) => t.completedAt ? 'Completed' : t.escalated ? 'Escalated' : store.updatedToday(t.id) ? 'Open' : 'No update today'
  const taskColumns: Column<Task>[] = [
    {
      key: 'title',
      header: 'Task',
      sortable: true,
      width: '24%',
      value: (t) => t.title,
      render: (t) => <RecordIdentity name={store.candidate(t.candidateId)?.name ?? t.title} detail={t.title} />,
    },
    {
      key: 'order',
      header: 'Order & stage',
      width: '20%',
      value: (t) => orderOf(t) ?? 'Not mapped',
      render: (t) => (
        <div className="min-w-[10rem]">
          <p className="leading-snug text-ink">{orderOf(t) ?? <span className="text-ink-400">Not mapped to an order</span>}</p>
          <p className="mt-0.5 flex items-center gap-1.5 text-[13px] text-ink-500">
            {t.stage ? <><StageDot no={t.stage} />{stageByNo(t.stage).title}</> : 'Initial call'}
          </p>
        </div>
      ),
    },
    {
      key: 'owner',
      header: 'Assigned to',
      sortable: true,
      className: 'whitespace-nowrap',
      value: (t) => store.user(t.ownerId)?.name ?? '',
      render: (t) => <span className="inline-flex items-center gap-1.5"><UserRound size={14} className="text-ink-400" aria-hidden />{store.user(t.ownerId)?.name ?? '—'}</span>,
    },
    {
      key: 'sla',
      header: 'Deadline',
      sortable: true,
      width: '14%',
      value: (t) => t.createdAt,
      render: (t) =>
        t.completedAt ? (
          <span className="text-[13px] text-ink-500">Completed {smartTime(t.completedAt)}</span>
        ) : (
          <TimeBar createdAt={taskClockStart(t)} />
        ),
    },
    {
      key: 'status',
      header: 'Status',
      className: 'whitespace-nowrap',
      value: statusOf,
      render: (t) => <Badge dot tone={t.completedAt ? 'neutral' : t.escalated ? 'stamp' : store.updatedToday(t.id) ? 'navy' : 'amber'}>{statusOf(t)}</Badge>,
    },
    {
      key: 'remarks',
      header: 'Latest remark',
      width: '20%',
      value: (t) => store.remarksFor(t.id)[0]?.body ?? '',
      render: (t) => {
        const latest = store.remarksFor(t.id)[0]
        if (t.completedAt && !latest) return <span className="text-ink-300">—</span>
        return (
          <button
            onClick={(e) => {
              e.stopPropagation()
              setRemarking(t.id)
            }}
            className="group w-full text-left"
          >
            {latest ? (
              <>
                <span className="line-clamp-2 leading-snug text-ink-700 group-hover:text-ink">{latest.body}</span>
                <span className="mt-0.5 block text-[12.5px] text-ink-400">
                  {store.user(latest.authorId)?.name} · {smartTime(latest.createdAt)}
                </span>
              </>
            ) : (
              <span className="inline-flex items-center gap-1.5 font-medium text-navy-700 group-hover:text-navy-600">
                <MessageSquarePlus className="h-3.5 w-3.5" />
                Add remark
              </span>
            )}
          </button>
        )
      },
    },
  ]
  const canReassign = store.can('reassign')
  const canReset = store.me.role === 'admin'
  if (canReassign || canReset) taskColumns.push({
    key: 'actions', header: '',
    render: (task) => (!task.completedAt && !task.cancelledAt) ? (
      <div className="flex items-center justify-end gap-1 whitespace-nowrap">
        {canReassign && <Button size="sm" variant="ghost" onClick={(event) => { event.stopPropagation(); setReassigning(task) }}>Reassign</Button>}
        {canReset && <Button size="sm" variant="ghost" onClick={(event) => { event.stopPropagation(); setResetting(task) }}>Reset SLA</Button>}
      </div>
    ) : null,
  })

  return (
    <>
      <PageHeader
        title="Tasks"
        description="Follow-ups and deadlines across the recruitment workflow."
        actions={<Button onClick={taskRead.refresh}>Refresh tasks</Button>}
      />

      <SummaryCards
        items={[
          { label: 'Open tasks', value: buckets.open.length, detail: 'In your current workflow', icon: ClipboardList },
          { label: 'Due today', value: buckets.due.length, detail: 'Prioritise these next', icon: CalendarClock, tone: 'violet' },
          { label: 'Overdue', value: buckets.overdue.length, detail: 'Past the 48-hour limit', icon: TimerOff, tone: 'amber' },
          { label: 'Completed', value: buckets.completed.length, detail: completedFrom || completedTo ? 'In the selected period' : 'Finished tasks', icon: CheckCheck, tone: 'green' },
        ]}
      />
      {store.me.role === 'admin' && <AutomationSettings />}

      <div className="tasks-notices">
        {historyDay && <p className="is-warn">Daily updates missing on {historyDay}, for the recruiter responsible on that date. <button onClick={() => navigate('/tasks')}>Show current tasks</button></p>}
        {params.get('task') && notificationTask && <p>Showing the task from your notification. <button onClick={() => navigate('/tasks')}>Show all tasks</button></p>}
        {notificationTask?.completedAt && <p>Completed {dateTimeLabel(notificationTask.completedAt)}. {tab !== 'completed' && <button onClick={() => setParams(previous => { const next = new URLSearchParams(previous); next.set('view', 'completed'); next.delete('from'); next.delete('to'); next.delete('date'); return next })}>View completed task</button>}</p>}
        {notificationTask?.cancelledAt && <p>Cancelled {dateTimeLabel(notificationTask.cancelledAt)}. <button onClick={() => navigate(`/candidates/${notificationTask.candidateId}`)}>View candidate history</button></p>}
        {params.get('task') && !notificationTask && <p role="status">This task is no longer available in your current access.</p>}
        {stageFilter !== null && <p>Stage {stageFilter} — {stageByNo(stageFilter).title}. <button onClick={() => navigate('/tasks')}>Clear</button></p>}
        {notUpdated > 0 && tab !== 'completed' && tab !== 'history' && <p className="is-warn"><MessageSquarePlus size={15} aria-hidden="true" />{notUpdated} open {notUpdated === 1 ? 'task has' : 'tasks have'} no remark today. Each open task needs a daily update.</p>}
      </div>

      <Panel flush>
        <Tabs tabs={tabs} active={tab} onChange={changeTab} className="px-2" />
        <div className="tasks-toolbar">
          <SearchInput value={query} onChange={setQuery} placeholder="Search task, candidate, order or recruiter" className="tasks-search" />
          {isSupervisor && (
            <Select value={staffFilter} onChange={(e) => updateFilter('staff', e.target.value)} className="w-48" aria-label="Filter by staff">
              <option value="all">Whole team</option>
              {store.users
                .filter((u) => u.role !== 'admin' || u.id === staffFilter)
                .map((u) => <option key={u.id} value={u.id}>{u.name}{!u.active ? ' (inactive)' : ''}</option>)}
            </Select>
          )}
          <Select className="w-44" aria-label="More task views" value={moreViews.some(view => view.key === tab) ? tab : ''} onChange={(e) => { if (e.target.value) changeTab(e.target.value) }}>
            <option value="">More views</option>
            {moreViews.map((view) => <option key={view.key} value={view.key}>{view.label}</option>)}
          </Select>
          {tab === 'completed' && <>
            <label className="tasks-date">From<Input type="date" value={completedFrom} max={completedTo || undefined} onChange={event => updateFilter('from', event.target.value)} aria-label="Completed from" /></label>
            <label className="tasks-date">To<Input type="date" value={completedTo} min={completedFrom || undefined} onChange={event => updateFilter('to', event.target.value)} aria-label="Completed to" /></label>
          </>}
          <span className="tasks-count" role="status">{rows.length} {rows.length === 1 ? 'task' : 'tasks'}</span>
        </div>

        {rows.length === 0 ? (
          <EmptyState title={emptyTitle(tab)} description={term ? 'Try a different search.' : emptyBody(tab)} />
        ) : (
          <DataTable
            rows={rows}
            columns={taskColumns}
            rowKey={(t) => t.id}
            pageSize={20}
            resetPageKey={`${tab}:${term}:${staffFilter}`}
            onRowClick={(t) =>
              t.applicationId ? setOpenApp(t.applicationId) : navigate(`/candidates/${t.candidateId}`)
            }
            rowClassName={(t) =>
              !t.completedAt && slaState(taskClockStart(t)).overdue ? 'bg-stamp-50/40' : undefined
            }
          />
        )}
      </Panel>

      <RemarksDialog key={remarking} taskId={remarking} onClose={() => setRemarking(null)} />
      {resetting && <ResetDeadline task={resetting} onClose={() => setResetting(null)} />}
      {reassigning && <ReassignCandidatesDialog key={reassigning.candidateId} candidateIds={[reassigning.candidateId]} onClose={() => setReassigning(null)} description={`${store.candidate(reassigning.candidateId)?.name || 'Candidate'} · ${reassigning.title}`} />}
      <StageModal applicationId={openApp} open={!!openApp} onClose={() => setOpenApp(null)} />
    </>
  )
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
  return <div className="tasks-automation">
    <div className="min-w-0 flex-1"><p className="font-medium text-ink">Task monitoring</p><p className="text-xs text-ink-500">48-hour deadline · 15-minute checks · {settings?.lastRunAt ? `Last checked ${smartTime(settings.lastRunAt)}` : 'Waiting for the scheduler'}</p></div>
    <Select aria-label="Escalation reminders" disabled={!settings || busy} value={settings?.escalationRepeatHours ?? 0} onChange={async (e) => {
      const hours = Number(e.target.value) as 0 | 24
      setBusy(true); setError('')
      try { await api.patch('/workflow/settings', { escalationRepeatHours: hours }); setSettings((old) => old ? { ...old, escalationRepeatHours: hours } : old); store.notify('Escalation reminders updated') }
      catch (e) { setError(e instanceof Error ? e.message : 'Settings could not be saved') }
      finally { setBusy(false) }
    }}><option value={0}>One escalation per task</option><option value={24}>Repeat every 24 hours</option></Select>
    {error && <p className="w-full text-xs text-stamp-700" role="alert">{error}</p>}
  </div>
}

function ResetDeadline({ task, onClose }: { task: Task; onClose: () => void }) {
  const store = useStore()
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  return <Dialog open onClose={() => { if (!busy) onClose() }} title="Reset the task deadline" description="Start a new 48-hour window. Previous escalations and this reason remain in the audit history." footer={<><Button variant="ghost" disabled={busy} onClick={onClose}>Cancel</Button><Button variant="primary" disabled={busy || reason.trim().length < 3} onClick={async () => {
    setBusy(true); setError('')
    try {
      await api.post(`/tasks/${task.id}/reset-sla`, { reason: reason.trim() })
      try { await store.refresh({ keepCurrent: true }) } catch { store.notify('Deadline reset. Refresh the task list to see it.', 'warn') }
      onClose()
    } catch (e) { setError(e instanceof Error ? e.message : 'Deadline could not be reset') }
    finally { setBusy(false) }
  }}>{busy ? 'Saving…' : 'Reset deadline'}</Button></>}>
    {error && <p role="alert" className="mb-3 text-sm text-stamp-700">{error}</p>}
    <Field label="Reason" required><Textarea value={reason} onChange={(e) => setReason(e.target.value)} rows={3} maxLength={2000} /></Field>
  </Dialog>
}

function emptyBody(tab: TabKey) {
  return {
    history: 'There are no recorded missing updates for the selected recruiter and agency date.',
    open: 'Tasks appear here as candidates are assigned and stages complete.',
    due: 'Every open task still has time on its 48-hour clock.',
    overdue: 'The desk is inside its limits. Keep the daily remarks going.',
    escalated: 'Nothing has passed 48 hours without being completed.',
    initial: 'Initial-call tasks appear when candidates are assigned and close after a call outcome is recorded.',
    missing: 'There are no open tasks missing a daily remark in the selected scope.',
    completed: 'A task closes when its stage is completed.',
  }[tab]
}

/**
 * Daily updates. Every open task needs a remark each day, and remarks are permanent —
 * so this is a write-and-read panel, never an edit form.
 */
function RemarksDialog({ taskId, onClose }: { taskId: string | null; onClose: () => void }) {
  const store = useStore()
  const [body, setBody] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const task = store.tasks.find((t) => t.id === taskId)

  if (!task) return null
  const cand = store.candidate(task.candidateId)
  const remarks = store.remarksFor(task.id)
  const sla = slaState(taskClockStart(task))
  const canWork = !task.completedAt && !task.cancelledAt && (store.can('work_any_candidate') || task.ownerId === store.me.id)

  return (
    <Dialog
      open={!!taskId}
      onClose={() => { if (!busy) onClose() }}
      title="Daily updates"
      description={`${task.title} · ${cand?.name ?? ''}`}
      footer={
        <>
          <Button variant="ghost" disabled={busy} onClick={onClose}>
            Close
          </Button>
          <Button
            variant="primary"
            disabled={busy || !canWork || !body.trim()}
            onClick={async () => {
              if (busy || !canWork || !body.trim()) return
              setBusy(true); setError('')
              try { await store.addRemark(task.id, body.trim()); setBody('') }
              catch (e) { setError(e instanceof Error ? e.message : 'Remark could not be saved') }
              finally { setBusy(false) }
            }}
          >
            Add remark
          </Button>
        </>
      }
    >
      {error && <p role="alert" className="mb-3 text-sm text-stamp-700">{error}</p>}
      {canWork && !store.updatedToday(task.id) && (
        <p className="mb-3 rounded-control border border-amber-100 bg-amber-50 px-3 py-2 text-xs text-amber-700">
          This task has no remark today.
        </p>
      )}

      <Textarea
        rows={3}
        value={body}
        onChange={(e) => setBody(e.target.value)}
        placeholder="What happened today? e.g. Waiting for client feedback"
        maxLength={10000}
        disabled={!canWork || busy}
      />

      {remarks.length > 0 ? (
        <ul className="mt-4 space-y-3 border-t border-rule-soft pt-4">
          {remarks.map((r) => (
            <li key={r.id}>
              <p className="text-sm leading-snug text-ink-700">{r.body}</p>
              <p className="mt-0.5 text-micro text-ink-400">
                {store.user(r.authorId)?.name} · {smartTime(r.createdAt)}
              </p>
            </li>
          ))}
        </ul>
      ) : (
        <p className="mt-4 border-t border-rule-soft pt-4 text-micro text-ink-400">
          No remarks yet. Remarks are timestamped and cannot be edited or deleted.
        </p>
      )}

      <p className="mt-4 border-t border-rule-soft pt-3 text-micro text-ink-400">
        Raised {dateTimeLabel(task.createdAt)} · due {dateTimeLabel(sla.dueAt)} ·{' '}
        <span className={urgencyText[sla.urgency]}>{sla.label}</span>
      </p>
    </Dialog>
  )
}
