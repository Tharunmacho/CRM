import { useMemo, useState, type ReactNode } from 'react'
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { BriefcaseBusiness, ChartNoAxesCombined, CircleCheck, Download, FileDown, GitBranch } from 'lucide-react'
import { cn } from '@/shared/lib/cn'
import { money, slaState } from '@/shared/lib/format'
import { useStore } from '@/shared/state/crm-store'
import { STAGES, closureLabels } from '@/shared/workflow/stages'
import { EmptyState, PageHeader, Panel, PanelHeader } from '@/shared/ui/display'
import { Button, Field, Select } from '@/shared/ui/primitives'
import { Tabs } from '@/shared/ui/overlay'
import { DataTable } from '@/shared/ui/table'
import { SummaryCards } from '@/shared/ui/workspace'

/**
 * Pastel chart palette, client-specified. Assigned in this fixed order, never cycled.
 * These are deliberately light, so every chart here must keep its direct value labels
 * and legend — the colour alone does not separate the series.
 */
const CATEGORICAL = ['#4169E1', '#9382D8', '#47AD94', '#E5AC5C', '#8BB4EB'] as const
/** Sequential teal ramp, light to dark, for the order funnel's ordered depth. */
const SEQUENTIAL = ['#DCE8FD', '#A9C5F8', '#7FA6F3', '#5B8DEF'] as const

const AXIS = { fontSize: 12, fill: '#7B8190', fontFamily: 'Poppins, sans-serif' }
const GRID = '#E6EAF0'

type ReportKey = 'funnel' | 'staff' | 'source' | 'orders' | 'rejections' | 'advance'

export default function ReportsPage() {
  const store = useStore()
  const [report, setReport] = useState<ReportKey>('funnel')
  const [range, setRange] = useState('90')
  const [clientId, setClientId] = useState('all')
  const [staffId, setStaffId] = useState('all')

  const scopedApps = useMemo(() => {
    const since = new Date(Date.now() - Number(range) * 86400_000)
    return store.applications.filter((a) => {
      if (new Date(a.createdAt) < since) return false
      if (clientId !== 'all' && store.order(a.orderId)?.companyId !== clientId) return false
      if (staffId !== 'all' && store.candidate(a.candidateId)?.ownerId !== staffId) return false
      return true
    })
  }, [store.applications, range, clientId, staffId])

  const tabs = [
    { key: 'funnel', label: 'Pipeline funnel' },
    { key: 'staff', label: 'Staff performance' },
    { key: 'source', label: 'Source' },
    { key: 'orders', label: 'Order status' },
    { key: 'rejections', label: 'Rejections' },
    { key: 'advance', label: 'Advance collections' },
  ]

  return (
    <>
      <PageHeader
        title="Reports"
        eyebrow="Insights & performance"
        description={
          store.can('all_reports')
            ? 'The whole desk. Filter by period, client and staff.'
            : 'Your own numbers. Admins and managers see the whole team.'
        }
        actions={
          <>
            <Button variant="secondary" icon={<Download className="h-4 w-4" />}>
              Excel
            </Button>
            <Button variant="secondary" icon={<FileDown className="h-4 w-4" />}>
              PDF
            </Button>
          </>
        }
      />

      <SummaryCards
        items={[
          {
            label: 'Applications',
            value: scopedApps.length,
            detail: 'In the selected period',
            icon: GitBranch,
          },
          {
            label: 'Deployed',
            value: scopedApps.filter((application) => application.status === 'deployed').length,
            detail: 'Successful placements in this cohort',
            icon: CircleCheck,
            tone: 'green',
          },
          {
            label: 'Placement rate',
            value: `${scopedApps.length ? Math.round((scopedApps.filter((application) => application.status === 'deployed').length / scopedApps.length) * 100) : 0}%`,
            detail: 'Deployed out of all applications',
            icon: ChartNoAxesCombined,
            tone: 'violet',
          },
          {
            label: 'Linked orders',
            value: new Set(scopedApps.map((application) => application.orderId)).size,
            detail: 'Represented in this period',
            icon: BriefcaseBusiness,
            tone: 'amber',
          },
        ]}
      />

      {/* Filters sit in one row above the charts. */}
      <Panel className="report-filter-panel mb-4">
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <Field label="Period">
            <Select value={range} onChange={(e) => setRange(e.target.value)}>
              <option value="30">Last 30 days</option>
              <option value="90">Last 90 days</option>
              <option value="180">Last 6 months</option>
              <option value="365">Last 12 months</option>
            </Select>
          </Field>
          <Field label="Company">
            <Select value={clientId} onChange={(e) => setClientId(e.target.value)}>
              <option value="all">All companies</option>
              {store.companies.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </Select>
          </Field>
          {store.can('all_reports') && (
            <Field label="Staff">
              <Select value={staffId} onChange={(e) => setStaffId(e.target.value)}>
                <option value="all">Whole team</option>
                {store.users
                  .filter((u) => u.role === 'staff')
                  .map((u) => (
                    <option key={u.id} value={u.id}>
                      {u.name}
                    </option>
                  ))}
              </Select>
            </Field>
          )}
          <div className="flex items-end">
            <p className="text-micro text-ink-400 tnum">
              {scopedApps.length} applications in this period
            </p>
          </div>
        </div>
      </Panel>

      <Panel flush>
        <Tabs tabs={tabs} active={report} onChange={(k) => setReport(k as ReportKey)} className="px-2" />
        <div className="report-content px-4 py-5">
          {report === 'funnel' && <Funnel apps={scopedApps} />}
          {report === 'staff' && <StaffPerformance />}
          {report === 'source' && <SourceReport />}
          {report === 'orders' && <OrderStatus />}
          {report === 'rejections' && <Rejections apps={scopedApps} />}
          {report === 'advance' && <Advance apps={scopedApps} />}
        </div>
      </Panel>
    </>
  )
}

/* --------------------------------------------------------------- Tooltip */

function ChartTooltip({ active, payload, label }: any) {
  if (!active || !payload?.length) return null
  return (
    <div className="rounded-control border border-rule bg-white px-2.5 py-2 shadow-pop">
      <p className="text-xs font-medium text-ink">{label}</p>
      <ul className="mt-1 space-y-0.5">
        {payload.map((p: any) => (
          <li key={p.dataKey} className="flex items-center gap-2 text-micro">
            <span className="h-2 w-2 rounded-[2px]" style={{ backgroundColor: p.color ?? p.fill }} />
            <span className="text-ink-500">{p.name}</span>
            <span className="ml-auto tnum font-medium text-ink">{p.value}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}

function ChartFrame({ title, note, children }: { title: string; note: string; children: ReactNode }) {
  return (
    <figure className="m-0">
      <figcaption className="mb-3">
        <h3 className="text-h3 font-semibold text-ink">{title}</h3>
        <p className="mt-0.5 max-w-[70ch] text-xs text-ink-500">{note}</p>
      </figcaption>
      {children}
    </figure>
  )
}

/* ---------------------------------------------------------------- Funnel */

function Funnel({ apps }: { apps: ReturnType<typeof useStore>['applications'] }) {
  const data = STAGES.map((s) => {
    // Anyone who reached this stage: still on it, or completed it earlier.
    const reached = apps.filter(
      (a) => a.currentStage > s.no || a.stages.some((r) => r.stage === s.no) || a.currentStage === s.no,
    ).length
    return { stage: `${s.no}. ${s.title}`, reached }
  })

  const withDrop = data.map((d, i) => ({
    ...d,
    drop: i === 0 ? 0 : data[i - 1].reached - d.reached,
    dropPct: i === 0 || !data[i - 1].reached ? 0 : Math.round(((data[i - 1].reached - d.reached) / data[i - 1].reached) * 100),
  }))

  if (!apps.length) return <EmptyState title="No applications in this period" description="Widen the date range." />

  return (
    <ChartFrame
      title="Pipeline funnel"
      note="Candidates who reached each stage, and how many were lost at each hand-off."
    >
      <div className="grid gap-6 lg:grid-cols-[1fr_18rem]">
        <ResponsiveContainer width="100%" height={460}>
          <BarChart data={withDrop} layout="vertical" margin={{ left: 8, right: 32, top: 4, bottom: 4 }}>
            <CartesianGrid horizontal={false} stroke={GRID} />
            <XAxis type="number" tick={AXIS} axisLine={false} tickLine={false} allowDecimals={false} />
            <YAxis
              interval={0}
              type="category"
              dataKey="stage"
              tick={AXIS}
              axisLine={false}
              tickLine={false}
              width={152}
            />
            <Tooltip content={<ChartTooltip />} cursor={{ fill: 'rgba(17,28,46,0.04)' }} />
            <Bar
            isAnimationActive={false}
              dataKey="reached"
              name="Reached"
              fill={CATEGORICAL[0]}
              radius={[0, 4, 4, 0]}
              barSize={14}
              label={{ position: 'right', fontSize: 11, fill: '#4A5B73' }}
            />
          </BarChart>
        </ResponsiveContainer>

        <div>
          <p className="mb-2 text-xs font-medium text-ink">Drop-off between stages</p>
          <ul className="divide-y divide-rule-soft rounded-control border border-rule-soft">
            {withDrop.slice(1).filter((d) => d.drop > 0).map((d) => (
              <li key={d.stage} className="flex items-center gap-2 px-2.5 py-1.5 text-micro">
                <span className="min-w-0 flex-1 truncate text-ink-600">{d.stage}</span>
                <span className="shrink-0 tnum font-medium text-stamp-600">−{d.drop}</span>
                <span className="w-9 shrink-0 text-right tnum text-ink-400">{d.dropPct}%</span>
              </li>
            ))}
            {withDrop.slice(1).every((d) => d.drop <= 0) && (
              <li className="px-2.5 py-3 text-center text-micro text-ink-400">
                Nobody has dropped out in this period.
              </li>
            )}
          </ul>
        </div>
      </div>
    </ChartFrame>
  )
}

/* ------------------------------------------------------ Staff performance */

function StaffPerformance() {
  const store = useStore()
  const staff = store.users.filter((u) => u.role === 'staff')

  const rows = staff.map((u) => {
    const assigned = store.candidates.filter((c) => c.ownerId === u.id).length
    const theirTasks = store.tasks.filter((t) => t.ownerId === u.id)
    const completed = theirTasks.filter((t) => t.completedAt).length
    const open = theirTasks.filter((t) => !t.completedAt && !t.cancelledAt)
    const escalations = open.filter((t) => slaState(t.createdAt).overdue).length
    const missed = open.filter((t) => !store.updatedToday(t.id)).length
    const avgHours = completed
      ? Math.round(
          theirTasks
            .filter((t) => t.completedAt)
            .reduce((sum, t) => sum + (+new Date(t.completedAt!) - +new Date(t.createdAt)) / 3600_000, 0) /
            completed,
        )
      : 0
    return { name: u.name, assigned, completed, escalations, missed, avgHours }
  })

  const peak = Math.max(1, ...rows.map((r) => Math.max(r.assigned, r.completed)))

  return (
    <ChartFrame
      title="Staff performance"
      note="Workload, throughput and where the limit is being missed. Measures of different scale stay in their own columns rather than sharing an axis."
    >
      <DataTable
        rows={rows}
        rowKey={(r) => r.name}
        pageSize={10}
        columns={[
          { key: 'name', header: 'Staff', sortable: true, value: (r) => r.name,
            render: (r) => <span className="font-semibold text-ink">{r.name}</span> },
          {
            key: 'load',
            header: 'Assigned vs completed',
            width: '26%',
            render: (r) => (
              <div className="space-y-1">
                <div className="h-2 overflow-hidden rounded-[2px] bg-field">
                  <div
                    className="h-full rounded-[2px]"
                    style={{ width: `${(r.assigned / peak) * 100}%`, backgroundColor: CATEGORICAL[0] }}
                  />
                </div>
                <div className="h-2 overflow-hidden rounded-[2px] bg-field">
                  <div
                    className="h-full rounded-[2px]"
                    style={{ width: `${(r.completed / peak) * 100}%`, backgroundColor: CATEGORICAL[2] }}
                  />
                </div>
              </div>
            ),
          },
          { key: 'assigned', header: 'Assigned', align: 'right', sortable: true, value: (r) => r.assigned },
          { key: 'completed', header: 'Completed', align: 'right', sortable: true, value: (r) => r.completed },
          {
            key: 'avg', header: 'Avg hours / stage', align: 'right', sortable: true,
            value: (r) => r.avgHours,
            render: (r) => <span className="tnum">{r.avgHours || '—'}</span>,
          },
          {
            key: 'esc', header: 'Escalations', align: 'right', sortable: true,
            value: (r) => r.escalations,
            render: (r) => (
              <span className={cn('tnum', r.escalations > 0 ? 'font-medium text-stamp-700' : 'text-ink-400')}>
                {r.escalations}
              </span>
            ),
          },
          {
            key: 'missed', header: 'Missed updates', align: 'right', sortable: true,
            value: (r) => r.missed,
            render: (r) => (
              <span className={cn('tnum', r.missed > 0 ? 'font-medium text-amber-700' : 'text-ink-400')}>
                {r.missed}
              </span>
            ),
          },
        ]}
      />
      <p className="mt-3 flex items-center gap-4 text-micro text-ink-500">
        <span className="inline-flex items-center gap-1.5">
          <span className="h-2 w-3 rounded-[2px]" style={{ backgroundColor: CATEGORICAL[0] }} />
          Assigned
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span className="h-2 w-3 rounded-[2px]" style={{ backgroundColor: CATEGORICAL[2] }} />
          Completed
        </span>
      </p>
    </ChartFrame>
  )
}

/* ---------------------------------------------------------------- Source */

function SourceReport() {
  const store = useStore()
  const labels: Record<string, string> = {
    email: 'Email',
    whatsapp: 'WhatsApp',
    walk_in: 'Walk-in',
    referral: 'Referral',
    other: 'Other',
  }

  const bySource = Object.entries(labels).map(([key, label]) => ({
    source: label,
    count: store.candidates.filter((c) => c.source === key).length,
  }))

  const byAccount = store.mailAccounts.map((a) => ({
    source: a.address,
    count: store.candidates.filter((c) => c.sourceDetail === a.address).length,
  }))

  return (
    <ChartFrame
      title="Candidates by source"
      note="Where candidates actually come from, with each connected mail account counted separately."
    >
      <div className="grid gap-6 lg:grid-cols-2">
        <div>
          <ResponsiveContainer width="100%" height={240}>
            <BarChart data={bySource} layout="vertical" margin={{ left: 8, right: 32 }}>
              <CartesianGrid horizontal={false} stroke={GRID} />
              <XAxis type="number" tick={AXIS} axisLine={false} tickLine={false} allowDecimals={false} />
              <YAxis type="category" dataKey="source" tick={AXIS} axisLine={false} tickLine={false} width={80} />
              <Tooltip content={<ChartTooltip />} cursor={{ fill: 'rgba(17,28,46,0.04)' }} />
              <Bar
            isAnimationActive={false}
                dataKey="count"
                name="Candidates"
                fill={CATEGORICAL[0]}
                radius={[0, 4, 4, 0]}
                barSize={16}
                label={{ position: 'right', fontSize: 11, fill: '#4A5B73' }}
              />
            </BarChart>
          </ResponsiveContainer>
        </div>

        <div>
          <p className="mb-2 text-xs font-medium text-ink">By mail account</p>
          <ResponsiveContainer width="100%" height={240}>
            <BarChart data={byAccount} layout="vertical" margin={{ left: 8, right: 32 }}>
              <CartesianGrid horizontal={false} stroke={GRID} />
              <XAxis type="number" tick={AXIS} axisLine={false} tickLine={false} allowDecimals={false} />
              <YAxis type="category" dataKey="source" tick={AXIS} axisLine={false} tickLine={false} width={150} />
              <Tooltip content={<ChartTooltip />} cursor={{ fill: 'rgba(17,28,46,0.04)' }} />
              <Bar
            isAnimationActive={false}
                dataKey="count"
                name="Candidates"
                fill={CATEGORICAL[2]}
                radius={[0, 4, 4, 0]}
                barSize={16}
                label={{ position: 'right', fontSize: 11, fill: '#4A5B73' }}
              />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>
    </ChartFrame>
  )
}

/* ---------------------------------------------------------- Order status */

function OrderStatus() {
  const store = useStore()
  const data = store.orders
    .filter((o) => o.status !== 'closed')
    .map((o) => {
      const apps = store.applications.filter((a) => a.orderId === o.id)
      return {
        ref: o.ref,
        label: `${store.designation(o.designationId)?.title ?? ''}`,
        openings: o.openings,
        matched: apps.filter((a) => a.status === 'active').length,
        selected: apps.filter((a) => a.status === 'active' && a.currentStage > 4).length,
        deployed: apps.filter((a) => a.status === 'deployed').length,
      }
    })

  return (
    <ChartFrame
      title="Order status"
      note="Openings against what has actually been matched, selected and deployed. The ramp runs light to dark as candidates get closer to travelling."
    >
      <ResponsiveContainer width="100%" height={Math.max(280, data.length * 46)}>
        <BarChart data={data} layout="vertical" margin={{ left: 8, right: 24, top: 4 }} barGap={2}>
          <CartesianGrid horizontal={false} stroke={GRID} />
          <XAxis type="number" tick={AXIS} axisLine={false} tickLine={false} allowDecimals={false} />
          <YAxis type="category" dataKey="ref" tick={AXIS} axisLine={false} tickLine={false} width={110} />
          <Tooltip content={<ChartTooltip />} cursor={{ fill: 'rgba(17,28,46,0.04)' }} />
          <Legend
            verticalAlign="top"
            align="left"
            height={28}
            iconType="square"
            iconSize={9}
            wrapperStyle={{ fontSize: 11, color: '#4A5B73' }}
          />
          <Bar isAnimationActive={false} dataKey="openings" name="Openings" fill={SEQUENTIAL[0]} radius={[0, 3, 3, 0]} barSize={8} />
          <Bar isAnimationActive={false} dataKey="matched" name="Matched" fill={SEQUENTIAL[1]} radius={[0, 3, 3, 0]} barSize={8} />
          <Bar isAnimationActive={false} dataKey="selected" name="Selected" fill={SEQUENTIAL[2]} radius={[0, 3, 3, 0]} barSize={8} />
          <Bar isAnimationActive={false} dataKey="deployed" name="Deployed" fill={SEQUENTIAL[3]} radius={[0, 3, 3, 0]} barSize={8} />
        </BarChart>
      </ResponsiveContainer>
    </ChartFrame>
  )
}

/* ------------------------------------------------------------ Rejections */

function Rejections({ apps }: { apps: ReturnType<typeof useStore>['applications'] }) {
  const reasons = [
    { key: 'prescreen_failed', label: 'Prescreen failed', stage: 1 },
    { key: 'not_interested', label: 'Not interested', stage: 2 },
    { key: 'interview_rejected', label: 'Interview rejected', stage: 4 },
    { key: 'medical_unfit', label: 'Medically unfit', stage: 6 },
  ] as const

  const closed = apps.filter((a) => a.status === 'closed' && a.closureReason !== 'unmapped')

  const data = reasons.map((r, i) => ({
    reason: `${r.label}`,
    stage: `Stage ${r.stage}`,
    count: closed.filter((a) => a.closureReason === r.key).length,
    fill: CATEGORICAL[i],
  }))

  if (!closed.length)
    return (
      <EmptyState
        title="No applications closed in this period"
        description="Rejections appear here once a candidate fails prescreening, declines, is rejected at interview, or is found unfit."
      />
    )

  return (
    <ChartFrame
      title="Rejections by stage and reason"
      note="Only stages 1, 2, 4 and 6 can close an application, so every rejection lands in one of these four reasons."
    >
      <ResponsiveContainer width="100%" height={260}>
        <BarChart data={data} layout="vertical" margin={{ left: 8, right: 36 }}>
          <CartesianGrid horizontal={false} stroke={GRID} />
          <XAxis type="number" tick={AXIS} axisLine={false} tickLine={false} allowDecimals={false} />
          <YAxis type="category" dataKey="reason" tick={AXIS} axisLine={false} tickLine={false} width={130} />
          <Tooltip content={<ChartTooltip />} cursor={{ fill: 'rgba(17,28,46,0.04)' }} />
          <Bar
            isAnimationActive={false}
            dataKey="count"
            name="Closed applications"
            radius={[0, 4, 4, 0]}
            barSize={18}
            label={{ position: 'right', fontSize: 11, fill: '#4A5B73' }}
          >
            {data.map((d) => (
              <Cell key={d.reason} fill={d.fill} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>

      <ul className="mt-3 flex flex-wrap gap-4">
        {data.map((d) => (
          <li key={d.reason} className="inline-flex items-center gap-1.5 text-micro text-ink-500">
            <span className="h-2 w-3 rounded-[2px]" style={{ backgroundColor: d.fill }} />
            {d.reason} · {d.stage}
          </li>
        ))}
      </ul>
    </ChartFrame>
  )
}

/* -------------------------------------------------------------- Advance */

function Advance({ apps }: { apps: ReturnType<typeof useStore>['applications'] }) {
  const store = useStore()

  const collected = apps.flatMap((a) => {
    const rec = a.stages.find((s) => s.stage === 8)
    if (!rec) return []
    const amount = Number((rec.data as any).amount) || 0
    if (!amount) return []
    return [
      {
        candidate: store.candidate(a.candidateId)?.name ?? '—',
        order: store.order(a.orderId)?.ref ?? '—',
        amount,
        month: new Date(rec.completedAt).toLocaleDateString('en-GB', { month: 'short', year: '2-digit' }),
      },
    ]
  })

  const byMonth = Object.values(
    collected.reduce<Record<string, { month: string; amount: number }>>((acc, c) => {
      acc[c.month] = acc[c.month] ?? { month: c.month, amount: 0 }
      acc[c.month].amount += c.amount
      return acc
    }, {}),
  )

  const total = collected.reduce((s, c) => s + c.amount, 0)

  if (!collected.length)
    return (
      <EmptyState
        title="No advances collected in this period"
        description="Amounts appear here once stage 8 is completed with a receipt."
      />
    )

  return (
    <ChartFrame
      title="Advance collections"
      note="Collected at stage 8, per candidate and per month. Amounts are entered per candidate and vary."
    >
      <p className="mb-4 text-stat font-semibold text-ink tnum">{money(total, 'INR')}</p>

      <ResponsiveContainer width="100%" height={220}>
        <BarChart data={byMonth} margin={{ left: 8, right: 8, top: 4 }}>
          <CartesianGrid vertical={false} stroke={GRID} />
          <XAxis dataKey="month" tick={AXIS} axisLine={false} tickLine={false} />
          <YAxis tick={AXIS} axisLine={false} tickLine={false} width={64} />
          <Tooltip content={<ChartTooltip />} cursor={{ fill: 'rgba(17,28,46,0.04)' }} />
          <Bar isAnimationActive={false} dataKey="amount" name="Collected" fill={CATEGORICAL[0]} radius={[4, 4, 0, 0]} barSize={34} />
        </BarChart>
      </ResponsiveContainer>

      <div className="mt-5">
        <DataTable
          rows={collected}
          rowKey={(c) => `${c.candidate}-${c.order}-${c.amount}`}
          pageSize={10}
          columns={[
            { key: 'candidate', header: 'Candidate', sortable: true, value: (c) => c.candidate },
            {
              key: 'order',
              header: 'Order',
              sortable: true,
              filterable: true,
              value: (c) => c.order,
              render: (c) => <span className="text-xs text-ink-500 tnum">{c.order}</span>,
            },
            {
              key: 'month',
              header: 'Month',
              filterable: true,
              value: (c) => c.month,
              render: (c) => <span className="text-xs text-ink-500">{c.month}</span>,
            },
            {
              key: 'amount',
              header: 'Amount',
              align: 'right',
              sortable: true,
              value: (c) => c.amount,
              render: (c) => <span className="tnum text-ink">{money(c.amount, 'INR')}</span>,
            },
          ]}
        />
      </div>
    </ChartFrame>
  )
}
