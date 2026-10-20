import { useMemo, useState, type ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Bar,
  CartesianGrid,
  Cell,
  ComposedChart,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import {
  ArrowDownToLine,
  ArrowUpRight,
  BriefcaseBusiness,
  Building2,
  CalendarDays,
  ChevronLeft,
  ChevronRight,
  ChevronsUpDown,
  CircleCheck,
  Clock3,
  FileCheck2,
  GitBranch,
  Globe2,
  Mail,
  MessageCircle,
  Plus,
  UserPlus,
  Users,
} from 'lucide-react'
import { useStore } from '@/shared/state/crm-store'
import type { Candidate } from '@/shared/model/types'
import { agency } from '@/app/config/agency'
import { STAGES, stageByNo } from '@/shared/workflow/stages'
import { ago, dateLabel, initials, slaState } from '@/shared/lib/format'
import { cn } from '@/shared/lib/cn'
import { StageModal } from '@/features/workflow/components/stage-modal'
import { CheckboxInput, Select } from '@/shared/ui/primitives'
import { DatePicker } from '@/shared/ui/date-picker'
import { SourceHealth } from '../components/source-health'

const STATUS_LABELS: Record<string, string> = {
  new: 'New',
  in_process: 'In progress',
  deployed: 'Deployed',
  needs_review: 'Needs review',
  not_interested: 'Not interested',
}
const SOURCE_LABELS: Record<string, string> = {
  email: 'Email',
  whatsapp: 'WhatsApp',
  walk_in: 'Walk-in',
  referral: 'Referral',
  other: 'Other',
}
const COUNTRY_FLAGS: Record<string, string> = {
  UAE: 'ae',
  'United Arab Emirates': 'ae',
  'Saudi Arabia': 'sa',
  Qatar: 'qa',
  Oman: 'om',
  Kuwait: 'kw',
  Bahrain: 'bh',
  India: 'in',
  Singapore: 'sg',
  Germany: 'de',
  'United Kingdom': 'gb',
  'United States': 'us',
}
const WEEKDAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']
const HOURS = Array.from({ length: 13 }, (_, index) => index + 8)
const calendarFormatter = new Intl.DateTimeFormat('en-US', {
  timeZone: agency.timezone,
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  weekday: 'short',
  hour: '2-digit',
  hourCycle: 'h23',
})

function calendar(value: string | Date) {
  const parts = Object.fromEntries(
    calendarFormatter.formatToParts(new Date(value)).map((part) => [part.type, part.value]),
  )
  return {
    key: `${parts.year}-${parts.month}-${parts.day}`,
    month: `${parts.year}-${parts.month}`,
    year: Number(parts.year),
    monthNo: Number(parts.month),
    day: Number(parts.day),
    weekday: parts.weekday,
    hour: Number(parts.hour),
  }
}

function monthKey(offset = 0) {
  const current = calendar(new Date())
  return new Date(Date.UTC(current.year, current.monthNo - 1 - offset, 1)).toISOString().slice(0, 7)
}

function hourLabel(hour: number) {
  return `${hour % 12 || 12}${hour < 12 ? 'am' : 'pm'}`
}

export default function DashboardPage() {
  const store = useStore()
  const navigate = useNavigate()
  const [staffFilter, setStaffFilter] = useState('all')
  const [trendPeriod, setTrendPeriod] = useState('year')
  const [activityMonth, setActivityMonth] = useState('current')
  const [candidatePeriod, setCandidatePeriod] = useState('all')
  const [candidateDates, setCandidateDates] = useState({ from: '', to: '' })
  const [statusFilter, setStatusFilter] = useState('all')
  const [sort, setSort] = useState<{ key: 'name' | 'addedAt'; descending: boolean }>({
    key: 'addedAt',
    descending: true,
  })
  const [page, setPage] = useState(0)
  const [selected, setSelected] = useState<string[]>([])
  const [openApp, setOpenApp] = useState<string | null>(null)
  const scopeUserId = store.can('all_reports')
    ? staffFilter === 'all'
      ? null
      : staffFilter
    : store.me.id
  const candidates = useMemo(
    () => store.candidates.filter((candidate) => !scopeUserId || candidate.ownerId === scopeUserId),
    [store.candidates, scopeUserId],
  )
  const scopedTasks = store.tasks.filter((task) => !scopeUserId || task.ownerId === scopeUserId)
  const tasks = scopedTasks
    .filter((task) => !task.completedAt && !task.cancelledAt)
    .sort((first, second) => +new Date(first.createdAt) - +new Date(second.createdAt))
  const applications = store.applications.filter(
    (application) =>
      !scopeUserId || store.candidate(application.candidateId)?.ownerId === scopeUserId,
  )
  const activeApplications = applications.filter((application) => application.status === 'active')
  const deployed = applications.filter((application) => application.status === 'deployed').length
  const todayKey = calendar(new Date()).key
  const newToday = candidates.filter((candidate) => calendar(candidate.addedAt).key === todayKey)
  const overdue = tasks.filter((task) => slaState(task.createdAt).overdue).length
  const dueToday = tasks.filter((task) => {
    const deadline = slaState(task.createdAt)
    return !deadline.overdue && calendar(deadline.dueAt).key === todayKey
  }).length
  const activeOrders = store.orders.filter(
    (order) => order.status === 'open' || order.status === 'on_hold',
  )
  const myTodos = store.todos.filter((todo) => todo.ownerId === store.me.id && !todo.done)
  const metrics = [
    {
      label: 'New candidates',
      value: newToday.length,
      icon: UserPlus,
      detail: 'Added today',
      to: '/data-bank',
    },
    {
      label: 'In progress',
      value: activeApplications.length,
      icon: GitBranch,
      detail: `${STAGES.length} recruitment stages`,
      to: '/mapped',
    },
    {
      label: 'Open tasks',
      value: tasks.length,
      icon: FileCheck2,
      detail: `${dueToday} due today`,
      to: '/tasks',
    },
    {
      label: 'Overdue tasks',
      value: overdue,
      icon: Clock3,
      detail: '48-hour response limit',
      to: '/tasks',
    },
  ]

  const trend = useMemo(() => {
    const current = calendar(new Date())
    const bucketCount = trendPeriod === 'year' ? 12 : 30
    const buckets = Array.from({ length: bucketCount }, (_, index) => {
      const offset = bucketCount - 1 - index
      const date =
        trendPeriod === 'year'
          ? new Date(Date.UTC(current.year, current.monthNo - 1 - offset, 1))
          : new Date(Date.UTC(current.year, current.monthNo - 1, current.day - offset))
      return {
        key: date.toISOString().slice(0, trendPeriod === 'year' ? 7 : 10),
        label:
          trendPeriod === 'year'
            ? date.toLocaleDateString('en-US', { month: 'short', timeZone: 'UTC' })
            : date.toLocaleDateString('en-US', { day: 'numeric', month: 'short', timeZone: 'UTC' }),
        totalHours: 0,
        count: 0,
        days: null as number | null,
      }
    })
    scopedTasks
      .filter((task) => task.completedAt && !task.cancelledAt)
      .forEach((task) => {
        const completed = calendar(task.completedAt!)
        const bucket = buckets.find(
          (item) => item.key === (trendPeriod === 'year' ? completed.month : completed.key),
        )
        const elapsedHours = (+new Date(task.completedAt!) - +new Date(task.createdAt)) / 3600000
        if (bucket && elapsedHours >= 0) {
          bucket.totalHours += elapsedHours
          bucket.count += 1
        }
      })
    buckets.forEach((bucket) => {
      bucket.days = bucket.count ? Number((bucket.totalHours / bucket.count / 24).toFixed(2)) : null
    })
    const count = buckets.reduce((total, bucket) => total + bucket.count, 0)
    const totalHours = buckets.reduce((total, bucket) => total + bucket.totalHours, 0)
    return { buckets, count, averageDays: count ? totalHours / count / 24 : null }
  }, [scopedTasks, trendPeriod])

  const rankings = useMemo(() => {
    const countries = new Map<string, number>()
    const companies = new Map<string, number>()
    activeApplications.forEach((application) => {
      const order = store.order(application.orderId)
      if (!order) return
      const country = order.country || 'Not specified'
      countries.set(country, (countries.get(country) ?? 0) + 1)
      companies.set(order.companyId, (companies.get(order.companyId) ?? 0) + 1)
    })
    return {
      countries: [...countries.entries()].sort((first, second) => second[1] - first[1]).slice(0, 5),
      companies: [...companies.entries()].sort((first, second) => second[1] - first[1]).slice(0, 5),
    }
  }, [activeApplications, store.orders])

  const activity = useMemo(() => {
    const cells = WEEKDAYS.map(() => HOURS.map(() => 0))
    const targetMonth = monthKey(activityMonth === 'previous' ? 1 : 0)
    const scopedTaskIds = new Set(scopedTasks.map((task) => task.id))
    const events = [
      ...candidates.map((candidate) => candidate.addedAt),
      ...scopedTasks.map((task) => task.createdAt),
      ...store.remarks
        .filter((remark) => scopedTaskIds.has(remark.taskId))
        .map((remark) => remark.createdAt),
    ]
    events.forEach((event) => {
      const recorded = calendar(event)
      const weekdayIndex = WEEKDAYS.indexOf(recorded.weekday)
      const hourIndex = HOURS.indexOf(recorded.hour)
      if (recorded.month === targetMonth && weekdayIndex >= 0 && hourIndex >= 0)
        cells[weekdayIndex][hourIndex] += 1
    })
    const peak = Math.max(1, ...cells.flat())
    const hourTotals = HOURS.map((_, hourIndex) =>
      cells.reduce((total, row) => total + row[hourIndex], 0),
    )
    const busiest = hourTotals.indexOf(Math.max(...hourTotals))
    return {
      cells,
      peak,
      busiest: hourTotals[busiest]
        ? `${hourLabel(HOURS[busiest])} – ${hourLabel(HOURS[busiest] + 1)}`
        : 'No activity yet',
    }
  }, [candidates, scopedTasks, store.remarks, activityMonth])

  const rows = candidates
    .filter(
      (candidate) =>
        (statusFilter === 'all' || candidate.status === statusFilter) &&
        (candidatePeriod === 'all' ||
          (candidatePeriod === 'custom'
            ? (!candidateDates.from || calendar(candidate.addedAt).key >= candidateDates.from) &&
              (!candidateDates.to || calendar(candidate.addedAt).key <= candidateDates.to)
            : calendar(candidate.addedAt).month ===
              monthKey(candidatePeriod === 'previous' ? 1 : 0))),
    )
    .sort((first, second) => {
      const comparison =
        sort.key === 'name'
          ? first.name.localeCompare(second.name)
          : +new Date(first.addedAt) - +new Date(second.addedAt)
      return sort.descending ? -comparison : comparison
    })
  const pageCount = Math.max(1, Math.ceil(rows.length / 5))
  const safePage = Math.min(page, pageCount - 1)
  const visibleRows = rows.slice(safePage * 5, safePage * 5 + 5)
  const allVisibleSelected =
    visibleRows.length > 0 && visibleRows.every((candidate) => selected.includes(candidate.id))
  const progressFor = (candidate: Candidate) => {
    const candidateApps = applications.filter(
      (application) => application.candidateId === candidate.id,
    )
    const application =
      candidateApps.find((item) => item.status === 'active') ??
      candidateApps.find((item) => item.status === 'deployed')
    return {
      application,
      percent:
        application?.status === 'deployed'
          ? 100
          : application
            ? Math.round(((application.currentStage - 1) / STAGES.length) * 100)
            : 0,
    }
  }
  const exportCandidates = () => {
    const exportRows = selected.length
      ? candidates.filter((candidate) => selected.includes(candidate.id))
      : rows
    const csvCell = (value: unknown) => {
      const raw = String(value ?? '')
      return `"${(/^[=+@\t\r-]/.test(raw) ? `'${raw}` : raw).replaceAll('"', '""')}"`
    }
    const csv = [
      ['Reference', 'Name', 'Source', 'Added', 'Status', 'Progress'],
      ...exportRows.map((candidate) => [
        candidate.ref,
        candidate.name,
        SOURCE_LABELS[candidate.source],
        candidate.addedAt,
        STATUS_LABELS[candidate.status],
        `${progressFor(candidate).percent}%`,
      ]),
    ]
      .map((row) => row.map(csvCell).join(','))
      .join('\r\n')
    const url = URL.createObjectURL(new Blob(['\uFEFF', csv], { type: 'text/csv;charset=utf-8' }))
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = `care-india-candidates-${todayKey}.csv`
    anchor.click()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  }

  return (
    <div className="dashboard">
      <div className="dashboard-toolbar">
        <p>
          <span className="live-dot" /> Recruitment overview{' '}
          <span className="toolbar-date">{dateLabel(new Date())}</span>
        </p>
        <div>
          {store.can('all_reports') && (
            <label className="dashboard-select">
              <Users size={13} />
              <Select
                compact
                aria-label="Filter by staff"
                value={staffFilter}
                onChange={(event) => {
                  setStaffFilter(event.target.value)
                  setPage(0)
                  setSelected([])
                }}
              >
                <option value="all">Whole team</option>
                {store.users
                  .filter((user) => user.role === 'staff' && user.active)
                  .map((user) => (
                    <option key={user.id} value={user.id}>
                      {user.name}
                    </option>
                  ))}
              </Select>
            </label>
          )}
          <button className="dashboard-primary" onClick={() => navigate('/add-candidate')}>
            <Plus size={14} /> Add candidate
          </button>
        </div>
      </div>

      <div className="analytics-grid">
        <section className="analytics-metrics" aria-label="Recruitment metrics">
          <button className="total-card" onClick={() => navigate('/data-bank')}>
            <span className="total-card-art" aria-hidden="true" />
            <span className="metric-icon">
              <Users size={21} />
            </span>
            <span className="total-label">Total candidates</span>
            <div className="total-value">
              <strong>{candidates.length.toLocaleString('en-IN')}</strong>
              <span>
                <CircleCheck size={11} /> {deployed} deployed
              </span>
            </div>
            <span className="total-caption">
              {activeOrders.length} active orders <ArrowUpRight size={14} />
            </span>
          </button>
          <div className="compact-metrics">
            {metrics.map((metric) => (
              <button
                key={metric.label}
                className="compact-metric"
                onClick={() => navigate(metric.to)}
              >
                <span className="compact-metric-title">
                  <span className="metric-icon">
                    <metric.icon size={15} />
                  </span>
                  {metric.label}
                </span>
                <span className="compact-metric-bottom">
                  <strong className="metric-value">{metric.value.toLocaleString('en-IN')}</strong>
                  <span
                    className={cn(
                      metric.label === 'Overdue tasks' && metric.value > 0 && 'metric-warning',
                    )}
                  >
                    {metric.detail}
                  </span>
                </span>
              </button>
            ))}
          </div>
        </section>

        <section className="analytics-card completion-card" aria-labelledby="completion-title">
          <CardHeading id="completion-title" title="Average task completion">
            <PeriodSelect
              label="Completion chart period"
              value={trendPeriod}
              onChange={setTrendPeriod}
              options={[
                ['year', 'Last 12 months'],
                ['month', 'Last 30 days'],
              ]}
            />
          </CardHeading>
          <div className="completion-summary">
            <strong>
              {trend.averageDays === null ? '—' : trend.averageDays.toFixed(2)}
              <span> Days</span>
            </strong>
            <span
              className={cn(
                'completion-badge',
                trend.averageDays !== null && trend.averageDays > 2 && 'is-slow',
              )}
            >
              {trend.count ? `${trend.count} tasks completed` : 'No completed tasks yet'}
            </span>
          </div>
          <div className="completion-chart">
            <ResponsiveContainer width="100%" height="100%">
              <ComposedChart
                data={trend.buckets}
                margin={{ top: 10, right: 8, bottom: 0, left: -24 }}
              >
                <CartesianGrid vertical={false} stroke="#f3f3f3" />
                <XAxis
                  dataKey="label"
                  axisLine={false}
                  tickLine={false}
                  tick={{ fontSize: 11, fill: '#999' }}
                  interval={trendPeriod === 'month' ? 5 : 0}
                />
                <YAxis
                  axisLine={false}
                  tickLine={false}
                  tick={{ fontSize: 11, fill: '#999' }}
                  allowDecimals
                  domain={[0, (maximum: number) => Math.max(4, Math.ceil(maximum))]}
                />
                <Tooltip
                  contentStyle={{
                    border: 'none',
                    borderRadius: 7,
                    fontSize: 13,
                    boxShadow: '0 5px 20px #00000012',
                  }}
                  formatter={(value: number) => [`${value} days`, 'Average completion']}
                />
                <Bar dataKey="days" radius={[3, 3, 0, 0]} maxBarSize={24} isAnimationActive={false}>
                  {trend.buckets.map((bucket, index) => (
                    <Cell
                      key={bucket.key}
                      fill={
                        index === trend.buckets.length - 1 ? '#282929' : 'var(--ui-accent-soft)'
                      }
                    />
                  ))}
                </Bar>
                <Line
                  type="monotone"
                  dataKey="days"
                  stroke="var(--ui-accent)"
                  strokeWidth={2}
                  dot={{
                    r: trendPeriod === 'year' ? 2.5 : 1.5,
                    fill: 'var(--ui-accent)',
                    strokeWidth: 0,
                  }}
                  isAnimationActive={false}
                  tooltipType="none"
                />
              </ComposedChart>
            </ResponsiveContainer>
            {!trend.count && (
              <span className="chart-empty-note">Completed tasks will build your trend.</span>
            )}
          </div>
        </section>

        <div className="ranking-grid">
          <section className="analytics-card ranking-card" aria-labelledby="countries-title">
            <CardHeading id="countries-title" title="Top countries">
              <button
                className="dashboard-icon-link"
                aria-label="View country orders"
                onClick={() => navigate('/jobs')}
              >
                <ArrowUpRight size={15} />
              </button>
            </CardHeading>
            {rankings.countries.length ? (
              <ol>
                {rankings.countries.map(([country, count]) => (
                  <li className="country-rank" key={country}>
                    <span className="country-flag">
                      {COUNTRY_FLAGS[country] ? (
                        <img
                          src={`/flags/${COUNTRY_FLAGS[country]}.svg`}
                          alt=""
                          width={27}
                          height={27}
                        />
                      ) : (
                        <Globe2 size={22} />
                      )}
                    </span>
                    <div>
                      <div className="rank-label">
                        <strong>{country}</strong>
                        <span>
                          {count} candidates{' '}
                          <b>
                            · {Math.round((count / Math.max(1, activeApplications.length)) * 100)}%
                          </b>
                        </span>
                      </div>
                      <span className="rank-track">
                        <span
                          style={{
                            width: `${(count / Math.max(1, activeApplications.length)) * 100}%`,
                          }}
                        />
                      </span>
                    </div>
                  </li>
                ))}
              </ol>
            ) : (
              <CompactEmpty
                icon={Globe2}
                title="Your reach starts here"
                detail="Countries appear as candidates are mapped."
              />
            )}
          </section>
          <section className="analytics-card ranking-card" aria-labelledby="companies-title">
            <CardHeading id="companies-title" title="Top companies">
              <button
                className="dashboard-icon-link"
                aria-label="View companies"
                onClick={() => navigate('/companies')}
              >
                <ArrowUpRight size={15} />
              </button>
            </CardHeading>
            {rankings.companies.length ? (
              <ol>
                {rankings.companies.map(([companyId, count], index) => {
                  const company = store.company(companyId)
                  return (
                    <li key={companyId} className="company-rank">
                      <span className={cn('company-symbol', index % 2 === 0 && 'is-dark')}>
                        {initials(company?.name ?? 'Company')}
                      </span>
                      <div>
                        <strong>{company?.name ?? 'Company'}</strong>
                        <span>{company?.industry || 'Recruitment partner'}</span>
                      </div>
                      <span className="company-rank-count">
                        <strong>{count}</strong>
                        <span>candidates</span>
                      </span>
                    </li>
                  )
                })}
              </ol>
            ) : (
              <CompactEmpty
                icon={Building2}
                title="Make your next connection"
                detail="Mapped employers will appear here."
              />
            )}
          </section>
        </div>

        <section className="analytics-card activity-card" aria-labelledby="activity-title">
          <CardHeading id="activity-title" title="Busy periods">
            <PeriodSelect
              label="Activity month"
              value={activityMonth}
              onChange={setActivityMonth}
              options={[
                ['current', 'This month'],
                ['previous', 'Last month'],
              ]}
            />
          </CardHeading>
          <div className="activity-caption">
            <span>
              Highest activity <strong>{activity.busiest}</strong>
            </span>
            <span className="heat-legend">
              Less{' '}
              {[0, 1, 2, 3, 4].map((level) => (
                <i key={level} data-level={level} />
              ))}{' '}
              More
            </span>
          </div>
          <div
            className="heatmap"
            role="img"
            aria-label="Recruitment activity by weekday and hour in India Standard Time"
          >
            <div className="heatmap-days">
              {WEEKDAYS.map((day) => (
                <span key={day}>{day}</span>
              ))}
            </div>
            <div className="heatmap-body">
              {activity.cells.map((row, dayIndex) => (
                <div className="heatmap-row" key={WEEKDAYS[dayIndex]}>
                  {row.map((count, hourIndex) => (
                    <span
                      key={HOURS[hourIndex]}
                      data-level={count ? Math.max(1, Math.ceil((count / activity.peak) * 4)) : 0}
                      title={`${WEEKDAYS[dayIndex]}, ${hourLabel(HOURS[hourIndex])} IST: ${count} activities`}
                    />
                  ))}
                </div>
              ))}
              <div className="heatmap-hours">
                {HOURS.map((hour) => (
                  <span key={hour}>{hourLabel(hour)}</span>
                ))}
              </div>
            </div>
          </div>
          <p className="heatmap-footnote">
            Candidate additions, tasks, and remarks · India Standard Time
          </p>
        </section>
      </div>

      <section
        className="analytics-card candidate-list-card"
        aria-labelledby="candidate-list-title"
      >
        <div className="candidate-list-toolbar">
          <h2 id="candidate-list-title">Candidate list</h2>
          <div className="candidate-list-controls">
            <div className="candidate-status-tabs" aria-label="Filter candidates by status">
              {['all', 'new', 'in_process', 'deployed', 'needs_review'].map((status) => (
                <button
                  key={status}
                  aria-pressed={statusFilter === status}
                  className={cn(statusFilter === status && 'is-active')}
                  onClick={() => {
                    setStatusFilter(status)
                    setPage(0)
                  }}
                >
                  {status === 'all' ? 'All' : STATUS_LABELS[status]}
                </button>
              ))}
            </div>
            <PeriodSelect
              label="Candidate date range"
              value={candidatePeriod}
              onChange={(value) => {
                setCandidatePeriod(value)
                setPage(0)
              }}
              options={[
                ['all', 'All time'],
                ['current', 'This month'],
                ['previous', 'Last month'],
                ['custom', 'Custom dates'],
              ]}
              icon
            />
            <button
              className="dashboard-export"
              aria-label="Export candidates to CSV"
              onClick={exportCandidates}
              disabled={!rows.length}
              hidden={!store.can('export')}
            >
              <ArrowDownToLine size={14} />
            </button>
          </div>
        </div>
        {candidatePeriod === 'custom' && (
          <div className="candidate-custom-dates">
            <DatePicker
              compact
              aria-label="From date"
              placeholder="From date"
              value={candidateDates.from}
              max={candidateDates.to || undefined}
              onChange={(value) => {
                setCandidateDates((current) => ({ ...current, from: value }))
                setPage(0)
              }}
            />
            <span>–</span>
            <DatePicker
              compact
              aria-label="To date"
              placeholder="To date"
              value={candidateDates.to}
              min={candidateDates.from || undefined}
              onChange={(value) => {
                setCandidateDates((current) => ({ ...current, to: value }))
                setPage(0)
              }}
            />
          </div>
        )}
        {selected.length > 0 && (
          <div className="candidate-selection">
            {selected.length} selected
            <button onClick={() => setSelected([])}>Clear selection</button>
            <button onClick={exportCandidates}>
              Export selected <ArrowDownToLine size={12} />
            </button>
          </div>
        )}
        <div className="candidate-table-scroll">
          <table className="candidate-table">
            <thead>
              <tr>
                {store.can('export') && (
                  <th className="checkbox-cell">
                    <CheckboxInput
                      aria-label="Select all candidates on this page"
                      checked={allVisibleSelected}
                      indeterminate={
                        !allVisibleSelected &&
                        visibleRows.some((candidate) => selected.includes(candidate.id))
                      }
                      onChange={() =>
                        setSelected(
                          allVisibleSelected
                            ? selected.filter(
                                (id) => !visibleRows.some((candidate) => candidate.id === id),
                              )
                            : [
                                ...new Set([
                                  ...selected,
                                  ...visibleRows.map((candidate) => candidate.id),
                                ]),
                              ],
                        )
                      }
                    />
                  </th>
                )}
                <th>Candidate ID</th>
                <th>
                  <button
                    onClick={() => {
                      setSort({
                        key: 'name',
                        descending: sort.key === 'name' ? !sort.descending : false,
                      })
                      setPage(0)
                    }}
                  >
                    Candidate <ChevronsUpDown size={10} />
                  </button>
                </th>
                <th>Position / Company</th>
                <th>Source</th>
                <th
                  aria-sort={
                    sort.key === 'addedAt' ? (sort.descending ? 'descending' : 'ascending') : 'none'
                  }
                >
                  <button
                    onClick={() => {
                      setSort({
                        key: 'addedAt',
                        descending: sort.key === 'addedAt' ? !sort.descending : true,
                      })
                      setPage(0)
                    }}
                  >
                    Added <ChevronsUpDown size={10} />
                  </button>
                </th>
                <th>Progress</th>
                <th>Status</th>
                <th>
                  <span className="sr-only">Open profile</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {visibleRows.map((candidate) => {
                const progress = progressFor(candidate)
                const order = progress.application
                  ? store.order(progress.application.orderId)
                  : undefined
                return (
                  <tr
                    key={candidate.id}
                    className={cn(selected.includes(candidate.id) && 'is-selected')}
                  >
                    {store.can('export') && (
                      <td className="checkbox-cell">
                        <CheckboxInput
                          aria-label={`Select ${candidate.name}`}
                          checked={selected.includes(candidate.id)}
                          onChange={() =>
                            setSelected(
                              selected.includes(candidate.id)
                                ? selected.filter((id) => id !== candidate.id)
                                : [...selected, candidate.id],
                            )
                          }
                        />
                      </td>
                    )}
                    <td>
                      <button
                        className="candidate-reference"
                        onClick={() => navigate(`/candidates/${candidate.id}`)}
                      >
                        <span
                          className={cn(
                            'candidate-source-icon',
                            candidate.source === 'whatsapp' && 'is-dark',
                          )}
                        >
                          {candidate.source === 'email' ? (
                            <Mail size={14} />
                          ) : candidate.source === 'whatsapp' ? (
                            <MessageCircle size={14} />
                          ) : (
                            <Users size={14} />
                          )}
                        </span>
                        {candidate.ref}
                      </button>
                    </td>
                    <td>
                      <button
                        className="candidate-name-button"
                        onClick={() => navigate(`/candidates/${candidate.id}`)}
                      >
                        <strong>{candidate.name}</strong>
                        <span>{store.user(candidate.ownerId)?.name ?? 'Unassigned'}</span>
                      </button>
                    </td>
                    <td>
                      <span className="table-main-text">
                        {order
                          ? (store.designation(order.designationId)?.title ?? 'Open position')
                          : candidate.designations
                              .map(
                                (designation) =>
                                  store.designation(designation)?.title ?? designation,
                              )
                              .join(', ') || 'Not specified'}
                      </span>
                      <span className="table-sub-text">
                        {order ? store.company(order.companyId)?.name : candidate.currentCity}
                      </span>
                    </td>
                    <td>
                      <span className="table-main-text">
                        {SOURCE_LABELS[candidate.source] ?? candidate.source}
                      </span>
                    </td>
                    <td>
                      <span className="table-main-text">{dateLabel(candidate.addedAt)}</span>
                      <span className="table-sub-text">{ago(candidate.addedAt)}</span>
                    </td>
                    <td>
                      <span className="table-progress">
                        <span>
                          <span style={{ width: `${progress.percent}%` }} />
                        </span>
                        <strong>{progress.percent}%</strong>
                      </span>
                      <span className="table-sub-text">
                        {progress.application?.status === 'active'
                          ? stageByNo(progress.application.currentStage)?.title
                          : progress.application?.status === 'deployed'
                            ? 'Placement complete'
                            : 'Not mapped'}
                      </span>
                    </td>
                    <td>
                      <span className={`dashboard-status status-${candidate.status}`}>
                        {STATUS_LABELS[candidate.status] ?? candidate.status}
                      </span>
                    </td>
                    <td>
                      <button
                        className="dashboard-icon-link"
                        aria-label={`Open ${candidate.name}`}
                        onClick={() => navigate(`/candidates/${candidate.id}`)}
                      >
                        <ArrowUpRight size={14} />
                      </button>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
        {!rows.length && (
          <CompactEmpty
            icon={Users}
            title={
              candidates.length
                ? 'No candidates match these filters'
                : 'Your talent pool starts here'
            }
            detail={
              candidates.length
                ? 'Try another status or date range.'
                : 'Add your first candidate to start building your pipeline.'
            }
            action={
              !candidates.length && (
                <button className="dashboard-primary" onClick={() => navigate('/add-candidate')}>
                  <Plus size={13} /> Add candidate
                </button>
              )
            }
          />
        )}
        <footer className="candidate-pagination">
          <span>
            Showing {rows.length ? safePage * 5 + 1 : 0}–{Math.min(rows.length, (safePage + 1) * 5)}{' '}
            of {rows.length} candidates
          </span>
          <div>
            <button
              aria-label="Previous candidate page"
              disabled={safePage === 0}
              onClick={() => setPage(safePage - 1)}
            >
              <ChevronLeft size={14} />
            </button>
            <span>
              {safePage + 1} / {pageCount}
            </span>
            <button
              aria-label="Next candidate page"
              disabled={safePage === pageCount - 1}
              onClick={() => setPage(safePage + 1)}
            >
              <ChevronRight size={14} />
            </button>
          </div>
        </footer>
      </section>

      <div className="operations-grid">
        <section className="analytics-card" aria-labelledby="tasks-title">
          <CardHeading id="tasks-title" title="Open tasks">
            <button className="dashboard-text-link" onClick={() => navigate('/tasks')}>
              {tasks.length} total <ArrowUpRight size={13} />
            </button>
          </CardHeading>
          {tasks.length ? (
            <ul className="operational-list">
              {tasks.slice(0, 4).map((task) => (
                <li key={task.id}>
                  <button
                    onClick={() =>
                      task.applicationId
                        ? setOpenApp(task.applicationId)
                        : navigate(`/candidates/${task.candidateId}`)
                    }
                  >
                    <span className="operational-icon">
                      <FileCheck2 size={15} />
                    </span>
                    <span>
                      <strong>{task.title}</strong>
                      <span>{store.candidate(task.candidateId)?.name}</span>
                    </span>
                    <span
                      className={cn(
                        'task-deadline',
                        slaState(task.createdAt).overdue && 'is-overdue',
                      )}
                    >
                      {slaState(task.createdAt).label}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <CompactEmpty
              icon={CircleCheck}
              title="You're all caught up"
              detail="Open tasks will appear here."
            />
          )}
        </section>
        <section className="analytics-card" aria-labelledby="orders-title">
          <CardHeading id="orders-title" title="Active orders">
            <button className="dashboard-text-link" onClick={() => navigate('/jobs')}>
              View all <ArrowUpRight size={13} />
            </button>
          </CardHeading>
          {activeOrders.length ? (
            <ul className="operational-list">
              {activeOrders.slice(0, 3).map((order) => (
                <li key={order.id}>
                  <button onClick={() => navigate(`/orders/${order.id}`)}>
                    <span className="operational-icon is-dark">
                      <BriefcaseBusiness size={15} />
                    </span>
                    <span>
                      <strong>
                        {store.designation(order.designationId)?.title ?? 'Open position'}
                      </strong>
                      <span>{store.company(order.companyId)?.name}</span>
                    </span>
                    <span className="order-openings">
                      {order.openings}
                      <span>openings</span>
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <CompactEmpty
              icon={BriefcaseBusiness}
              title="Ready for new opportunities"
              detail="Your open orders will appear here."
            />
          )}
        </section>
        {store.can('todo_list') && (
          <section className="analytics-card" aria-labelledby="todos-title">
            <CardHeading id="todos-title" title="My to-dos">
              <button className="dashboard-text-link" onClick={() => navigate('/todo')}>
                Open list <ArrowUpRight size={13} />
              </button>
            </CardHeading>
            {myTodos.length ? (
              <ul className="operational-list">
                {myTodos.slice(0, 3).map((todo) => (
                  <li key={todo.id} className="operational-todo">
                    <CheckboxInput
                      checked={false}
                      aria-label={`Complete ${todo.title}`}
                      onChange={() => {
                        void store.toggleTodo(todo.id).catch(() => {})
                      }}
                    />
                    <span>
                      <strong>{todo.title}</strong>
                      <span>{ago(todo.dueAt)}</span>
                    </span>
                  </li>
                ))}
              </ul>
            ) : (
              <CompactEmpty
                icon={CalendarDays}
                title="A fresh start"
                detail="Add a to-do to plan your day."
              />
            )}
          </section>
        )}
        {store.can('connect_sources') && <SourceHealth />}
      </div>
      <div className="dashboard-footer">
        <span>Care India Overseas · Recruitment workspace</span>
        <span>Activity times in India Standard Time</span>
      </div>
      <StageModal applicationId={openApp} open={!!openApp} onClose={() => setOpenApp(null)} />
    </div>
  )
}

function CardHeading({ id, title, children }: { id: string; title: string; children?: ReactNode }) {
  return (
    <header className="analytics-card-heading">
      <h2 id={id}>{title}</h2>
      {children}
    </header>
  )
}

function PeriodSelect({
  label,
  value,
  onChange,
  options,
  icon,
}: {
  label: string
  value: string
  onChange: (value: string) => void
  options: string[][]
  icon?: boolean
}) {
  return (
    <label className="period-select">
      {icon && <CalendarDays size={12} />}
      <Select
        compact
        aria-label={label}
        value={value}
        onChange={(event) => onChange(event.target.value)}
      >
        {options.map(([key, title]) => (
          <option key={key} value={key}>
            {title}
          </option>
        ))}
      </Select>
    </label>
  )
}

function CompactEmpty({
  icon: Icon,
  title,
  detail,
  action,
}: {
  icon: typeof Users
  title: string
  detail: string
  action?: ReactNode
}) {
  return (
    <div className="compact-empty">
      <span>
        <Icon size={23} strokeWidth={1.5} />
      </span>
      <strong>{title}</strong>
      <p>{detail}</p>
      {action}
    </div>
  )
}
