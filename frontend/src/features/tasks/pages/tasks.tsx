import { useMemo, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { CalendarClock, CheckCheck, ChevronDown, ClipboardList, MessageSquarePlus, TimerOff } from 'lucide-react'
import { cn } from '@/shared/lib/cn'
import { dateTimeLabel, isSameDay, slaState, smartTime, urgencyText } from '@/shared/lib/format'
import { useStore } from '@/shared/state/crm-store'
import { stageByNo } from '@/shared/workflow/stages'
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
import { Button, Select, Textarea } from '@/shared/ui/primitives'
import { Dialog, Tabs } from '@/shared/ui/overlay'
import { DataTable, type Column } from '@/shared/ui/table'
import { StageModal } from '@/features/workflow/components/stage-modal'
import { SummaryCards } from '@/shared/ui/workspace'

type TabKey = 'open' | 'due' | 'overdue' | 'escalated' | 'completed'

export default function TasksPage() {
  const store = useStore()
  const navigate = useNavigate()
  const [params] = useSearchParams()
  const stageParam = params.get('stage')

  const [tab, setTab] = useState<TabKey>('open')
  const [staffFilter, setStaffFilter] = useState('all')
  const [openApp, setOpenApp] = useState<string | null>(null)
  const [remarking, setRemarking] = useState<string | null>(null)

  const isSupervisor = store.can('all_reports')

  const scope = useMemo(() => {
    let list = store.visibleTasks
    if (isSupervisor && staffFilter !== 'all') list = list.filter((t) => t.ownerId === staffFilter)
    if (stageParam) list = list.filter((t) => String(t.stage) === stageParam)
    return list.filter((t) => !t.cancelledAt)
  }, [store.visibleTasks, staffFilter, stageParam, isSupervisor])

  const buckets = useMemo(() => {
    const open = scope.filter((t) => !t.completedAt)
    return {
      open,
      due: open.filter((t) => {
        const s = slaState(t.createdAt)
        return !s.overdue && isSameDay(s.dueAt, new Date())
      }),
      overdue: open.filter((t) => slaState(t.createdAt).overdue),
      escalated: open.filter((t) => t.escalated || slaState(t.createdAt).overdue),
      completed: scope.filter((t) => t.completedAt),
    }
  }, [scope])

  const rows = [...buckets[tab]].sort((a, b) =>
    tab === 'completed'
      ? +new Date(b.completedAt!) - +new Date(a.completedAt!)
      : +new Date(a.createdAt) - +new Date(b.createdAt),
  )

  const tabs = [
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
          <TimeBar createdAt={t.createdAt} />
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
          : t.escalated || slaState(t.createdAt).overdue
            ? 'Escalated'
            : store.updatedToday(t.id)
              ? 'Open'
              : 'No update today',
      render: (t) => {
        if (t.completedAt) return <span className="text-ink-500">Completed</span>
        if (t.escalated || slaState(t.createdAt).overdue)
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

      {stageParam && (
        <p className="mb-3 flex items-center gap-2 rounded-control border border-rule bg-white px-3 py-2 text-xs text-ink-600">
          Filtered to stage {stageParam} — {stageByNo(Number(stageParam)).title}.
          <button onClick={() => navigate('/tasks')} className="font-medium text-navy-700 hover:text-navy-600">
            Clear
          </button>
        </p>
      )}

      {notUpdated > 0 && tab !== 'completed' && (
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
              !t.completedAt && slaState(t.createdAt).overdue ? 'bg-stamp-50/40' : undefined
            }
          />
        )}
      </Panel>

      <RemarksDialog taskId={remarking} onClose={() => setRemarking(null)} />
      <StageModal applicationId={openApp} open={!!openApp} onClose={() => setOpenApp(null)} />
    </>
  )
}

function emptyTitle(tab: TabKey) {
  return {
    open: 'No open tasks',
    due: 'Nothing due today',
    overdue: 'Nothing is overdue',
    escalated: 'No escalations',
    completed: 'No completed tasks yet',
  }[tab]
}

function emptyBody(tab: TabKey) {
  return {
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
  const task = store.tasks.find((t) => t.id === taskId)

  if (!task) return null
  const cand = store.candidate(task.candidateId)
  const remarks = store.remarksFor(task.id)
  const sla = slaState(task.createdAt)
  const canWork = store.can('work_any_candidate') || task.ownerId === store.me.id

  return (
    <Dialog
      open={!!taskId}
      onClose={onClose}
      title="Daily updates"
      description={`${task.title} · ${cand?.name ?? ''}`}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Close
          </Button>
          <Button
            variant="primary"
            disabled={!canWork || !body.trim()}
            onClick={() => {
              void store.addRemark(task.id, body.trim())
              setBody('')
            }}
          >
            Add remark
          </Button>
        </>
      }
    >
      {!store.updatedToday(task.id) && (
        <p className="mb-3 rounded-control border border-amber-100 bg-amber-50 px-3 py-2 text-xs text-amber-700">
          This task has no remark today.
        </p>
      )}

      <Textarea
        rows={3}
        value={body}
        onChange={(e) => setBody(e.target.value)}
        placeholder="What happened today? e.g. Waiting for client feedback"
        disabled={!canWork}
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
