import { useMemo, useState } from 'react'
import { CircleCheck, Clock3, GitBranch, Pencil, TimerOff } from 'lucide-react'
import { cn } from '@/shared/lib/cn'
import { dateLabel, slaState } from '@/shared/lib/format'
import { useStore } from '@/shared/state/crm-store'
import { STAGES, closureLabels, stageByNo } from '@/shared/workflow/stages'
import type { Application } from '@/shared/model/types'
import { DataTable, type Column } from '@/shared/ui/table'
import {
  EmptyState,
  PageHeader,
  Panel,
  StageDot,
} from '@/shared/ui/display'
import { Button, SearchInput, Select } from '@/shared/ui/primitives'
import { Tabs } from '@/shared/ui/overlay'
import { StageModal } from '@/features/workflow/components/stage-modal'
import { RecordIdentity, SummaryCards } from '@/shared/ui/workspace'

type TabKey = 'active' | 'deployed' | 'closed'

const SOURCE_LABEL: Record<string, string> = {
  email: 'Email',
  whatsapp: 'WhatsApp',
  walk_in: 'Walk-in',
  referral: 'Referral',
  other: 'Other',
}

const STATUS_TEXT: Record<string, string> = {
  active: 'Active',
  deployed: 'Deployed',
  closed: 'Closed',
}

/**
 * Every candidate mapped to an order. This is the only place a stage can be worked —
 * the Data Bank stays a read-only directory.
 */
export default function MappedCandidatesPage() {
  const store = useStore()
  const [tab, setTab] = useState<TabKey>('active')
  const [query, setQuery] = useState('')
  const [stageFilter, setStageFilter] = useState('all')
  const [ownerFilter, setOwnerFilter] = useState('all')
  const [editing, setEditing] = useState<string | null>(null)

  const isSupervisor = store.can('all_reports')

  const scoped = useMemo(() => {
    const q = query.trim().toLowerCase()
    return store.applications.filter((a) => {
      const cand = store.candidate(a.candidateId)
      if (!cand) return false
      if (ownerFilter !== 'all' && cand.ownerId !== ownerFilter) return false
      if (!q) return true
      const order = store.order(a.orderId)
      return (
        cand.name.toLowerCase().includes(q) ||
        cand.phone.includes(q) ||
        (cand.passportNo ?? '').toLowerCase().includes(q) ||
        (order?.ref ?? '').toLowerCase().includes(q) ||
        store.orderLabel(a.orderId).toLowerCase().includes(q)
      )
    })
  }, [store.applications, store.candidates, query, ownerFilter])

  const buckets = useMemo(
    () => ({
      active: scoped.filter((a) => a.status === 'active'),
      deployed: scoped.filter((a) => a.status === 'deployed'),
      closed: scoped.filter((a) => a.status === 'closed'),
    }),
    [scoped],
  )

  // Most recently mapped first, which is the order the desk works in.
  const rows = useMemo(() => {
    const base =
      tab === 'active' && stageFilter !== 'all'
        ? buckets.active.filter((a) => String(a.currentStage) === stageFilter)
        : buckets[tab]
    return [...base].sort((a, b) => +new Date(b.createdAt) - +new Date(a.createdAt))
  }, [buckets, tab, stageFilter])

  const columns: Column<Application>[] = [
    {
      key: 'candidate',
      header: 'Candidate',
      sortable: true,
      width: '20%',
      value: (a) => store.candidate(a.candidateId)?.name ?? '',
      render: (a) => {
        const c = store.candidate(a.candidateId)!
        return (
          <RecordIdentity name={c.name} detail={<span className="ident">{c.phone}</span>} />
        )
      },
    },
    {
      key: 'passport',
      header: 'Passport',
      sortable: true,
      className: 'whitespace-nowrap',
      value: (a) => store.candidate(a.candidateId)?.passportNo ?? '',
      render: (a) => {
        const c = store.candidate(a.candidateId)!
        return c.passportNo ? (
          <span className="ident text-ink-600">{c.passportNo}</span>
        ) : (
          <span className="text-ink-300">Not on file</span>
        )
      },
    },
    {
      key: 'designation',
      header: 'Designation',
      sortable: true,
      filterable: true,
      className: 'whitespace-nowrap',
      value: (a) => {
        const o = store.order(a.orderId)
        return o ? (store.designation(o.designationId)?.title ?? '') : ''
      },
    },
    {
      key: 'company',
      header: 'Company',
      sortable: true,
      filterable: true,
      className: 'whitespace-nowrap',
      value: (a) => {
        const o = store.order(a.orderId)
        return o ? (store.company(o.companyId)?.name ?? '') : ''
      },
    },
    {
      key: 'source',
      header: 'Source',
      filterable: true,
      className: 'whitespace-nowrap',
      value: (a) => SOURCE_LABEL[store.candidate(a.candidateId)?.source ?? ''] ?? '',
    },
    {
      key: 'owner',
      header: 'Assigned to',
      sortable: true,
      filterable: true,
      className: 'whitespace-nowrap',
      value: (a) => store.user(store.candidate(a.candidateId)?.ownerId ?? '')?.name ?? '',
    },
    {
      key: 'stage',
      header: 'Stage',
      sortable: true,
      filterable: true,
      width: '15%',
      value: (a) =>
        a.status === 'closed' && a.closureReason
          ? closureLabels[a.closureReason]
          : `${a.currentStage}. ${stageByNo(a.currentStage).title}`,
      render: (a) => {
        if (a.status === 'closed' && a.closureReason) {
          return <span className="text-stamp-700">{closureLabels[a.closureReason]}</span>
        }
        return (
          <span className="flex items-center gap-2 whitespace-nowrap">
            <StageDot no={a.currentStage} />
            <span className="text-ink-700">{stageByNo(a.currentStage).title}</span>
          </span>
        )
      },
    },
    {
      key: 'status',
      header: 'Status',
      filterable: true,
      className: 'whitespace-nowrap',
      value: (a) => STATUS_TEXT[a.status] ?? a.status,
      render: (a) => {
        const task = store.tasks.find(
          (t) => t.applicationId === a.id && !t.completedAt && !t.cancelledAt,
        )
        const overdue = task ? slaState(task.createdAt).overdue : false
        return (
          <span className={cn(overdue ? 'font-medium text-stamp-700' : 'text-ink-600')}>
            {overdue ? 'Overdue' : STATUS_TEXT[a.status]}
          </span>
        )
      },
    },
    {
      key: 'mapped',
      header: 'Mapped',
      sortable: true,
      className: 'whitespace-nowrap',
      value: (a) => a.createdAt,
      render: (a) => <span className="text-ink-500">{dateLabel(a.createdAt)}</span>,
    },
    {
      key: 'action',
      header: '',
      align: 'right',
      render: (a) => {
        const c = store.candidate(a.candidateId)!
        const canWork = store.can('work_any_candidate') || c.ownerId === store.me.id
        return (
          <Button
            size="sm"
            variant={a.status === 'active' ? 'primary' : 'secondary'}
            icon={<Pencil className="h-3.5 w-3.5" />}
            onClick={() => setEditing(a.id)}
          >
            {a.status === 'active' && canWork ? 'Edit' : 'View'}
          </Button>
        )
      },
    },
  ]

  return (
    <>
      <PageHeader
        title="Mapped candidates"
        eyebrow="Recruitment pipeline"
        description="Every match, every milestone. Move candidates from their first screening to deployment."
        actions={
          isSupervisor && (
            <Select
              value={ownerFilter}
              onChange={(e) => setOwnerFilter(e.target.value)}
              className="w-52"
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
            label: 'In progress',
            value: buckets.active.length,
            detail: 'Moving through the pipeline',
            icon: GitBranch,
          },
          {
            label: 'Deployed',
            value: buckets.deployed.length,
            detail: 'Successfully placed overseas',
            icon: CircleCheck,
            tone: 'green',
          },
          {
            label: 'Closed',
            value: buckets.closed.length,
            detail: 'Completed application records',
            icon: Clock3,
            tone: 'violet',
          },
          {
            label: 'Overdue tasks',
            value: store.visibleTasks.filter(
              (task) =>
                !task.completedAt &&
                !task.cancelledAt &&
                slaState(task.createdAt).overdue &&
                buckets.active.some((application) => application.id === task.applicationId),
            ).length,
            detail: 'Active matches needing attention',
            icon: TimerOff,
            tone: 'amber',
          },
        ]}
      />

      <Panel flush>
        <Tabs
          tabs={[
            { key: 'active', label: 'In progress', count: buckets.active.length },
            { key: 'deployed', label: 'Deployed', count: buckets.deployed.length },
            { key: 'closed', label: 'Closed', count: buckets.closed.length, tone: 'stamp' },
          ]}
          active={tab}
          onChange={(k) => setTab(k as TabKey)}
          className="px-3"
        />

        <div className="workspace-list-toolbar">
          <SearchInput
            value={query}
            onChange={setQuery}
            placeholder="Search candidates, passports, or orders..."
            className="min-w-0 flex-1"
          />
          {tab === 'active' && (
            <Select
              value={stageFilter}
              onChange={(e) => setStageFilter(e.target.value)}
              className="w-56"
              aria-label="Filter by stage"
            >
              <option value="all">Every stage</option>
              {STAGES.map((s) => (
                <option key={s.no} value={s.no}>
                  {s.no}. {s.title}
                </option>
              ))}
            </Select>
          )}
          <span className="ml-auto text-xs text-ink-400 tnum">{rows.length} candidates</span>
        </div>

        <DataTable
          rows={rows}
          columns={columns}
          rowKey={(a) => a.id}
          pageSize={20}
          rowClassName={(a) => {
            const task = store.tasks.find(
              (t) => t.applicationId === a.id && !t.completedAt && !t.cancelledAt,
            )
            return task && slaState(task.createdAt).overdue ? 'bg-stamp-50/40' : undefined
          }}
          empty={
            <EmptyState
              title={
                tab === 'active'
                  ? 'Nobody is in progress'
                  : tab === 'deployed'
                    ? 'Nobody deployed yet'
                    : 'No closed applications'
              }
              description={
                tab === 'active'
                  ? 'Match candidates to an order from Jobs / Positions and they appear here.'
                  : tab === 'deployed'
                    ? 'A candidate lands here once stage 11 is complete.'
                    : 'Applications closed at prescreening, interview or medical show up here.'
              }
            />
          }
        />
      </Panel>

      <StageModal applicationId={editing} open={!!editing} onClose={() => setEditing(null)} />
    </>
  )
}
