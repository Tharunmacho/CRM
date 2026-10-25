import { useEffect, useMemo, useState, type CSSProperties } from 'react'
import { CircleCheck, Clock3, Eye, GitBranch, Pencil, Search, TimerOff, X } from 'lucide-react'
import { cn } from '@/shared/lib/cn'
import { dateLabel, slaState } from '@/shared/lib/format'
import { taskClockStart } from '@/shared/lib/task-time'
import { useStore } from '@/shared/state/crm-store'
import { STAGES, closureLabels } from '@/shared/workflow/stages'
import type { Application } from '@/shared/model/types'
import { EmptyState, PageHeader, Panel } from '@/shared/ui/display'
import { Button, Select } from '@/shared/ui/primitives'
import { StageModal } from '@/features/workflow/components/stage-modal'
import { ownerLabel } from '@/features/candidates/data-bank-pools'
import { arrivalTrend, weeklyArrivals } from '@/features/candidates/data-bank-trends'
import { OwnerText, SOURCE_LABEL, SourceDot, SourceIcon, StageChip, shortDate } from '@/features/candidates/components/data-bank-columns'
import { CountryFlag, KpiCards, Pager, StatusDot, ViewToggle, Who, readView, saveView, type DbView } from '@/features/candidates/components/db-parts'
import { DbTable, TABLE_MIN_WIDTH, useElementWidth, type DbColumn } from '@/features/candidates/components/db-table'
import '@/styles/data-bank-v2.css'

type TabKey = 'active' | 'deployed' | 'closed'

const STATUS_TEXT: Record<string, string> = {
  active: 'Active',
  deployed: 'Deployed',
  closed: 'Closed',
}

const TAB_TITLE: Record<TabKey, string> = { active: 'In progress', deployed: 'Deployed', closed: 'Closed' }
const PAGE_SIZE = 20
const Dash = () => <span className="db-muted" aria-label="None">–</span>

/**
 * Every candidate mapped to an order. This is the only place a stage can be worked —
 * the Data Bank stays a read-only directory.
 */
export default function MappedCandidatesPage() {
  const store = useStore()
  const [tab, setTab] = useState<TabKey>('active')
  const [overdueOnly, setOverdueOnly] = useState(false)
  const [query, setQuery] = useState('')
  const [stageFilter, setStageFilter] = useState('all')
  const [ownerFilter, setOwnerFilter] = useState('all')
  const [designationFilter, setDesignationFilter] = useState('all')
  const [companyFilter, setCompanyFilter] = useState('all')
  const [sourceFilter, setSourceFilter] = useState('all')
  const [editing, setEditing] = useState<string | null>(null)
  const [view, setView] = useState<DbView>(() => readView('mapped'))
  const [page, setPage] = useState(0)
  const [sort, setSort] = useState<{ key: string; dir: 'asc' | 'desc' } | null>(null)
  const [boxRef, boxWidth] = useElementWidth<HTMLDivElement>()

  const isSupervisor = store.can('all_reports')

  const designationOf = (a: Application) => { const o = store.order(a.orderId); return o ? (store.designation(o.designationId)?.title ?? '') : '' }
  const companyOf = (a: Application) => { const o = store.order(a.orderId); return o ? (store.company(o.companyId)?.name ?? '') : '' }
  const sourceOf = (a: Application) => store.candidate(a.candidateId)?.source ?? ''

  const scoped = useMemo(() => {
    const q = query.trim().toLowerCase()
    return store.applications.filter((a) => {
      const cand = store.candidate(a.candidateId)
      if (!cand) return false
      if (ownerFilter !== 'all' && cand.ownerId !== ownerFilter) return false
      if (designationFilter !== 'all' && designationOf(a) !== designationFilter) return false
      if (companyFilter !== 'all' && companyOf(a) !== companyFilter) return false
      if (sourceFilter !== 'all' && cand.source !== sourceFilter) return false
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
  }, [store.applications, store.candidates, query, ownerFilter, designationFilter, companyFilter, sourceFilter])

  const buckets = useMemo(
    () => ({
      active: scoped.filter((a) => a.status === 'active'),
      deployed: scoped.filter((a) => a.status === 'deployed'),
      closed: scoped.filter((a) => a.status === 'closed'),
    }),
    [scoped],
  )

  // Real mapping history per card: when each application was created.
  const history = useMemo(() => {
    const make = (status: string) => {
      const dates = scoped.filter((a) => a.status === status).map((a) => a.createdAt)
      return { trend: arrivalTrend(dates), spark: weeklyArrivals(dates) }
    }
    return { active: make('active'), deployed: make('deployed'), closed: make('closed') }
  }, [scoped])

  // Distinct choices for the designation, company and source filters, from every mapped application.
  const choices = useMemo(() => {
    const all = store.applications.filter((a) => store.candidate(a.candidateId))
    const uniq = (values: string[]) => [...new Set(values.filter(Boolean))].sort((x, y) => x.localeCompare(y))
    return { designations: uniq(all.map(designationOf)), companies: uniq(all.map(companyOf)), sources: uniq(all.map(sourceOf)) }
  }, [store.applications, store.candidates, store.orders])

  const sorters: Record<string, (a: Application) => string | number> = {
    candidate: (a) => store.candidate(a.candidateId)?.name ?? '',
    passport: (a) => store.candidate(a.candidateId)?.passportNo ?? '',
    order: (a) => store.order(a.orderId)?.ref ?? '',
    designation: designationOf,
    company: companyOf,
    owner: (a) => ownerLabel(store.user(store.candidate(a.candidateId)?.ownerId ?? '')?.name),
    stage: (a) => a.currentStage,
    mapped: (a) => a.createdAt,
  }

  // The 48-hour clock of the open task runs from its restart (taskClockStart), not from when the task was first created.
  const taskOf = (a: Application) => store.tasks.find((t) => t.applicationId === a.id && !t.completedAt && !t.cancelledAt)
  const isOverdue = (a: Application) => { const task = taskOf(a); return task ? slaState(taskClockStart(task)).overdue : false }
  const hoursLate = (a: Application) => { const task = taskOf(a); return task ? Math.floor(slaState(taskClockStart(task)).hoursOverdue) : 0 }

  // Most recently mapped first, which is the order the desk works in, unless a header is chosen.
  const rows = useMemo(() => {
    let base = buckets[tab]
    if (tab === 'active' && stageFilter !== 'all') base = base.filter((a) => String(a.currentStage) === stageFilter)
    if (tab === 'active' && overdueOnly) base = base.filter(isOverdue)
    const list = [...base].sort((a, b) => +new Date(b.createdAt) - +new Date(a.createdAt))
    if (!sort || !sorters[sort.key]) return list
    const value = sorters[sort.key]
    const factor = sort.dir === 'asc' ? 1 : -1
    return list.sort((a, b) => {
      const x = value(a)
      const y = value(b)
      return factor * (typeof x === 'number' && typeof y === 'number' ? x - y : String(x).localeCompare(String(y), undefined, { numeric: true }))
    })
  }, [buckets, tab, stageFilter, overdueOnly, sort, store.candidates, store.orders, store.tasks])

  useEffect(() => { setPage(0) }, [tab, overdueOnly, stageFilter, ownerFilter, designationFilter, companyFilter, sourceFilter, query, sort])
  const changeView = (next: DbView) => { setView(next); saveView('mapped', next) }
  const onFile = store.applications.filter((a) => a.status === tab && store.candidate(a.candidateId)).length
  const hasFilters = Boolean(query.trim()) || stageFilter !== 'all' || ownerFilter !== 'all' || designationFilter !== 'all' || companyFilter !== 'all' || sourceFilter !== 'all' || overdueOnly
  const clearFilters = () => { setQuery(''); setStageFilter('all'); setOwnerFilter('all'); setDesignationFilter('all'); setCompanyFilter('all'); setSourceFilter('all'); setOverdueOnly(false) }
  const forceCards = boxWidth > 0 && boxWidth < TABLE_MIN_WIDTH
  const showCards = forceCards || view === 'cards'

  const statusLabel = (a: Application) => (a.status === 'closed' ? (a.closureReason ? closureLabels[a.closureReason] : STATUS_TEXT.closed) : isOverdue(a) ? 'Overdue' : STATUS_TEXT[a.status])
  const statusDot = (a: Application) => (
    <span className="db-cell">
      <StatusDot title={statusLabel(a)} tone={a.status === 'closed' || isOverdue(a) ? 'bad' : a.status === 'deployed' ? 'ok' : 'info'}>{statusLabel(a)}</StatusDot>
      {a.status === 'active' && isOverdue(a) && <span className="db-late" title="Time past the 48-hour deadline of the open task">{hoursLate(a)}h late</span>}
    </span>
  )
  const canWork = (a: Application) => {
    const c = store.candidate(a.candidateId)
    return a.status === 'active' && !!c && (store.can('work_any_candidate') || c.ownerId === store.me.id)
  }
  const openButton = (a: Application) => (
    <Button size="sm" variant={a.status === 'active' ? 'primary' : 'secondary'} icon={canWork(a) ? <Pencil className="h-3.5 w-3.5" /> : <Eye className="h-3.5 w-3.5" />} onClick={() => setEditing(a.id)}>
      {canWork(a) ? 'Open' : 'View'}
    </Button>
  )

  const columns: DbColumn<Application>[] = [
    {
      key: 'candidate', header: 'Candidate', sortKey: 'candidate', width: 132,
      render: (a) => { const c = store.candidate(a.candidateId)!; return <Who compact name={c.name} sub={<span className="ident" title={c.phone}>{c.phone}</span>} /> },
    },
    {
      key: 'passport', header: 'Passport', sortKey: 'passport', width: 92,
      render: (a) => { const c = store.candidate(a.candidateId)!; return c.passportNo ? <span className="ident db-nowrap" title={`Passport ${c.passportNo}`}>{c.passportNo}</span> : <Dash /> },
    },
    { key: 'order', header: 'Order', sortKey: 'order', width: 92, render: (a) => <span className="db-order-chip ident" title={store.orderLabel(a.orderId)}>{store.order(a.orderId)?.ref ?? '–'}</span> },
    { key: 'designation', header: 'Job title', sortKey: 'designation', width: 112, render: (a) => <span className="db-wrap" title={designationOf(a)}>{designationOf(a) || <Dash />}</span> },
    {
      key: 'company', header: 'Company', sortKey: 'company', width: 156,
      render: (a) => {
        const o = store.order(a.orderId)
        const name = companyOf(a)
        return <span className="db-with-flag" title={name}><CountryFlag country={o?.country ?? (o ? store.company(o.companyId)?.country : undefined)} /><span className="db-wrap">{name || <Dash />}</span></span>
      },
    },
    { key: 'source', header: 'Source', width: 60, render: (a) => <SourceIcon source={sourceOf(a) || 'other'} /> },
    { key: 'owner', header: 'Recruiter', sortKey: 'owner', width: 104, render: (a) => <OwnerText candidate={store.candidate(a.candidateId)!} store={store} /> },
    {
      key: 'stage', header: 'Stage', sortKey: 'stage', width: 128,
      render: (a) => (a.status === 'closed' && a.closureReason ? <span className="db-wrap">{closureLabels[a.closureReason]}</span> : <StageChip stage={a.currentStage} />),
    },
    { key: 'status', header: 'Status', width: 96, render: (a) => statusDot(a) },
    { key: 'mapped', header: 'Matched', sortKey: 'mapped', width: 82, render: (a) => <span className="db-nowrap" title={dateLabel(a.createdAt)}>{shortDate(a.createdAt)}</span> },
    { key: 'action', header: '', width: 92, align: 'right', render: (a) => openButton(a) },
  ]

  const emptyState = (
    <EmptyState
      title={
        hasFilters ? 'No matches found'
          : tab === 'active' ? 'Nobody is in progress'
            : tab === 'deployed' ? 'Nobody deployed yet' : 'No closed applications'
      }
      description={
        hasFilters ? 'Try another search or filter, or clear them to see everything.'
          : tab === 'active' ? 'Match candidates to an order from the Jobs page and they will show up here.'
            : tab === 'deployed' ? 'A candidate shows up here once stage 11 is done.'
              : 'Matches that ended at prescreening, interview or medical show up here.'
      }
      action={hasFilters ? <Button onClick={clearFilters}>Clear filters</Button> : undefined}
    />
  )

  const pageRows = rows.slice(page * PAGE_SIZE, page * PAGE_SIZE + PAGE_SIZE)
  const overdueTasks = store.visibleTasks.filter(
    (task) =>
      !task.completedAt &&
      !task.cancelledAt &&
      slaState(taskClockStart(task)).overdue &&
      buckets.active.some((application) => application.id === task.applicationId),
  ).length
  const selects: { label: string; value: string; onChange: (value: string) => void; any: string; options: [string, string][] }[] = []
  if (tab === 'active') selects.push({ label: 'Filter by stage', value: stageFilter, onChange: setStageFilter, any: 'Every stage', options: STAGES.map((s) => [String(s.no), `${s.no}. ${s.title}`]) })
  selects.push({ label: 'Filter by job title', value: designationFilter, onChange: setDesignationFilter, any: 'Any job title', options: choices.designations.map((d) => [d, d]) })
  selects.push({ label: 'Filter by company', value: companyFilter, onChange: setCompanyFilter, any: 'Any company', options: choices.companies.map((d) => [d, d]) })
  selects.push({ label: 'Filter by source', value: sourceFilter, onChange: setSourceFilter, any: 'Any source', options: choices.sources.map((d) => [d, SOURCE_LABEL[d] ?? d]) })
  if (isSupervisor) selects.push({ label: 'Filter by recruiter', value: ownerFilter, onChange: setOwnerFilter, any: 'All recruiters', options: store.users.filter((u) => u.role === 'staff' && u.active).map((u) => [u.id, u.name]) })

  return (
    <div className="db mc">
      <PageHeader
        title="Matched candidates"
        description="Candidates who are matched to an order. Open one to work on its 11 stages, from prescreening to deployment."
      />

      <KpiCards
        items={[
          { label: 'In progress', value: buckets.active.length, detail: 'Being worked on now', icon: GitBranch, ...history.active, active: tab === 'active' && !overdueOnly, onSelect: () => { setTab('active'); setOverdueOnly(false) } },
          { label: 'Deployed', value: buckets.deployed.length, detail: 'Placed in a job abroad', icon: CircleCheck, tone: 'ok', ...history.deployed, active: tab === 'deployed', onSelect: () => { setTab('deployed'); setOverdueOnly(false) } },
          { label: 'Closed', value: buckets.closed.length, detail: 'Ended before a placement', icon: Clock3, tone: 'quiet', ...history.closed, active: tab === 'closed', onSelect: () => { setTab('closed'); setOverdueOnly(false) } },
          { label: 'Overdue tasks', value: overdueTasks, detail: 'Past their 48-hour deadline', icon: TimerOff, tone: 'bad', active: tab === 'active' && overdueOnly, onSelect: () => { setTab('active'); setOverdueOnly((value) => (tab === 'active' ? !value : true)) } },
        ]}
      />

      <Panel flush className="db-panel">
        <div className="db-panel-head">
          <div className="min-w-0">
            <h2 className="db-panel-title">{overdueOnly && tab === 'active' ? 'Overdue in progress' : TAB_TITLE[tab]}</h2>
            <p className="db-panel-sub tnum">Showing {rows.length} of {onFile} matches</p>
          </div>
          <div className="db-panel-tools">
            <label className="db-search">
              <Search size={15} aria-hidden="true" />
              <input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search candidates, passports or orders" aria-label="Search candidates, passports or orders" />
            </label>
            {!forceCards && <ViewToggle view={view} onChange={changeView} />}
          </div>
        </div>

        <div className="db-filters" aria-label="Filters">
          <div className="db-filterbar" style={{ '--db-cols': selects.length } as CSSProperties}>
            <span className="db-filterbar-label">Filters</span>
            {selects.map((select, index) => (
              <span className={cn('db-sel', index === selects.length - 1 && selects.length % 2 === 1 && 'is-last-odd')} key={select.label}>
                <Select aria-label={select.label} value={select.value} onChange={(e) => select.onChange(e.target.value)}>
                  <option value="all">{select.any}</option>
                  {select.options.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                </Select>
              </span>
            ))}
            <span className="db-more-cell">
              {hasFilters && <button type="button" className="db-clear" onClick={clearFilters}><X size={13} aria-hidden="true" />Clear all</button>}
            </span>
          </div>
        </div>

        <div ref={boxRef} className="db-box">
          {rows.length === 0 ? emptyState : showCards ? (
            <ul className="db-grid" aria-label="Matched candidates">
              {pageRows.map((a) => {
                const c = store.candidate(a.candidateId)!
                const o = store.order(a.orderId)
                return (
                  <li key={a.id} className={cn('db-mini', isOverdue(a) && 'is-late')} tabIndex={0} onClick={() => setEditing(a.id)}
                    onKeyDown={(event) => { if (event.target === event.currentTarget && (event.key === 'Enter' || event.key === ' ')) { event.preventDefault(); setEditing(a.id) } }}>
                    <div className="db-mini-head">
                      <Who name={c.name} sub={<span className="ident">{c.phone}</span>} />
                      {statusDot(a)}
                    </div>
                    <dl className="db-mini-meta">
                      <div><dt>Order</dt><dd><span className="db-order-chip ident">{o?.ref ?? '—'}</span></dd></div>
                      <div><dt>Stage</dt><dd>{a.status === 'closed' && a.closureReason ? closureLabels[a.closureReason] : <StageChip stage={a.currentStage} />}</dd></div>
                      <div><dt>Job title</dt><dd>{designationOf(a) || '—'}</dd></div>
                      <div><dt>Company</dt><dd className="db-with-flag"><CountryFlag country={o?.country ?? (o ? store.company(o.companyId)?.country : undefined)} /><span className="db-ellip" title={companyOf(a)}>{companyOf(a) || '—'}</span></dd></div>
                      <div><dt>Recruiter</dt><dd><OwnerText candidate={c} store={store} /></dd></div>
                      <div><dt>Source</dt><dd><SourceDot source={c.source} /></dd></div>
                    </dl>
                    <div className="db-mini-foot">
                      <span className="db-mini-when">Matched {dateLabel(a.createdAt)}</span>
                      <span onClick={(event) => event.stopPropagation()}>{openButton(a)}</span>
                    </div>
                  </li>
                )
              })}
            </ul>
          ) : (
            <DbTable
              label="Matched candidates"
              rows={pageRows}
              columns={columns}
              rowKey={(a) => a.id}
              sort={sort}
              onSort={(key) => setSort((current) => ({ key, dir: current?.key === key && current.dir === 'asc' ? 'desc' : 'asc' }))}
              rowClassName={(a) => (isOverdue(a) ? 'is-late' : undefined)}
            />
          )}
        </div>
        {rows.length > 0 && <Pager page={page} pageSize={PAGE_SIZE} total={rows.length} noun="matches" onPage={setPage} />}
      </Panel>

      <StageModal applicationId={editing} open={!!editing} onClose={() => setEditing(null)} />
    </div>
  )
}
