import { useEffect, useMemo, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { CalendarClock, CheckCheck, ChevronDown, ClipboardList, MessageSquarePlus, TimerOff } from 'lucide-react'
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
  StageChip,
  StageDot,
  TimeBar,
} from '@/shared/ui/display'
import { Button, Field, Select, Textarea } from '@/shared/ui/primitives'
import { Dialog, Tabs } from '@/shared/ui/overlay'
import { DataTable, type Column } from '@/shared/ui/table'
import { StageModal } from '@/features/workflow/components/stage-modal'
import { SummaryCards } from '@/shared/ui/workspace'

type TabKey = 'open' | 'due' | 'overdue' | 'escalated' | 'completed' | 'history'

export default function TasksPage() {
  const store = useStore()
  const navigate = useNavigate()
  const [params] = useSearchParams()
  const stageFilter = parseStageNumber(params.get('stage'))

  const [tab, setTab] = useState<TabKey>(params.get('date') ? 'history' : 'open')
  const [staffFilter, setStaffFilter] = useState(params.get('staff') || 'all')
  const [openApp, setOpenApp] = useState<string | null>(null)
  const [remarking, setRemarking] = useState<string | null>(null)
  const [resetting, setResetting] = useState<Task | null>(null)

  const isSupervisor = store.can('all_reports')
  const historyDay = isSupervisor ? params.get('date') : null
  const [historicalIds, setHistoricalIds] = useState<string[]>([])
  const [historyError, setHistoryError] = useState('')
  const [historyLoading, setHistoryLoading] = useState(false)
  useEffect(() => {
    setStaffFilter(params.get('staff') || 'all')
    setTab(params.get('date') && isSupervisor ? 'history' : 'open')
  }, [params, isSupervisor])
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
  }, [historyDay, staffFilter])

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
      completed: scope.filter((t) => t.completedAt),
    }
  }, [scope])

  const rows = [...buckets[tab]].sort((a, b) =>
    tab === 'completed'
      ? +new Date(b.completedAt!) - +new Date(a.completedAt!)
      : +new Date(taskClockStart(a)) - +new Date(taskClockStart(b)),
  )

  const tabs = [
    ...(historyDay ? [{ key: 'history', label: 'Missing updates on this date', count: buckets.history.length }] : []),
    { key: 'open', label: 'Open', count: buckets.open.length },
    { key: 'due', label: 'Due today', count: buckets.due.length, tone: 'amber' as const },
    { key: 'overdue', label: 'Overdue', count: buckets.overdue.length, tone: 'stamp' as const },
    ...(isSupervisor
      ? [{ key: 'escalated', label: 'Escalated', count: buckets.escalated.length, tone: 'stamp' as const }]
      : []),
    { key: 'completed', label: 'Completed', count: buckets.completed.length },
  ]

  const notUpdated = buckets.open.filter((t) => !store.updatedToday(t.id)).length

  const taskColumns: Column<Task>[] = [
    {
      key: 'title',
      header: 'Task',
      sortable: true,
      width: '22%',
      value: (t) => t.title,
      render: (t) => {
        const cand = store.candidate(t.candidateId)
        return (
          <div className="min-w-0">
            <p className="font-semibold leading-snug text-ink">{t.title}</p>
            <p className="text-micro text-ink-400">{cand?.name}</p>
          </div>
        )
      },
    },
    {
      key: 'stage',
      header: 'Stage',
      sortable: true,
      filterable: true,
      value: (t) => (t.stage ? `${t.stage}. ${stageByNo(t.stage).title}` : 'Initial call'),
      render: (t) =>
        t.stage ? (
          <span className="flex items-center gap-2 whitespace-nowrap">
            <StageDot no={t.stage} />
            <span className="text-ink-700">{stageByNo(t.stage).title}</span>
          </span>
        ) : (
          <span className="text-ink-500">Initial call</span>
        ),
    },
    {
      key: 'order',
      header: 'Order',
      filterable: true,
      width: '16%',
      value: (t) => {
        const app = store.applications.find((a) => a.id === t.applicationId)
        return app ? store.orderLabel(app.orderId) : 'Not mapped'
      },
      render: (t) => {
        const app = store.applications.find((a) => a.id === t.applicationId)
        const label = app ? store.orderLabel(app.orderId) : null
        return label ? (
          <span className="block leading-snug text-ink-700">{label}</span>
        ) : (
          <span className="text-ink-300">Not mapped</span>
        )
      },
    },
    {
      key: 'owner',
      header: 'Assigned to',
      sortable: true,
      filterable: true,
      className: 'whitespace-nowrap',
      value: (t) => store.user(t.ownerId)?.name ?? '',
    },
    {
      key: 'sla',
      header: '48-hour limit',
      sortable: true,
      width: '13%',
      value: (t) => t.createdAt,
      render: (t) =>
        t.completedAt ? (
          <span className="text-ink-400">Completed {smartTime(t.completedAt)}</span>
        ) : (
          <TimeBar createdAt={taskClockStart(t)} />
        ),
    },
    {
      key: 'status',
      header: 'Status',
      filterable: true,
      className: 'whitespace-nowrap',
      value: (t) =>
        t.completedAt
          ? 'Completed'
          : t.escalated
            ? 'Escalated'
            : store.updatedToday(t.id)
              ? 'Open'
              : 'No update today',
      render: (t) => {
        if (t.completedAt) return <span className="text-ink-500">Completed</span>
        if (t.escalated)
          return <span className="font-medium text-stamp-700">Escalated</span>
        if (!store.updatedToday(t.id))
          return <span className="font-medium text-amber-700">No update today</span>
        return <span className="text-ink-600">Open</span>
      },
    },
    {
      key: 'remarks',
      header: 'Remark',
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
                <span className="block leading-snug text-ink-700 group-hover:text-ink">
                  {latest.body}
                </span>
                <span className="mt-0.5 block text-micro text-ink-400">
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
  if (store.me.role === 'admin') taskColumns.push({ key: 'reset', header: 'Deadline', render: (task) => !task.completedAt && !task.cancelledAt && <Button size="sm" variant="ghost" onClick={(event) => { event.stopPropagation(); setResetting(task) }}>Reset SLA</Button> })

  return (
    <>
      <PageHeader
        title="Tasks"
        eyebrow="Team workflow"
        description="Stay on top of every follow-up. Tasks close automatically when their recruitment stage is complete."
        actions={
          isSupervisor && (
            <Select
              value={staffFilter}
              onChange={(e) => setStaffFilter(e.target.value)}
              className="w-48"
              aria-label="Filter by staff"
            >
              <option value="all">Whole team</option>
              {store.users
                .filter((u) => u.role === 'staff' && u.active)
                .map((u) => (
                  <option key={u.id} value={u.id}>
                    {u.name}
                  </option>
                ))}
            </Select>
          )
        }
      />

      <SummaryCards
        items={[
          {
            label: 'Open tasks',
            value: buckets.open.length,
            detail: 'Across your current workflow',
            icon: ClipboardList,
          },
          {
            label: 'Due today',
            value: buckets.due.length,
            detail: 'Prioritise these next',
            icon: CalendarClock,
            tone: 'violet',
          },
          {
            label: 'Overdue',
            value: buckets.overdue.length,
            detail: 'Past the 48-hour limit',
            icon: TimerOff,
            tone: 'amber',
          },
          {
            label: 'Completed',
            value: buckets.completed.length,
            detail: 'Stages successfully finished',
            icon: CheckCheck,
            tone: 'green',
          },
        ]}
      />
      {store.me.role === 'admin' && <AutomationSettings />}
      {historyDay && <p className="mb-3 rounded-control border border-amber-100 bg-amber-50 p-3 text-sm text-amber-700">Daily updates missing on {historyDay}. This list preserves the recruiter responsible on that date, including tasks that have since closed or moved. <button className="font-medium underline" onClick={() => navigate('/tasks')}>Show current tasks</button></p>}
      {historyLoading && <p role="status" className="mb-3 text-sm text-ink-500">Loading daily history?</p>}
      {historyError && <p role="alert" className="mb-3 text-sm text-stamp-700">{historyError}</p>}
      {params.get('task') && <p className="mb-3 text-sm">Showing the task from your notification. <button className="font-medium text-navy-700" onClick={() => navigate('/tasks')}>Show all tasks</button></p>}

      {stageFilter !== null && (
        <p className="mb-3 flex items-center gap-2 rounded-control border border-rule bg-white px-3 py-2 text-xs text-ink-600">
          Filtered to stage {stageFilter} — {stageByNo(stageFilter).title}.
          <button onClick={() => navigate('/tasks')} className="font-medium text-navy-700 hover:text-navy-600">
            Clear
          </button>
        </p>
      )}

      {notUpdated > 0 && tab !== 'completed' && tab !== 'history' && (
        <p className="workspace-notice mb-3 rounded-control border border-amber-100 bg-amber-50 px-3 py-2 text-xs text-amber-700">
          <MessageSquarePlus size={18} aria-hidden="true" />
          <span>
          {notUpdated} open {notUpdated === 1 ? 'task has' : 'tasks have'} no remark today. Every open
          task needs one daily update until it is completed.
          </span>
        </p>
      )}

      <Panel flush>
        <Tabs tabs={tabs} active={tab} onChange={(k) => setTab(k as TabKey)} className="px-2" />

        {rows.length === 0 ? (
          <EmptyState
            title={emptyTitle(tab)}
            description={emptyBody(tab)}
          />
        ) : (
          <DataTable
            rows={rows}
            columns={taskColumns}
            rowKey={(t) => t.id}
            pageSize={20}
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
  return <div className="mb-4 flex flex-wrap items-center gap-3 rounded-xl border border-rule bg-white p-4 text-sm">
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
