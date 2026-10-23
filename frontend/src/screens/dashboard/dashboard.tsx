import { useState, type ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { ArrowDownToLine, ArrowUpRight, BriefcaseBusiness, CalendarDays, Check, CheckCheck, ChevronRight, Clock3, GitBranch, Mail, MessageCircle, Plus, RefreshCw, Users } from 'lucide-react'
import { useStore } from '@/shared/state/crm-store'
import { Select } from '@/shared/ui/primitives'
import { DataTable, type Column } from '@/shared/ui/table'
import { Dialog } from '@/shared/ui/overlay'
import { StageModal } from '@/features/workflow/components/stage-modal'
import { dateTimeLabel, initials } from '@/shared/lib/format'
import { useReportData, type DashboardResult, type DashboardTask, type OrderMetrics } from '@/features/reports/report-api'
import { SourceHealth } from '@/features/dashboard/components/source-health'
import { NeedsAttention } from '@/features/dashboard/components/needs-attention'
import '@/styles/dashboard-page.css'

type TaskFilter = 'open' | 'dueToday' | 'overdue' | 'escalated' | 'notUpdated'
const FILTERS: { key: TaskFilter; label: string }[] = [
  { key: 'open', label: 'All open' }, { key: 'dueToday', label: 'Due today' },
  { key: 'overdue', label: 'Overdue' }, { key: 'escalated', label: 'Escalated' }, { key: 'notUpdated', label: 'Not updated today' },
]
const number = (value: number) => value.toLocaleString()
function CardHeading({ title, subtitle, action }: { title: string; subtitle?: string; action?: ReactNode }) {
  return <header className="dash-card-heading"><div><h2>{title}</h2>{subtitle && <p>{subtitle}</p>}</div>{action}</header>
}
function EmptyContent({ title, detail, icon: Icon = BriefcaseBusiness }: { title: string; detail: string; icon?: typeof Users }) {
  return <div className="dash-empty"><span><Icon size={22} strokeWidth={1.5} /></span><strong>{title}</strong><p>{detail}</p></div>
}
function FulfillmentGauge({ deployed, openings, scoped }: { deployed: number; openings: number; scoped: boolean }) {
  const progress = openings ? Math.min(100, Math.round(deployed / openings * 100)) : 0
  const detail = openings ? scoped ? `${number(deployed)} scope deployments · ${number(openings)} total openings` : `${number(deployed)} of ${number(openings)} openings filled` : 'No active openings yet'
  return <div className="dash-gauge" role="img" aria-label={`${openings ? `${progress}%. ` : ''}${detail}`}>
    <svg viewBox="0 0 260 145" aria-hidden="true">{Array.from({ length: 36 }, (_, index) => {
      const angle = Math.PI + index / 35 * Math.PI
      return <line key={index} x1={130 + Math.cos(angle) * 98} y1={123 + Math.sin(angle) * 98} x2={130 + Math.cos(angle) * 116} y2={123 + Math.sin(angle) * 116} stroke={index < progress / 100 * 36 ? '#4fbe92' : '#edf0f3'} strokeWidth="5" />
    })}</svg>
    <div><strong>{openings ? `${progress}%` : '—'}</strong></div>
    <p className="dash-gauge-caption">{detail}</p>
  </div>
}

/** Charts and tables share the same live, permission-scoped dashboard result. */
export default function DashboardPage() {
  const store = useStore()
  const navigate = useNavigate()
  const [staff, setStaff] = useState('')
  const [stage, setStage] = useState<number | null>(null)
  const [openApp, setOpenApp] = useState<string | null>(null)
  const [taskFilter, setTaskFilter] = useState<TaskFilter>('open')
  const [todoError, setTodoError] = useState('')
  const [pendingTodo, setPendingTodo] = useState('')
  const { data, error, refresh, refreshing } = useReportData<DashboardResult>(`/dashboard${staff ? `?staff_id=${encodeURIComponent(staff)}` : ''}`, store.me.id)
  const stageData = data?.pipeline.find(row => row.stage === stage)
  const active = data?.pipeline.reduce((sum, item) => sum + item.count, 0) ?? 0
  const openings = data?.orders.reduce((sum, item) => sum + item.openings, 0) ?? 0
  const deployed = data?.orders.reduce((sum, item) => sum + item.deployed, 0) ?? 0
  const todos = store.todos.filter(todo => todo.ownerId === store.me.id && !todo.done).sort((a, b) => +new Date(a.dueAt) - +new Date(b.dueAt))
  const channels = data ? [
    { label: 'Email', count: data.today.email, color: '#3976f6' }, { label: 'WhatsApp', count: data.today.whatsapp, color: '#54bd96' },
    { label: 'Walk-in', count: data.today.walk_in, color: '#9a85e8' }, { label: 'Referral', count: data.today.referral, color: '#f3b46a' },
    { label: 'Other', count: data.today.other, color: '#a9b5c7' },
  ] : []
  const sourceMax = Math.max(1, ...channels.map(source => source.count))
  const taskColumns: Column<DashboardTask>[] = [
    { key: 'candidate', header: 'CANDIDATE / TASK', value: row => row.candidate, render: row => <div className="dash-person"><span className="dash-person-avatar">{initials(row.candidate)}</span><div><strong>{row.candidate}</strong><span>{row.title}</span></div></div> },
    { key: 'stage', header: 'STAGE', value: row => row.stage ?? 'Initial call', render: row => <span className="dash-tag">{row.stage ? `Stage ${row.stage}` : 'Initial call'}</span> },
    { key: 'owner', header: 'ASSIGNED TO', value: row => row.owner },
    { key: 'due', header: 'DUE DATE', value: row => row.dueAt, render: row => <span className={row.overdue ? 'dash-overdue' : ''}>{dateTimeLabel(row.dueAt)}{row.overdue && <small>Overdue</small>}</span> },
    { key: 'updates', header: 'DAILY UPDATE', value: row => row.notUpdated ? 'Pending' : 'Updated', render: row => <span className={`dash-status ${row.notUpdated ? 'is-pending' : 'is-complete'}`}>{row.notUpdated ? <Clock3 size={11} /> : <Check size={11} />}{row.notUpdated ? 'Pending' : 'Updated'}</span> },
  ]
  const orderColumns: Column<OrderMetrics>[] = [
    { key: 'ref', header: 'ORDER', value: row => row.ref, render: row => <div className="dash-order"><span><BriefcaseBusiness size={16} /></span><div><strong>{row.ref}</strong><small>{row.company}</small></div></div> },
    { key: 'designation', header: 'POSITION', value: row => row.designation, render: row => <div className="dash-table-copy"><strong>{row.designation}</strong><small>{row.client}</small></div> },
    ...(['openings', 'matched', 'selected'] as const).map(key => ({ key, header: key.toUpperCase(), value: (row: OrderMetrics) => row[key], align: 'right' as const })),
    { key: 'deployed', header: 'DEPLOYED', value: row => row.deployed, align: 'right', render: row => <span className="dash-deployed">{number(row.deployed)}</span> },
  ]
  function openTask(task: DashboardTask) { if (task.applicationId) setOpenApp(task.applicationId); else navigate(`/candidates/${task.candidateId}`) }
  async function finishTodo(id: string) {
    setPendingTodo(id); setTodoError('')
    try { await store.toggleTodo(id) } catch (error) { setTodoError(error instanceof Error ? error.message : 'Unable to complete this to-do.') } finally { setPendingTodo('') }
  }
  function exportOverview() {
    if (!data) return
    const rows = [
      ['Date', data.date], ['Timezone', data.timezone], ['Scope', data.staffId ? data.staffOptions.find(option => option.id === data.staffId)?.label ?? store.user(data.staffId)?.name ?? data.staffId : 'Whole team'],
      ['Candidates today', data.today.total], ['Email intake', data.today.email], ['WhatsApp intake', data.today.whatsapp],
      ['Active applications', active], ['Open tasks', data.taskCounts.open], ['Due today', data.taskCounts.dueToday],
      ['Overdue tasks', data.taskCounts.overdue], ['Escalated tasks', data.taskCounts.escalated],
      ...data.pipeline.map(row => [`Stage ${row.stage}: ${row.label}`, row.count]),
    ]
    const csv = rows.map(row => row.map(value => {
      const text = String(value)
      return `"${(/^[=+@\-\t\r]/.test(text) ? `'${text}` : text).replaceAll('"', '""')}"`
    }).join(',')).join('\r\n')
    const url = URL.createObjectURL(new Blob(['\ufeff', csv], { type: 'text/csv;charset=utf-8' }))
    const link = document.createElement('a')
    link.href = url; link.download = `recruitment-overview-${data.date}.csv`
    document.body.appendChild(link); link.click(); link.remove()
    window.setTimeout(() => URL.revokeObjectURL(url), 1000)
  }
  return <div className="dashboard-refresh dash-v2">
    <header className="dash-page-heading">
      <div><h1>Dashboard</h1><p>Welcome back, {store.me.name.split(' ')[0]}.</p></div>
      <div className="dash-page-actions">
        {data && <span className="dash-date"><CalendarDays size={14} />{new Date(`${data.date}T12:00:00`).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' })}<span>Today</span></span>}
        {store.me.role === 'admin' && <div className="dash-team-select"><Select aria-label="Team view" value={staff} onChange={e => { setStaff(e.target.value); setStage(null) }}><option value="">Whole team</option>{(data?.staffOptions ?? store.users.map(user => ({ id: user.id, label: user.name }))).map(user => <option key={user.id} value={user.id}>{user.label}</option>)}</Select></div>}
        <button className="dash-button dash-refresh-button" aria-label="Refresh dashboard" title="Refresh dashboard" disabled={refreshing} onClick={refresh}><RefreshCw size={15} /></button>
        {store.can('export') && <button className="dash-button" onClick={exportOverview} disabled={!data}><ArrowDownToLine size={14} />Export</button>}
        <button className="dash-button is-primary" onClick={() => navigate('/add-candidate')}><Plus size={15} />Add candidate</button>
      </div>
    </header>
    {error && <div role="alert" className="dash-error">{error}{data && ' Showing the last loaded result.'}<button onClick={refresh}>Try again</button></div>}
    {!data && !error && <div className="dash-loading" role="status"><RefreshCw size={18} />Loading your dashboard…</div>}
    {data && <>
      <section className="dash-stats" aria-label="Recruitment summary">{[
        { label: 'Candidates today', value: data.today.total, detail: 'Added to the talent pool', icon: Users, to: '/data-bank', tone: 'blue' },
        { label: 'Email intake', value: data.today.email, detail: 'Saved from email today', icon: Mail, to: '/inbox', tone: 'violet' },
        { label: 'WhatsApp intake', value: data.today.whatsapp, detail: 'Saved from WhatsApp today', icon: MessageCircle, to: '/whatsapp', tone: 'green' },
        { label: 'Active applications', value: active, detail: 'Across the 11 recruitment stages', icon: GitBranch, to: '/mapped', tone: 'amber' },
      ].map(item => <button className="dash-stat" key={item.label} onClick={() => navigate(item.to)}><span className="dash-stat-title">{item.label}<span className={`dash-stat-icon tone-${item.tone}`}><item.icon size={17} strokeWidth={1.8} /></span></span><span className="dash-stat-value">{number(item.value)}</span><span className="dash-stat-detail">{item.detail}<ArrowUpRight size={13} /></span></button>)}</section>
      <div className="dash-overview-grid">
        <div className="dash-primary-column">
          <section className="dash-card dash-pipeline">
            <CardHeading title="Recruitment pipeline" action={<span className="dash-live-label"><i />Current overview</span>} />
            <div className="dash-pipeline-visual">
              <div className="dash-pipeline-total"><span>Active applications</span><strong>{number(active)}</strong><p><span className="dash-status is-complete"><BriefcaseBusiness size={11} />{number(data.orders.length)} open orders</span></p></div>
              <div className="dash-pipeline-chart" role="img" aria-label={`Active applications by stage. ${data.pipeline.map(row => `Stage ${row.stage}: ${row.count}`).join('. ')}`}>
                <ResponsiveContainer width="100%" height="100%"><AreaChart data={data.pipeline} margin={{ top: 16, right: 12, bottom: 4, left: 0 }} accessibilityLayer>
                  <defs><linearGradient id="recruitment-fill" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#4c83f8" stopOpacity={0.2} /><stop offset="100%" stopColor="#4c83f8" stopOpacity={0.01} /></linearGradient></defs>
                  <CartesianGrid strokeDasharray="4 5" vertical={false} stroke="#edf0f5" />
                  <XAxis dataKey="stage" tickFormatter={value => `S${value}`} axisLine={false} tickLine={false} tick={{ fill: '#66758a', fontSize: 12 }} tickMargin={12} minTickGap={10} interval="preserveStartEnd" />
                  <YAxis width={38} allowDecimals={false} axisLine={false} tickLine={false} tick={{ fill: '#66758a', fontSize: 12 }} domain={[0, (max: number) => Math.max(4, max)]} />
                  <Tooltip labelFormatter={value => `Stage ${value}`} formatter={(value: number) => [value, 'Active applications']} contentStyle={{ borderRadius: 10, border: '1px solid #edf0f5', fontSize: 13 }} />
                  <Area type="linear" dataKey="count" stroke="#3976f6" strokeWidth={2} fill="url(#recruitment-fill)" isAnimationActive={false} activeDot={{ r: 4 }} />
                </AreaChart></ResponsiveContainer>
              </div>
            </div>
            <div className="dash-stage-strip" aria-label="Explore recruitment stages">{data.pipeline.map(row => <button key={row.stage} aria-label={`Stage ${row.stage}: ${row.label}, ${row.count} active applications`} title={row.label} onClick={() => setStage(row.stage)}><span>S{row.stage}</span><strong>{number(row.count)}</strong></button>)}</div>
            <footer className="dash-card-foot"><button onClick={() => navigate('/mapped')}>View pipeline<ArrowUpRight size={13} /></button></footer>
          </section>
          <section className="dash-card dash-orders"><CardHeading title="Active orders" action={<button className="dash-text-link" onClick={() => navigate('/jobs')}>View all<ChevronRight size={13} /></button>} /><DataTable rows={data.orders} columns={orderColumns} rowKey={row => row.id} pageSize={5} onRowClick={row => navigate(`/orders/${row.id}`)} empty={<EmptyContent title="Ready for your next placement" detail="Open orders and their recruitment progress will appear here." />} /></section>
          <section className="dash-card dash-workload" id="dashboard-workload">
            <CardHeading title="Task overview" action={<button className="dash-text-link" onClick={() => navigate('/tasks')}>View tasks<ChevronRight size={13} /></button>} />
            <div className="dash-task-tabs" role="group" aria-label="Filter tasks">{FILTERS.map(filter => <button key={filter.key} aria-pressed={taskFilter === filter.key} className={taskFilter === filter.key ? 'is-active' : ''} onClick={() => setTaskFilter(filter.key)}>{filter.label}<span className={filter.key === 'overdue' && data.taskCounts.overdue ? 'is-danger' : ''}>{number(data.taskCounts[filter.key])}</span></button>)}</div>
            <DataTable rows={data.tasks.filter(task => taskFilter === 'open' || task[taskFilter])} columns={taskColumns} rowKey={row => row.id} pageSize={6} resetPageKey={`${staff}:${taskFilter}`} onRowClick={openTask} empty={<EmptyContent icon={CheckCheck} title={taskFilter === 'open' ? 'You’re all caught up' : 'No tasks in this view'} detail={taskFilter === 'open' ? 'New recruitment tasks will appear here when there’s work to do.' : 'Choose another filter to see more of your workload.'} />} />
          </section>
        </div>
        <aside className="dash-secondary-column" aria-label="Recruitment insights">
          <section className="dash-card dash-sources"><CardHeading title="Candidate sources" action={<span className="dash-period">Today</span>} /><div className="dash-source-bars">{channels.map(source => <div className="dash-source-column" key={source.label}><strong>{number(source.count)}</strong><div className="dash-source-track"><span style={{ height: `${source.count / sourceMax * 100}%`, background: source.color }} /></div><span>{source.label}</span></div>)}</div><p className="dash-source-caption">{data.today.total ? `${number(data.today.total)} candidates added today` : 'No new candidates today'}</p></section>
          <section className="dash-card dash-fulfillment"><CardHeading title={data.staffId ? 'Placement contribution' : 'Order fulfillment'} action={<BriefcaseBusiness size={15} className="dash-muted-icon" />} /><FulfillmentGauge deployed={deployed} openings={openings} scoped={!!data.staffId} /><button className="dash-small-button" onClick={() => navigate('/jobs')}>View orders<ArrowUpRight size={12} /></button></section>
          {store.me.role !== 'staff' && <section className="dash-card dash-reminders">
            <CardHeading title="My to-dos" action={<button className="dash-text-link" onClick={() => navigate('/todo')}>View all<ChevronRight size={13} /></button>} />
            {todoError && <p role="alert" className="dash-error">{todoError}</p>}
            {todos.length ? <ul>{todos.slice(0, 4).map(todo => <li key={todo.id}><button className="dash-todo-check" onClick={() => void finishTodo(todo.id)} disabled={!!pendingTodo} aria-label={`Complete ${todo.title}`}>{pendingTodo === todo.id ? <RefreshCw size={13} /> : <Check size={13} />}</button><div><strong>{todo.title}</strong><span className={+new Date(todo.dueAt) < Date.now() ? 'dash-overdue' : ''}>{dateTimeLabel(todo.dueAt)}</span>{todo.linkTo && <button className="dash-text-link" onClick={() => navigate(todo.linkTo!)}>{todo.linkLabel || 'Open linked record'}<ArrowUpRight size={11} /></button>}</div></li>)}</ul> : <div className="dash-small-empty"><CheckCheck size={19} /><span>No open reminders.<small>A little space to focus on what’s next.</small></span></div>}
          </section>}
        </aside>
      </div>
      <section className="dash-card dash-own-tasks"><CardHeading title="My assigned tasks" action={<Clock3 size={16} className="dash-muted-icon" />} /><DataTable rows={data.myTasks} columns={taskColumns.filter(column => column.key !== 'owner')} rowKey={row => row.id} pageSize={5} onRowClick={openTask} empty={<EmptyContent icon={CheckCheck} title="No tasks assigned to you" detail="Your personal follow-ups will appear here." />} /></section>
      {store.me.role !== 'staff' && <div className="dash-connected-sources"><NeedsAttention /></div>}
      {store.me.role === 'admin' && <div className="dash-connected-sources"><SourceHealth /></div>}
      <footer className="dash-page-foot"><span><i />Updated {new Date(data.generatedAt).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span><span>Today’s activity · {data.timezone}</span></footer>
      <Dialog open={stage !== null} onClose={() => setStage(null)} title={stageData ? `Stage ${stageData.stage} · ${stageData.label}` : 'Pipeline candidates'} description="Active applications in the selected dashboard scope." width="lg"><DataTable rows={stageData?.applications ?? []} rowKey={row => row.id} columns={[{ key: 'candidate', header: 'Candidate', value: row => row.candidate }, { key: 'order', header: 'Order', value: row => row.order }]} onRowClick={row => { setStage(null); setOpenApp(row.id) }} pageSize={10} /></Dialog>
    </>}
    {openApp && <StageModal applicationId={openApp} open onClose={() => { setOpenApp(null); refresh() }} />}
  </div>
}
