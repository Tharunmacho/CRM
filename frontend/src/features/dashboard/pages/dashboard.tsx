import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ArrowUpRight, BriefcaseBusiness, CalendarClock, CheckCheck, Clock3, GitBranch, Mail, MessageCircle, RefreshCw, Users } from 'lucide-react'
import { useStore } from '@/shared/state/crm-store'
import { Button, Field, Select } from '@/shared/ui/primitives'
import { PageHeader, Panel, PanelHeader } from '@/shared/ui/display'
import { SummaryCards } from '@/shared/ui/workspace'
import { DataTable, type Column } from '@/shared/ui/table'
import { Dialog } from '@/shared/ui/overlay'
import { StageModal } from '@/features/workflow/components/stage-modal'
import { dateTimeLabel } from '@/shared/lib/format'
import { useReportData, type DashboardResult, type DashboardTask, type OrderMetrics } from '@/features/reports/report-api'
import { SourceHealth } from '../components/source-health'

type TaskFilter = 'open' | 'dueToday' | 'overdue' | 'escalated' | 'notUpdated'
const FILTERS: { key: TaskFilter; label: string }[] = [
  { key: 'open', label: 'Open tasks' }, { key: 'dueToday', label: 'Due today' },
  { key: 'overdue', label: 'Overdue' }, { key: 'escalated', label: 'Escalated' }, { key: 'notUpdated', label: 'Not updated today' },
]

export default function DashboardPage() {
  const store = useStore()
  const navigate = useNavigate()
  const [staff, setStaff] = useState('')
  const [stage, setStage] = useState<number | null>(null)
  const [openApp, setOpenApp] = useState<string | null>(null)
  const [taskFilter, setTaskFilter] = useState<TaskFilter>('open')
  const [todoError, setTodoError] = useState('')
  const [pendingTodo, setPendingTodo] = useState('')
  const { data, error, refresh } = useReportData<DashboardResult>(`/dashboard${staff ? `?staff_id=${encodeURIComponent(staff)}` : ''}`, store.me.id)
  const staffOptions = store.me.role === 'admin' ? store.users : []
  const stageData = data?.pipeline.find(row => row.stage === stage)
  const todos = store.todos.filter(todo => todo.ownerId === store.me.id && !todo.done).sort((a, b) => +new Date(a.dueAt) - +new Date(b.dueAt))
  const taskColumns: Column<DashboardTask>[] = [
    { key: 'candidate', header: 'Candidate / task', value: row => row.candidate, render: row => <div><p className="font-medium text-ink">{row.candidate}</p><p className="mt-1 text-xs text-ink-500">{row.title}</p></div> },
    { key: 'stage', header: 'Stage', value: row => row.stage ?? 'Initial call' },
    { key: 'owner', header: 'Assigned to', value: row => row.owner },
    { key: 'due', header: 'Due at', value: row => row.dueAt, render: row => <span className={row.overdue ? 'font-medium text-stamp-600' : 'text-ink-600'}>{dateTimeLabel(row.dueAt)}{row.overdue && <span className="block text-xs">Overdue</span>}</span> },
    { key: 'updates', header: 'Daily update', value: row => row.notUpdated ? 'Not updated today' : 'Updated today', render: row => <span className={row.notUpdated ? 'text-amber-700' : 'text-ink-500'}>{row.notUpdated ? 'Not updated today' : 'Updated today'}</span> },
  ]
  const orderColumns: Column<OrderMetrics>[] = [
    { key: 'ref', header: 'Order', value: row => row.ref, render: row => <div><p className="font-medium text-navy-700">{row.ref}</p><p className="mt-1 text-xs text-ink-500">{row.company}</p></div> },
    { key: 'client', header: 'Client', value: row => row.client },
    { key: 'designation', header: 'Designation', value: row => row.designation },
    ...(['openings', 'matched', 'selected', 'deployed'] as const).map(key => ({ key, header: key[0].toUpperCase() + key.slice(1), value: (row: OrderMetrics) => row[key], align: 'right' as const })),
  ]
  function openTask(task: DashboardTask) { if (task.applicationId) setOpenApp(task.applicationId); else navigate(`/candidates/${task.candidateId}`) }
  async function finishTodo(id: string) {
    setPendingTodo(id); setTodoError('')
    try { await store.toggleTodo(id) } catch (error) { setTodoError(error instanceof Error ? error.message : 'Unable to complete this to-do.') } finally { setPendingTodo('') }
  }
  return <div className="space-y-5">
    <PageHeader title="Dashboard" description={`Welcome, ${store.me.name.split(' ')[0]}. Here is your recruitment desk for today.`} actions={<div className="flex items-end gap-2">
      {store.me.role === 'admin' && <Field label="Team view"><Select value={staff} onChange={e => { setStaff(e.target.value); setStage(null) }} className="min-w-40"><option value="">Whole team</option>{staffOptions.map(user => <option key={user.id} value={user.id}>{user.name}</option>)}</Select></Field>}
      <Button icon={<RefreshCw size={15} />} onClick={refresh}>Refresh</Button>
    </div>} />
    {error && <p role="alert" className="rounded-panel bg-stamp-50 p-4 text-sm text-stamp-700">{error}{data && ' Showing the last loaded result.'}</p>}
    {!data && !error && <Panel><p role="status" className="text-sm text-ink-500">Loading your dashboard…</p></Panel>}
    {data && <>
      <SummaryCards items={[
        { label: 'Candidates today', value: data.today.total, detail: `${data.today.walk_in + data.today.referral + data.today.other} from walk-in, referral or other`, icon: Users },
        { label: 'Email intake today', value: data.today.email, detail: 'Saved candidates from email', icon: Mail, tone: 'violet' },
        { label: 'WhatsApp intake today', value: data.today.whatsapp, detail: 'Saved candidates marked WhatsApp', icon: MessageCircle, tone: 'green' },
        { label: 'Active applications', value: data.pipeline.reduce((sum, item) => sum + item.count, 0), detail: 'Across eleven recruitment stages', icon: GitBranch, tone: 'amber' },
      ]} />
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">{FILTERS.map(filter => <button key={filter.key} onClick={() => { setTaskFilter(filter.key); document.getElementById('dashboard-workload')?.scrollIntoView({ behavior: 'smooth', block: 'start' }) }} className={`rounded-panel border p-4 text-left shadow-soft transition-colors ${taskFilter === filter.key ? 'border-navy-300 bg-navy-50' : 'border-rule bg-white hover:border-navy-200'}`}><span className="text-xs font-medium text-ink-500">{filter.label}</span><strong className={`mt-2 block text-2xl font-semibold tnum ${filter.key === 'overdue' && data.taskCounts.overdue ? 'text-stamp-600' : 'text-ink'}`}>{data.taskCounts[filter.key]}</strong></button>)}</div>
      <Panel flush><PanelHeader title="Recruitment pipeline" meta="Active candidates at each stage. Select a stage to view its candidates." action={<GitBranch className="text-navy-500" size={18} />} />
        <div className="grid grid-cols-2 gap-3 p-4 sm:grid-cols-3 xl:grid-cols-6">{data.pipeline.map(row => <button key={row.stage} onClick={() => setStage(row.stage)} className="group rounded-panel border border-rule bg-field p-4 text-left transition-colors hover:border-navy-300 hover:bg-navy-50"><span className="flex items-center justify-between text-xs text-ink-400">Stage {row.stage}<ArrowUpRight size={13} /></span><strong className="mt-2 block text-2xl font-semibold text-ink tnum">{row.count}</strong><span className="mt-1 block text-xs font-medium text-ink-600">{row.label}</span></button>)}</div>
      </Panel>
      <Panel flush id="dashboard-workload"><PanelHeader title={FILTERS.find(filter => filter.key === taskFilter)!.label} meta="Current workload for the selected scope, ordered by due time." action={<Button size="sm" onClick={() => navigate('/tasks')}>Tasks <ArrowUpRight size={13} /></Button>} />
        <DataTable rows={data.tasks.filter(task => taskFilter === 'open' || task[taskFilter])} columns={taskColumns} rowKey={row => row.id} pageSize={8} onRowClick={openTask} />
      </Panel>
      <Panel flush><PanelHeader title="Active orders" meta="Open orders, total openings and applications in the selected scope." action={<Button size="sm" onClick={() => navigate('/jobs')} icon={<BriefcaseBusiness size={14} />}>Jobs / positions</Button>} /><DataTable rows={data.orders} columns={orderColumns} rowKey={row => row.id} pageSize={8} onRowClick={row => navigate(`/orders/${row.id}`)} /></Panel>
      <div className="grid items-start gap-5 xl:grid-cols-2">
        <Panel flush><PanelHeader title="My tasks" meta="Your own open tasks, even when viewing another staff member." action={<Clock3 size={17} className="text-navy-500" />} /><DataTable rows={data.myTasks} columns={taskColumns.filter(column => column.key !== 'owner')} rowKey={row => row.id} pageSize={5} onRowClick={openTask} /></Panel>
        {store.me.role !== 'staff' && <Panel flush><PanelHeader title="My to-dos" meta="Your private reminders" action={<Button size="sm" onClick={() => navigate('/todo')} icon={<CalendarClock size={14} />}>View all</Button>} />
          {todoError && <p role="alert" className="px-5 pt-3 text-sm text-stamp-600">{todoError}</p>}
          {todos.length ? <ul className="divide-y divide-rule">{todos.slice(0, 5).map(todo => <li key={todo.id} className="flex gap-3 p-4"><button onClick={() => void finishTodo(todo.id)} disabled={!!pendingTodo} aria-label={`Complete ${todo.title}`} className="mt-0.5 text-ink-400 hover:text-navy-600 disabled:opacity-40"><CheckCheck size={19} /></button><div className="min-w-0"><p className="text-sm font-medium text-ink">{todo.title}</p><p className={`mt-1 text-xs ${+new Date(todo.dueAt) < Date.now() ? 'text-stamp-600' : 'text-ink-500'}`}>{dateTimeLabel(todo.dueAt)} · {todo.priority}</p>{todo.linkTo && <button onClick={() => navigate(todo.linkTo!)} className="mt-2 text-xs text-navy-600 underline">{todo.linkLabel || 'Open linked record'}</button>}</div></li>)}</ul> : <p className="p-5 text-sm text-ink-500">No open to-dos.</p>}
        </Panel>}
        {store.me.role === 'admin' && <SourceHealth />}
      </div>
      <p className="text-xs text-ink-400">Today is {data.date} in {data.timezone}. Updated {new Date(data.generatedAt).toLocaleTimeString()}. Escalated counts reflect recorded escalations.</p>
      <Dialog open={stage !== null} onClose={() => setStage(null)} title={stageData ? `Stage ${stageData.stage} · ${stageData.label}` : 'Pipeline candidates'} description="Active applications in the selected dashboard scope." width="lg">
        <DataTable rows={stageData?.applications ?? []} rowKey={row => row.id} columns={[{ key: 'candidate', header: 'Candidate', value: row => row.candidate }, { key: 'order', header: 'Order', value: row => row.order }]} onRowClick={row => { setStage(null); setOpenApp(row.id) }} pageSize={10} />
      </Dialog>
    </>}
    {openApp && <StageModal applicationId={openApp} open onClose={() => { setOpenApp(null); refresh() }} />}
  </div>
}
