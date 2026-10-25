import { useMemo, useState, type ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import { AlertTriangle, ArrowDownToLine, ArrowRight, ArrowUpRight, BriefcaseBusiness, CalendarClock, Check, CheckCheck, ChevronRight, Clock3, FileSearch, ListChecks, Mail, Plus, RefreshCw, Search, UserPlus, Users } from 'lucide-react'
import { useStore } from '@/shared/state/crm-store'
import { Select } from '@/shared/ui/primitives'
import { DataTable, type Column } from '@/shared/ui/table'
import { Dialog } from '@/shared/ui/overlay'
import { StageModal } from '@/features/workflow/components/stage-modal'
import { dateLabel, dateTimeLabel } from '@/shared/lib/format'
import { useReportData, type DashboardResult, type DashboardTask, type OrderMetrics } from '@/features/reports/report-api'
import { SourceHealth } from '@/features/dashboard/components/source-health'
import { NeedsAttention } from '@/features/dashboard/components/needs-attention'
import { IntakeBlock, IntakeSkeleton, SOURCE_KEYS, number, type IntakeOverview } from '@/features/dashboard/components/intake-panel'
import { CandidateCards, roleLine, OrderCards, TaskCards, useNarrow } from '@/features/dashboard/components/stacked'
import { Avatar, Flag, Pill, SlaMeter, SourceTag, StageMini } from '@/features/dashboard/components/identity'
import { AttentionList, type AttentionItem } from '@/features/dashboard/components/recent-arrivals'
import { PERIODS, arrivalGrid, inWindow, intakeBuckets, isVerified, periodStart, recentDays, type Period } from '@/features/dashboard/metrics'
import '@/styles/dashboard-ref.css'

type TaskFilter = 'open' | 'dueToday' | 'overdue' | 'escalated' | 'notUpdated'
const FILTERS: { key: TaskFilter; label: string; hint?: string }[] = [
  { key: 'open', label: 'All open' }, { key: 'dueToday', label: 'Due today', hint: 'Tasks that must be finished today, before their 48-hour limit ends' },
  { key: 'overdue', label: 'Overdue', hint: 'Tasks past their 48-hour limit' }, { key: 'escalated', label: 'Escalated', hint: 'Past the 48-hour limit and reported to the admin and managers' }, { key: 'notUpdated', label: 'Not updated today', hint: 'Open tasks with no remark added today' },
]
const STATUS: Record<string, { label: string; tone: 'info' | 'warn' | 'good' | 'bad' | 'muted' | 'accent' }> = {
  new: { label: 'New', tone: 'info' }, in_process: { label: 'In process', tone: 'accent' }, needs_review: { label: 'Needs review', tone: 'warn' },
  deployed: { label: 'Deployed', tone: 'good' }, not_interested: { label: 'Not interested', tone: 'muted' },
}

function CardHeading({ title, subtitle, action }: { title: string; subtitle?: string; action?: ReactNode }) {
  return <header className="dr-card-head"><div><h2>{title}</h2>{subtitle && <p>{subtitle}</p>}</div>{action}</header>
}
function EmptyContent({ title, detail, icon: Icon = BriefcaseBusiness }: { title: string; detail: string; icon?: typeof Users }) {
  return <div className="dr-empty"><span><Icon size={20} strokeWidth={1.5} /></span><strong>{title}</strong><p>{detail}</p></div>
}
function DashboardSkeleton() {
  return <div className="dr-skeleton"><IntakeSkeleton /></div>
}

/** Charts and tables share the same live, permission-scoped dashboard result. */
export default function DashboardPage() {
  const store = useStore()
  const navigate = useNavigate()
  const [staff, setStaff] = useState('')
  const [period, setPeriod] = useState<Period>('year')
  const [query, setQuery] = useState('')
  const [stage, setStage] = useState<number | null>(null)
  const [openApp, setOpenApp] = useState<string | null>(null)
  const [openStage, setOpenStage] = useState<number | undefined>()
  const [taskFilter, setTaskFilter] = useState<TaskFilter>('open')
  const [todoError, setTodoError] = useState('')
  const [pendingTodo, setPendingTodo] = useState('')
  const { data, error, refresh, refreshing } = useReportData<DashboardResult>(`/dashboard${staff ? `?staff_id=${encodeURIComponent(staff)}` : ''}`, store.me.id)
  const narrow = useNarrow()
  const isStaff = store.me.role === 'staff'
  const isAdmin = store.me.role === 'admin'
  // Admin and manager see the whole team (and can pick one person); staff see only their own work.
  const isTeam = store.me.role === 'admin' || store.me.role === 'manager'
  const stageData = data?.pipeline.find(row => row.stage === stage)
  // Staff see their own numbers; admin and managers see the team, or the person chosen in the filter.
  // The server scopes everyone except the admin to their own work, so only an admin without a filter sees the team.
  const scopeLabel = (noun: string) => !isTeam ? `My ${noun}` : staff ? `${noun[0].toUpperCase()}${noun.slice(1)} · ${store.user(staff)?.name.split(' ')[0] ?? 'selected'}` : `Team ${noun}`
  const active = data?.pipeline.reduce((sum, item) => sum + item.count, 0) ?? 0
  const openings = data?.orders.reduce((sum, item) => sum + item.openings, 0) ?? 0
  const deployed = data?.orders.reduce((sum, item) => sum + item.deployed, 0) ?? 0
  const todos = store.todos.filter(todo => todo.ownerId === store.me.id && !todo.done).sort((a, b) => +new Date(a.dueAt) - +new Date(b.dueAt))

  // Overview cards: candidates in the same scope as the rest of the page, over the chosen period.
  const scoped = useMemo(() => store.candidates.filter(candidate => isTeam ? !staff || candidate.ownerId === staff : candidate.ownerId === store.me.id), [store.candidates, isTeam, staff, store.me.id])
  const anchorKey = data?.generatedAt
  const overview = useMemo<IntakeOverview>(() => {
    const end = new Date().getTime()
    const start = periodStart(period, new Date(end), scoped)
    const pool = inWindow(scoped, start, end)
    const before = inWindow(scoped, start - (end - start), start - 1)
    // A source this screen does not know is counted as "Other", so the parts always add up to the total.
    const count = (rows: typeof pool) => Object.fromEntries(SOURCE_KEYS.map(key => [key, rows.filter(candidate => (SOURCE_KEYS.includes(candidate.source) ? candidate.source : 'other') === key).length])) as Record<string, number>
    // Placed = reached Deployed, either on the candidate's own status or on any of their applications.
    const deployedIds = new Set(store.applications.filter(application => application.status === 'deployed').map(application => application.candidateId))
    const placed = count(pool.filter(candidate => candidate.status === 'deployed' || deployedIds.has(candidate.id)))
    const recentRows = [...pool].sort((a, b) => b.addedAt.localeCompare(a.addedAt)).slice(0, 12)
      .map(candidate => ({ id: candidate.id, name: candidate.name, source: candidate.source, addedAt: candidate.addedAt, status: candidate.status, role: candidate.designations[0] || candidate.skills[0] || 'No job title yet' }))
    const daily = Object.fromEntries([7, 14, 30].map(days => [days, recentDays(pool, days, end, SOURCE_KEYS)]))
    return { total: pool.length, verified: pool.filter(isVerified).length, ...intakeBuckets(pool, period, start, end, SOURCE_KEYS), sources: count(pool), previous: count(before), placed, recent: recentRows, daily, span: period === 'week' ? 7 : period === 'month' ? 30 : null }
    // anchorKey refreshes the window end whenever the dashboard reloads.
  }, [scoped, period, anchorKey, store.applications])
  // Days a candidate has waited at their current stage: since the last stage was completed, or since the application began.
  const waitingDays = useMemo(() => {
    const now = Date.now()
    const days: Record<string, number> = {}
    for (const application of store.applications) {
      if (application.status !== 'active') continue
      const since = Math.max(Date.parse(application.createdAt) || 0, ...application.stages.map(record => Date.parse(record.completedAt ?? '') || 0))
      if (since) days[application.id] = Math.max(0, Math.floor((now - since) / 86_400_000))
    }
    return days
  }, [store.applications, anchorKey])
  const arrivals = useMemo(() => arrivalGrid(scoped, new Date().getTime()), [scoped, anchorKey])
  const recent = useMemo(() => {
    const term = query.trim().toLowerCase()
    return [...scoped].sort((a, b) => b.addedAt.localeCompare(a.addedAt))
      .filter(candidate => !term || [candidate.name, candidate.ref, candidate.designations.join(' '), candidate.email, candidate.phone].join(' ').toLowerCase().includes(term)).slice(0, 6)
  }, [scoped, query])

  const taskColumns: Column<DashboardTask>[] = [
    { key: 'candidate', header: 'Candidate / task', value: row => row.candidate, width: '28%', render: row => <div className="dr-person"><Avatar name={row.candidate} /><div><strong>{row.candidate}</strong><span>{row.title}</span></div></div> },
    { key: 'stage', header: 'Stage', value: row => row.stage ?? 0, width: '20%', render: row => <StageMini no={row.stage} /> },
    { key: 'owner', header: 'Assigned to', value: row => row.owner, width: '12%', render: row => <span className="dr-owner"><Avatar name={row.owner} size="sm" />{row.owner.split(' ')[0]}</span> },
    { key: 'due', header: 'Due', value: row => row.dueAt, width: '22%', render: row => <div className="dr-due"><span className={row.overdue ? 'dr-overdue' : ''}>{dateTimeLabel(row.dueAt)}</span><SlaMeter dueAt={row.dueAt} /></div> },
    { key: 'updates', header: 'Daily update', value: row => row.notUpdated ? 'Not updated today' : 'Updated today', width: '18%', render: row => <Pill tone={row.notUpdated ? 'warn' : 'good'} title={row.notUpdated ? 'No note from the owner yet today' : undefined}>{row.notUpdated ? 'Not updated today' : 'Updated today'}</Pill> },
  ]
  const orderColumns: Column<OrderMetrics>[] = [
    { key: 'ref', header: 'Order', value: row => row.ref, width: '19%', render: row => <div className="dr-copy"><strong>{row.ref}</strong><small>{row.company}</small></div> },
    { key: 'designation', header: 'Position', value: row => row.designation, width: '19%', render: row => <div className="dr-copy"><strong>{row.designation}</strong><small>{row.client}</small></div> },
    { key: 'country', header: 'Country', value: row => store.order(row.id)?.country ?? '', width: '18%', render: row => { const order = store.order(row.id); return <span className="dr-place"><Flag country={order?.country} />{[order?.city, order?.country].filter(Boolean).join(', ') || 'Not added'}</span> } },
    { key: 'progress', header: 'Deployed', value: row => row.openings ? row.deployed / row.openings : 0, width: '20%', render: row => <div className="dr-progress" title={`${row.deployed} deployed of ${row.openings} openings`}><span><i style={{ width: `${row.openings ? Math.min(100, row.deployed / row.openings * 100) : 0}%` }} /></span><b>{number(row.deployed)}/{number(row.openings)}</b></div> },
    ...(['matched', 'selected'] as const).map(key => ({ key, header: key[0].toUpperCase() + key.slice(1), value: (row: OrderMetrics) => row[key], align: 'right' as const, width: '12%' })),
  ]
  function openTask(task: DashboardTask) { if (task.applicationId) { setOpenStage(task.stage || undefined); setOpenApp(task.applicationId) } else navigate(`/candidates/${task.candidateId}`) }
  async function finishTodo(id: string) {
    setPendingTodo(id); setTodoError('')
    try { await store.toggleTodo(id) } catch (failure) { setTodoError(failure instanceof Error ? failure.message : 'We could not mark this to-do as done. Please try again.') } finally { setPendingTodo('') }
  }
  function showTasks(filter: TaskFilter) {
    setTaskFilter(filter)
    document.getElementById('dashboard-workload')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }
  function exportOverview() {
    if (!data) return
    const rows = [
      ['Date', data.date], ['Time zone', data.timezone], ['Showing', data.staffId ? data.staffOptions.find(option => option.id === data.staffId)?.label ?? store.user(data.staffId)?.name ?? data.staffId : 'Whole team'],
      ['Candidates today', data.today.total], ['Added by email', data.today.email], ['Added by WhatsApp', data.today.whatsapp],
      ['Active applications', active], ['Open tasks', data.taskCounts.open], ['Due today', data.taskCounts.dueToday],
      ['Overdue tasks', data.taskCounts.overdue], ['Escalated tasks', data.taskCounts.escalated],
      ...data.pipeline.map(row => [`Stage ${row.stage}: ${row.label}`, row.count]),
    ]
    const csv = rows.map(row => row.map(value => {
      const text = String(value)
      return `"${(/^[=+@\-\t\r]/.test(text) ? `'${text}` : text).replaceAll('"', '""')}"`
    }).join(',')).join('\r\n')
    const url = URL.createObjectURL(new Blob(['﻿', csv], { type: 'text/csv;charset=utf-8' }))
    const link = document.createElement('a')
    link.href = url; link.download = `recruitment-overview-${data.date}.csv`
    document.body.appendChild(link); link.click(); link.remove()
    window.setTimeout(() => URL.revokeObjectURL(url), 1000)
  }
  const tasksView = data?.tasks.filter(task => taskFilter === 'open' || task[taskFilter]) ?? []
  const needsReview = scoped.filter(candidate => candidate.needsReview || candidate.status === 'needs_review').length
  const unassigned = scoped.filter(candidate => !candidate.ownerId).length
  // What needs a person now. Only items that have something waiting are listed; each opens the exact list.
  const attention: AttentionItem[] = !data ? [] : [
    ...(isTeam && unassigned > 0 ? [{ key: 'waiting', label: unassigned === 1 ? 'candidate waiting for a recruiter' : 'candidates waiting for a recruiter', hint: 'Open the Data Bank, waiting list', count: unassigned, icon: Users, tone: 'warn' as const, onClick: () => navigate('/data-bank?pool=unassigned') }] : []),
    ...(needsReview > 0 ? [{ key: 'review', label: needsReview === 1 ? 'profile that needs a review' : 'profiles that need a review', hint: 'Open the Data Bank, profiles to review', count: needsReview, icon: FileSearch, tone: 'warn' as const, onClick: () => navigate(`/data-bank?pool=needs_review${isTeam && staff ? `&staff=${encodeURIComponent(staff)}` : ''}`) }] : []),
    ...(data.taskCounts.overdue > 0 ? [{ key: 'overdue', label: data.taskCounts.overdue === 1 ? 'overdue task' : 'overdue tasks', hint: 'Show them in the task list below', count: data.taskCounts.overdue, icon: AlertTriangle, tone: 'bad' as const, onClick: () => showTasks('overdue') }] : []),
    ...(data.taskCounts.dueToday > 0 ? [{ key: 'today', label: data.taskCounts.dueToday === 1 ? 'task due today' : 'tasks due today', hint: 'Show them in the task list below', count: data.taskCounts.dueToday, icon: CalendarClock, tone: 'info' as const, onClick: () => showTasks('dueToday') }] : []),
    ...(store.inboxUnread > 0 ? [{ key: 'mail', label: store.inboxUnread === 1 ? 'unread message' : 'unread messages', hint: 'Open the inbox', count: store.inboxUnread, icon: Mail, tone: 'info' as const, onClick: () => navigate('/inbox') }] : []),
  ]
  const overdueRow = (row: DashboardTask) => row.overdue ? 'dr-row-overdue' : undefined
  const fulfilled = openings ? Math.min(100, Math.round(deployed / openings * 100)) : 0
  const fulfilDetail = openings ? data?.staffId ? `${number(deployed)} deployed out of ${number(openings)} openings in this view` : `${number(deployed)} of ${number(openings)} openings filled` : 'No open positions yet'
  const todayCards = data ? [
    { label: 'New candidates today', value: data.today.total, detail: `${number(data.today.email)} email · ${number(data.today.whatsapp)} WhatsApp`, icon: UserPlus, tone: 'primary', go: () => navigate('/data-bank') },
    { label: scopeLabel('open tasks'), value: data.taskCounts.open, detail: 'Waiting for the next step', icon: ListChecks, tone: 'violet', filter: 'open' as TaskFilter },
    { label: 'Tasks due today', value: data.taskCounts.dueToday, detail: 'Finish these before the 48-hour limit ends', icon: CalendarClock, tone: 'amber', filter: 'dueToday' as TaskFilter },
    { label: 'Overdue tasks', value: data.taskCounts.overdue, detail: isStaff ? 'Past the 48-hour limit' : `${number(data.taskCounts.escalated)} reported to the admin and managers`, icon: AlertTriangle, tone: 'red', filter: 'overdue' as TaskFilter, danger: data.taskCounts.overdue > 0 },
  ] : []

  const orderEmpty = <EmptyContent title="No open orders yet" detail="Open orders and how far each one has come will show here." />
  const myEmpty = <EmptyContent icon={CheckCheck} title="No tasks for you" detail="Tasks given to you will show here." />
  const tasksEmpty = <EmptyContent icon={CheckCheck} title={taskFilter === 'open' ? 'You’re all caught up' : 'No tasks in this view'} detail={taskFilter === 'open' ? 'New tasks will show here when there is work to do.' : 'Choose another tab to see more tasks.'} />

  return <div className="dashref">
    <header className="dr-head">
      <div><h1>Dashboard</h1><p>New candidates, where each one is in the process, and today&rsquo;s follow-ups. {!isTeam ? 'Showing your own numbers.' : staff ? `Showing ${store.user(staff)?.name ?? 'the selected recruiter'}.` : 'Showing the whole team.'}</p></div>
      <div className="dr-actions">
        {isTeam && <div className="dr-select"><Select aria-label="Show numbers for" value={staff} onChange={e => { setStaff(e.target.value); setStage(null) }}><option value="">Whole team</option>{(data?.staffOptions ?? store.users.map(user => ({ id: user.id, label: user.name }))).map(user => <option key={user.id} value={user.id}>{user.label}</option>)}</Select></div>}
        <button className="dr-btn dr-icon-only" aria-label="Refresh dashboard" title="Refresh dashboard" disabled={refreshing} onClick={refresh}><RefreshCw size={15} className={refreshing ? 'is-spinning' : ''} /></button>
        {store.can('export') && <button className="dr-btn" onClick={exportOverview} disabled={!data}><ArrowDownToLine size={14} />Export</button>}
        <button className="dr-btn is-primary" onClick={() => navigate('/add-candidate')}><Plus size={15} />Add candidate</button>
      </div>
    </header>
    {error && <div role="alert" className="dr-error">{error}{data && ' Showing the last loaded result.'}<button onClick={refresh}>Try again</button></div>}
    {!data && !error && <DashboardSkeleton />}
    {data && <>
      {attention.length > 0
        ? <section aria-label="Needs your attention now" className="dc-attn-section">
          <header className="dr-section-head"><div><h2>Needs your attention now</h2></div></header>
          <AttentionList items={attention} />
        </section>
        : <p className="dc-calm" role="status"><CheckCheck size={16} aria-hidden="true" />You are all caught up. Nothing is waiting for you right now.</p>}
      <section aria-label="New candidates and pipeline">
        <header className="dr-section-head">
          <div><h2>New candidates</h2><p>How many candidates came in, where they came from, and how complete their details are.</p></div>
          <div className="dr-seg" role="group" aria-label="Reporting period">{PERIODS.map(option => <button key={option.value} aria-pressed={period === option.value} className={period === option.value ? 'is-active' : ''} onClick={() => setPeriod(option.value)}>{option.short}</button>)}</div>
        </header>
        <IntakeBlock overview={overview} arrivals={arrivals} period={period} periodLabel={PERIODS.find(option => option.value === period)?.label ?? ''} scope={!isTeam ? 'Your numbers' : staff ? store.user(staff)?.name.split(' ')[0] ?? 'Selected recruiter' : 'Whole team'} staffId={isTeam ? staff : ''} stages={data.pipeline.map(row => ({ stage: row.stage, label: row.label, count: row.count, people: row.applications.map(application => ({ id: application.id, name: application.candidate, days: waitingDays[application.id] ?? null })) }))}
          todayCount={data.today.total} openOrders={data.orders.length} statusOf={status => STATUS[status] ?? STATUS.new} onStage={setStage} onCandidate={id => navigate(`/candidates/${id}`)} go={path => navigate(path)} />
      </section>

      <section aria-label="Today's work">
        <header className="dr-section-head"><div><h2>Today&rsquo;s work</h2><p>{isTeam && !staff ? 'New candidates and follow-ups across the team' : isTeam ? 'New candidates and follow-ups for the chosen recruiter' : 'Your new candidates and follow-ups'}</p></div></header>
        <div className="dr-grid-4">{todayCards.map(item => {
          const pressed = item.filter ? taskFilter === item.filter : undefined
          return <button key={item.label} className={`dr-stat dr-tone-${item.tone}${pressed ? ' is-selected' : ''}`} aria-pressed={pressed} onClick={() => item.filter ? showTasks(item.filter) : item.go?.()}>
            <span className="dr-stat-top"><span>{item.label}</span><span className="dr-icon-circle"><item.icon size={17} strokeWidth={1.9} /></span></span>
            <span className="dr-stat-value">{number(item.value)}</span>
            <span className={`dr-stat-detail${item.danger ? ' is-danger' : ''}`}>{item.detail}</span>
          </button>
        })}</div>
      </section>

      <section className="dr-card dr-recent">
        <CardHeading title="Latest candidates" subtitle="The newest candidates you can see, ready for a first call" action={<label className="dr-search"><Search size={14} /><input type="search" value={query} placeholder="Search name, role or phone" aria-label="Search latest candidates" onChange={event => setQuery(event.target.value)} /></label>} />
        {recent.length ? narrow ? <CandidateCards rows={recent} status={candidate => STATUS[candidate.status] ?? STATUS.new} onOpen={candidate => navigate(`/candidates/${candidate.id}`)} /> : <div className="dr-table-wrap"><table className="dr-table"><colgroup><col style={{ width: '28%' }} /><col style={{ width: '17%' }} /><col style={{ width: '14%' }} /><col style={{ width: '13%' }} /><col style={{ width: '16%' }} /><col style={{ width: '12%' }} /></colgroup><thead><tr><th><span>Candidate</span></th><th><span>Job title</span></th><th><span>Source</span></th><th><span>Added</span></th><th><span>Status</span></th><th><span className="sr-only">Open candidate</span></th></tr></thead><tbody>{recent.map(candidate => {
          const status = STATUS[candidate.status] ?? STATUS.new
          return <tr key={candidate.id} onClick={() => navigate(`/candidates/${candidate.id}`)}>
            <td><span className="dr-person"><Avatar name={candidate.name} /><span><strong>{candidate.name}</strong><small>{candidate.phone || candidate.email || 'No phone or email'}</small></span></span></td>
            <td>{roleLine(candidate)}</td>
            <td><SourceTag source={candidate.source} /></td>
            <td>{dateLabel(candidate.addedAt)}</td>
            <td><Pill tone={status.tone}>{status.label}</Pill></td>
            <td className="dr-cell-end"><button type="button" className="dr-open" aria-label={`Open ${candidate.name}`} onClick={event => { event.stopPropagation(); navigate(`/candidates/${candidate.id}`) }}>Open<ArrowRight size={14} /></button></td>
          </tr>
        })}</tbody></table></div> : <EmptyContent icon={Users} title={query ? 'No matching candidates' : 'No candidates yet'} detail={query ? 'Try a different name, job title, phone or email.' : 'Add a candidate, or let emails and WhatsApp messages add them to the Data Bank.'} />}
        <footer className="dr-card-foot">
          <span className="dr-foot-chips">
            {needsReview > 0 && <button type="button" className="dr-chip" onClick={() => navigate('/data-bank')}>{number(needsReview)} need review</button>}
            {isTeam && unassigned > 0 && <button type="button" className="dr-chip" onClick={() => navigate('/data-bank')}>{number(unassigned)} not assigned</button>}
          </span>
          <button className="dr-link" onClick={() => navigate('/data-bank')}>View all in Data Bank<ArrowRight size={13} /></button>
        </footer>
      </section>

      <div className={`dr-split${isStaff ? ' is-single' : ''}`}>
        <section className="dr-card dr-orders">
          <CardHeading title="Active orders" subtitle="Open orders, and how many candidates are matched, selected and deployed for each." action={<button className="dr-link" onClick={() => navigate('/jobs')}>All orders<ChevronRight size={13} /></button>} />
          {data.orders.length > 0 && <div className="dr-fulfil" role="img" aria-label={fulfilDetail}>
            <span className="dr-fulfil-label">{data.staffId ? 'Deployed in this view' : 'Openings filled'}</span>
            <span className="dr-fulfil-bar"><i style={{ width: `${fulfilled}%` }} /></span>
            <span className="dr-fulfil-text">{fulfilDetail}</span>
          </div>}
          {narrow ? <OrderCards rows={data.orders} country={row => store.order(row.id) ?? {}} onOpen={row => navigate(`/orders/${row.id}`)} empty={orderEmpty} /> : <DataTable rows={data.orders} columns={orderColumns} rowKey={row => row.id} pageSize={5} onRowClick={row => navigate(`/orders/${row.id}`)} empty={orderEmpty} />}
        </section>
        {!isStaff && <section className="dr-card dr-todos">
          <CardHeading title="My to-dos" subtitle="Your personal reminders, soonest first." action={<button className="dr-link" onClick={() => navigate('/todo')}>All to-dos<ChevronRight size={13} /></button>} />
          {todoError && <p role="alert" className="dr-error">{todoError}</p>}
          {todos.length ? <ul>{todos.slice(0, 4).map(todo => <li key={todo.id}><button className="dr-check" onClick={() => void finishTodo(todo.id)} disabled={!!pendingTodo} aria-label={`Complete ${todo.title}`}>{pendingTodo === todo.id ? <RefreshCw size={13} /> : <Check size={13} />}</button><div><strong>{todo.title}</strong><span className={+new Date(todo.dueAt) < Date.now() ? 'dr-overdue' : ''}>{dateTimeLabel(todo.dueAt)}</span>{todo.linkTo && <button className="dr-link" onClick={() => navigate(todo.linkTo!)}>{todo.linkLabel || 'Open linked item'}<ArrowUpRight size={11} /></button>}</div></li>)}</ul> : <div className="dr-empty is-small"><span><CheckCheck size={18} /></span><strong>Nothing to do right now</strong><p>Add a to-do to remind yourself of a follow-up.</p></div>}
        </section>}
      </div>

      <section className="dr-card" id="dashboard-workload">
        <CardHeading title={isStaff ? 'My tasks' : 'Task overview'} subtitle={isStaff ? 'Open tasks, soonest due first. Overdue tasks are shown in red.' : `${scopeLabel('open tasks')}, soonest due first. Overdue tasks are shown in red.`} action={<button className="dr-link" onClick={() => navigate('/tasks')}>All tasks<ChevronRight size={13} /></button>} />
        <div className="dr-tabs dr-tabs-scroll" role="group" aria-label="Filter tasks">{FILTERS.filter(filter => !isStaff || filter.key !== 'escalated').map(filter => <button key={filter.key} aria-pressed={taskFilter === filter.key} title={filter.hint} className={taskFilter === filter.key ? 'is-active' : ''} onClick={() => setTaskFilter(filter.key)}>{filter.label}</button>)}</div>
        {narrow ? <TaskCards key={`${staff}:${taskFilter}`} rows={tasksView} showOwner={!isStaff} onOpen={openTask} empty={tasksEmpty} /> : <DataTable rows={tasksView} columns={isStaff ? taskColumns.filter(column => column.key !== 'owner') : taskColumns} rowKey={row => row.id} pageSize={6} resetPageKey={`${staff}:${taskFilter}`} onRowClick={openTask} rowClassName={overdueRow} empty={tasksEmpty} />}
      </section>
      {!isStaff && <section className="dr-card"><CardHeading title="My tasks" subtitle="Tasks assigned to you, soonest due first." action={<Clock3 size={16} className="dr-muted-icon" />} />{narrow ? <TaskCards rows={data.myTasks} showOwner={false} onOpen={openTask} empty={myEmpty} /> : <DataTable rows={data.myTasks} columns={taskColumns.filter(column => column.key !== 'owner')} rowKey={row => row.id} pageSize={5} onRowClick={openTask} rowClassName={overdueRow} empty={myEmpty} />}</section>}
      {!isStaff && <section aria-label="Email and WhatsApp imports">
        <header className="dr-section-head"><div><h2>Email and WhatsApp imports</h2><p>When each email account and WhatsApp number last checked for new messages, and files we could not read.{isAdmin ? '' : ' Only the admin can try again.'}</p></div></header>
        <div className={`dr-split dr-split-even${isAdmin ? '' : ' is-single'}`}>
          <NeedsAttention />
          {isAdmin && <SourceHealth />}
        </div>
      </section>}
      <footer className="dr-foot"><span><i />Updated {new Date(data.generatedAt).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span><span>Times are in {data.timezone}</span></footer>
      <Dialog open={stage !== null} onClose={() => setStage(null)} title={stageData ? `Stage ${stageData.stage} · ${stageData.label}` : 'Pipeline candidates'} description="Active candidates in this view." width="lg"><DataTable rows={stageData?.applications ?? []} rowKey={row => row.id} columns={[{ key: 'candidate', header: 'Candidate', value: row => row.candidate }, { key: 'order', header: 'Order', value: row => row.order }]} onRowClick={row => { setStage(null); setOpenApp(row.id) }} pageSize={10} /></Dialog>
    </>}
    {openApp && <StageModal applicationId={openApp} open focusStage={openStage} onClose={() => { setOpenApp(null); refresh() }} />}
  </div>
}
