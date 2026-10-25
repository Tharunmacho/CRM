import { useMemo, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { AlertTriangle, ArrowLeft, ArrowRight, BriefcaseBusiness, Building2, CalendarClock, CalendarX2, ClipboardCopy, Coins, FileText, Gift, Globe2, History, Link2, MapPin, Pause, Pencil, Play, Target, Trash2, Unlink, UserCheck, Users, Wallet } from 'lucide-react'
import { cn } from '@/shared/lib/cn'
import { dateLabel, dateTimeLabel, money } from '@/shared/lib/format'
import { useStore } from '@/shared/state/crm-store'
import { closureLabels, stageByNo } from '@/shared/workflow/stages'
import type { ClosureReason } from '@/shared/model/types'
import type { Application } from '@/shared/model/types'
import { EmptyState, PageHeader, Panel, PanelHeader } from '@/shared/ui/display'
import { Button, SearchInput } from '@/shared/ui/primitives'
import { SummaryCards } from '@/shared/ui/workspace'
import { DataTable, type Column } from '@/shared/ui/table'
import { StageModal } from '@/features/workflow/components/stage-modal'
import { DeleteRecordDialog } from '@/shared/components/delete-dialog'
import { CandidateMatchDrawer } from '@/features/jobs/components/candidate-match-drawer'
import { OrderDialog, UnmapCandidateDialog } from '@/features/jobs/components/job-dialogs'
import { Avatar, Flag, StageMini } from '@/features/jobs/components/identity'
import { ActionMenu, DueChip, MenuButton, OrderStatusText, useMinWidth } from '@/features/jobs/components/order-parts'
import { deadlineNote, daysToDeadline, isOverdue } from '@/features/jobs/order-meta'
import { useOrderActions } from '@/features/jobs/use-order-actions'
import { isMatched, isSelected, orderCounts } from '@/features/jobs/order-counts'
import { RecordAudit } from '@/features/master-data/components/record-audit'
import { useWorkspaceReady } from '@/features/master-data/use-workspace-ready'
import { SnapshotState } from '@/shared/ui/page-loading'
import '@/styles/jobs.css'

type AppFilter = 'all' | 'active' | 'selected' | 'deployed' | 'closed'
const FILTERS: [AppFilter, string][] = [['all', 'All'], ['active', 'Active'], ['selected', 'Selected'], ['deployed', 'Deployed'], ['closed', 'Closed']]
/** Plain wording for why a candidate's application closed. */
const closureText = (reason: ClosureReason) => (reason === 'unmapped' ? 'Removed from this order' : closureLabels[reason])
const APP_STATUS: Record<Application['status'], { text: string; tone: string }> = {
  active: { text: 'Active', tone: 's-open' },
  deployed: { text: 'Deployed', tone: 's-filled' },
  closed: { text: 'Closed', tone: 's-closed' },
}

export default function OrderDetailPage() {
  const { id } = useParams()
  const store = useStore()
  const snapshot = useWorkspaceReady(id ? `order:${id}` : null)
  const navigate = useNavigate()
  const actions = useOrderActions()
  const order = store.orders.find((o) => o.id === id)

  const [matching, setMatching] = useState(false)
  const [openApp, setOpenApp] = useState<string | null>(null)
  const [unmapping, setUnmapping] = useState<string | null>(null)
  const [unmapSaving, setUnmapSaving] = useState(false)
  const [unmapError, setUnmapError] = useState('')
  const [deleting, setDeleting] = useState(false)
  const [editing, setEditing] = useState(false)
  const [history, setHistory] = useState(false)
  const [filter, setFilter] = useState<AppFilter>('all')
  const [query, setQuery] = useState('')
  const [cardLimit, setCardLimit] = useState(12)
  const wideScreen = useMinWidth(1280)

  const orderId = order?.id
  const apps = useMemo(() => store.applications.filter((a) => a.orderId === orderId), [store.applications, orderId])
  const visibleApps = useMemo(() => {
    const q = query.trim().toLowerCase()
    return apps.filter((a) => {
      if (filter === 'active' && !isMatched(a)) return false
      if (filter === 'selected' && !isSelected(a)) return false
      if (filter === 'deployed' && a.status !== 'deployed') return false
      if (filter === 'closed' && a.status !== 'closed') return false
      if (!q) return true
      const candidate = store.candidate(a.candidateId)
      return [candidate?.name, candidate?.phone, candidate?.ref, store.user(candidate?.ownerId ?? '')?.name].some((value) => (value ?? '').toLowerCase().includes(q))
    })
  }, [apps, filter, query, store.candidates, store.users])

  if (!snapshot.ready) return <SnapshotState variant="detail" error={snapshot.error} onRetry={snapshot.retry} retryLabel="Try again" label="Loading order" />
  if (!order) {
    return (
      <EmptyState
        title="We could not find this order"
        description="It may have been deleted."
        action={<Button onClick={() => navigate('/jobs')}>Back to Jobs</Button>}
      />
    )
  }

  const client = store.company(order.companyId)
  const designation = store.designation(order.designationId)
  if (!client || !designation) return <EmptyState title="This order's company or job title is missing" description="Refresh the page to load the latest records." action={<Button onClick={snapshot.retry}>Refresh</Button>} />
  const canMatch = order.status === 'open'
  const counts = orderCounts(apps)
  const deployedCount = counts.deployed
  const unmapApp = apps.find((a) => a.id === unmapping)
  const activeApps = apps.filter(isMatched)
  const place = [order.city, order.country].filter(Boolean).join(', ')
  const note = deadlineNote(order)
  const overdue = isOverdue(order)
  const late = daysToDeadline(order.deadline)
  const busy = actions.busyId === order.id

  const copyShortlist = async () => {
    const lines = apps.filter((a) => a.status !== 'closed').map((a, index) => {
      const candidate = store.candidate(a.candidateId)
      const stage = a.status === 'deployed' ? 'Deployed' : `Stage ${a.currentStage}, ${stageByNo(a.currentStage).title}`
      return `${index + 1}. ${candidate?.name ?? 'Candidate'}${candidate?.phone ? ` (${candidate.phone})` : ''} · ${stage} · Recruiter ${store.user(candidate?.ownerId ?? '')?.name ?? 'unassigned'}`
    })
    const text = [
      `Shortlist for ${designation.title}, ${client.name} (${order.ref})`,
      `Openings: ${order.openings} · Deployed: ${deployedCount} · Matched: ${counts.matched} · Selected: ${counts.selected}`,
      '',
      ...(lines.length ? lines : ['No candidates are matched to this order yet.']),
    ].join('\n')
    try { await navigator.clipboard.writeText(text); store.notify('Shortlist copied') }
    catch { store.notify('The shortlist could not be copied. Copy it from the table instead.', 'warn') }
  }

  const matchedColumns: Column<Application>[] = [
    {
      key: 'candidate',
      header: 'Candidate',
      sortable: true,
      width: '24%',
      value: (a) => store.candidate(a.candidateId)?.name ?? '',
      render: (a) => {
        const c = store.candidate(a.candidateId)
        return (
          <div className="jv-person">
            <Avatar name={c?.name ?? '?'} />
            <div className="min-w-0">
              <p className="jv-cell-title truncate">{c?.name ?? 'Candidate not found'}</p>
              <p className="jv-cell-sub ident">{c?.phone ?? a.candidateId}</p>
            </div>
          </div>
        )
      },
    },
    {
      key: 'stage',
      header: 'Stage',
      width: '25%',
      sortable: true,
      filterable: true,
      value: (a) =>
        a.status === 'closed' && a.closureReason
          ? closureText(a.closureReason)
          : `${a.currentStage}. ${stageByNo(a.currentStage).title}`,
      render: (a) => (
        <div className="jv-stage-cell">
          <StageMini no={a.currentStage} closed={a.status === 'closed'} />
          {a.status === 'closed' && a.closureReason && <span className="jv-stage-note">{closureText(a.closureReason)}</span>}
        </div>
      ),
    },
    {
      key: 'owner',
      header: 'Recruiter',
      width: '13%',
      sortable: true,
      filterable: true,
      value: (a) => store.user(store.candidate(a.candidateId)?.ownerId ?? '')?.name ?? '',
      render: (a) => {
        const owner = store.user(store.candidate(a.candidateId)?.ownerId ?? '')?.name
        return owner ? <span className="jv-owner"><Avatar name={owner} size="sm" />{owner.split(' ')[0]}</span> : <span className="text-ink-400">Not assigned</span>
      },
    },
    {
      key: 'updated',
      header: 'Updated',
      width: '15%',
      sortable: true,
      value: (a) => a.updatedAt,
      render: (a) => <span className="jv-updated">{dateTimeLabel(a.updatedAt)}</span>,
    },
    {
      key: 'status',
      header: 'Status',
      width: '11%',
      filterable: true,
      value: (a) => a.status,
      render: (a) => <span className={cn('jv-status', APP_STATUS[a.status].tone)}><i aria-hidden="true" />{APP_STATUS[a.status].text}</span>,
    },
    {
      key: 'actions',
      header: '',
      align: 'right',
      width: '12%',
      render: (a) => (
        <div className="jv-row-actions" onClick={(e) => e.stopPropagation()}>
          {a.status === 'active' && store.can('map_candidates') && (
            <button
              type="button"
              onClick={() => { setUnmapping(a.id); setUnmapError('') }}
              aria-label={`Remove ${store.candidate(a.candidateId)?.name ?? 'candidate'} from this order`}
              title="Remove from order"
              className="jv-icon-btn"
            >
              <Unlink size={15} aria-hidden="true" />
            </button>
          )}
          <button type="button" className="jv-open-btn is-icon" aria-label={`Open stages for ${store.candidate(a.candidateId)?.name ?? 'candidate'}`} title="Open stages" onClick={() => setOpenApp(a.id)}>
            <ArrowRight size={15} aria-hidden="true" />
          </button>
        </div>
      ),
    },
  ]

  return (
    <div className="jv">
      <Link to={`/jobs?designation=${encodeURIComponent(order.designationId)}`} className="jv-back">
        <ArrowLeft className="h-3.5 w-3.5" />
        Jobs / Positions
      </Link>

      <PageHeader
        title={designation.title}
        description={[client.name, place].filter(Boolean).join(' · ')}
        actions={
          <>
            <Button variant="primary" icon={<Link2 className="h-4 w-4" />} disabled={!canMatch} title={canMatch ? undefined : 'Matching is off while the order is not open'} onClick={() => setMatching(true)}>Match candidates</Button>
            <Button icon={<Pencil className="h-4 w-4" />} onClick={() => setEditing(true)}>Edit order</Button>
            <Button icon={<History className="h-4 w-4" />} onClick={() => setHistory(true)}>History</Button>
            <ActionMenu label="More actions for this order" text="More">
              {(close) => (
                <>
                  {order.status === 'open' && <MenuButton icon={<Pause size={15} />} onClick={() => { close(); void actions.setStatus(order, 'on_hold') }}>Put on hold</MenuButton>}
                  {order.status === 'on_hold' && <MenuButton icon={<Play size={15} />} onClick={() => { close(); void actions.setStatus(order, 'open') }}>Start matching again</MenuButton>}
                  <MenuButton icon={<ClipboardCopy size={15} />} onClick={() => { close(); void copyShortlist() }}>Copy shortlist</MenuButton>
                  {store.can('delete_records') && <MenuButton danger icon={<Trash2 size={15} />} onClick={() => { close(); setDeleting(true) }}>Delete order</MenuButton>}
                </>
              )}
            </ActionMenu>
          </>
        }
      >
        <div className="jv-meta">
          <span className="ident">{order.ref}</span>
          <OrderStatusText order={order} />
          <DueChip order={order} />
          <span className="jv-meta-progress" title={`${deployedCount} of ${order.openings} openings deployed`}>
            <span className="jv-bar" aria-hidden="true"><span style={{ width: `${Math.min(100, order.openings ? (deployedCount / order.openings) * 100 : 0)}%` }} /></span>
            {deployedCount} of {order.openings} deployed
          </span>
        </div>
      </PageHeader>

      {overdue && (
        <div className="jv-alert" role="alert">
          <div className="jv-alert-copy">
            <AlertTriangle size={18} aria-hidden="true" />
            <div>
              <strong>{late === 0 ? 'This order is due today' : `The deadline passed ${Math.abs(late ?? 0)} ${Math.abs(late ?? 0) === 1 ? 'day' : 'days'} ago`}</strong>
              <span>The deadline was {dateLabel(order.deadline)}. Extend it, or use Edit order to put the order on hold or close it.</span>
            </div>
          </div>
          <div className="jv-alert-actions">
            <span>Extend by</span>
            {[7, 15, 30].map((days) => <Button key={days} size="sm" disabled={busy} onClick={() => void actions.extend(order, days)}>+{days} days</Button>)}
            <Button size="sm" disabled={busy} onClick={() => setEditing(true)}>Choose date</Button>
          </div>
        </div>
      )}

      <SummaryCards
        items={[
          { label: 'Openings', value: order.openings, detail: 'People needed', icon: Users },
          { label: 'Matched', value: counts.matched, detail: 'Candidates on this order', icon: Link2, onSelect: () => setFilter(filter === 'active' ? 'all' : 'active'), active: filter === 'active' },
          { label: 'Selected', value: counts.selected, detail: 'Past the interview stage', icon: Target, onSelect: () => setFilter(filter === 'selected' ? 'all' : 'selected'), active: filter === 'selected' },
          { label: 'Deployed', value: deployedCount, detail: `${apps.filter((a) => a.status === 'closed').length} closed`, icon: UserCheck, onSelect: () => setFilter(filter === 'deployed' ? 'all' : 'deployed'), active: filter === 'deployed' },
        ]}
      />

      <div className="jv-detail-grid">
        <Panel flush>
          <PanelHeader title="Order details" meta="What was agreed with the client for this order" action={<OrderStatusText order={order} />} />
          <div className="jv-card-body">
            <dl className="jv-fields">
              <div className="jv-field"><dt><FileText size={14} aria-hidden="true" />Order reference</dt><dd className="ident">{order.ref}</dd></div>
              <div className="jv-field"><dt><Building2 size={14} aria-hidden="true" />Client</dt><dd><Link to={`/clients/${client.clientId}`}>{client.name}</Link></dd></div>
              <div className="jv-field"><dt><BriefcaseBusiness size={14} aria-hidden="true" />Job title</dt><dd>{designation.title}</dd></div>
              <div className="jv-field"><dt><Users size={14} aria-hidden="true" />Number of openings</dt><dd className="tnum">{order.openings}</dd></div>
              <div className="jv-field"><dt><Globe2 size={14} aria-hidden="true" />Work country</dt><dd>{order.country ? <span className="jv-place"><Flag country={order.country} />{order.country}</span> : <i>Not set</i>}</dd></div>
              <div className="jv-field"><dt><MapPin size={14} aria-hidden="true" />Work city</dt><dd>{order.city || <i>Not set</i>}</dd></div>
              <div className="jv-field"><dt><Wallet size={14} aria-hidden="true" />Salary</dt><dd className="tnum">{order.salary ? `${money(order.salary, order.currency)} / month` : <i>Not set</i>}</dd></div>
              <div className="jv-field"><dt><Coins size={14} aria-hidden="true" />Currency</dt><dd>{order.currency}</dd></div>
              <div className="jv-field"><dt><FileText size={14} aria-hidden="true" />Contract period</dt><dd>{order.contractPeriod || <i>Not set</i>}</dd></div>
              <div className="jv-field"><dt><CalendarClock size={14} aria-hidden="true" />Interview date</dt><dd>{order.interviewDate ? dateLabel(order.interviewDate) : <i>Not set</i>}</dd></div>
              <div className="jv-field"><dt><CalendarX2 size={14} aria-hidden="true" />Order deadline</dt><dd className={cn(overdue && 'jv-late')}>{order.deadline ? dateLabel(order.deadline) : <i>No deadline</i>}</dd></div>
              <div className="jv-field"><dt><Gift size={14} aria-hidden="true" />Benefits</dt><dd>{order.benefits.length ? <span className="jv-chips">{order.benefits.map((b) => <span key={b}>{b}</span>)}</span> : <i>None added</i>}</dd></div>
            </dl>
          </div>
        </Panel>

        <Panel flush>
          <PanelHeader title="Candidate requirements" meta="Used when matching candidates" action={<Button className="whitespace-nowrap" onClick={() => navigate(`/clients/${client.clientId}`)}>View client</Button>} />
          <div className="jv-card-body">
            <p className="jv-require-title">{designation.title}<span>{designation.minExperience > 0 ? `${designation.minExperience}+ years experience` : 'Any experience'}</span></p>
            <p className="jv-require-copy">{designation.description || 'Check each candidate\'s skills and experience before you start. If something is missing from a profile, ask the candidate.'}</p>
            <p className="jv-subtle">Required skills</p>
            <div className="jv-chips">{designation.requiredSkills.length ? designation.requiredSkills.map((skill) => <span key={skill}>{skill}</span>) : <i>No skills added</i>}</div>
            {!canMatch && <p role="status" className="jv-pause">You cannot match new candidates because this order is {order.status === 'on_hold' ? 'on hold' : order.status}. Candidates already matched stay on the order.</p>}
          </div>
        </Panel>
      </div>

      <Panel flush className="jv-matched">
        <PanelHeader
          title="Matched candidates"
          meta={`${apps.length} ${apps.length === 1 ? 'candidate' : 'candidates'} on this order, with their stage and recruiter`}
          action={<Button variant="primary" icon={<Link2 className="h-4 w-4" />} disabled={!canMatch} onClick={() => setMatching(true)}>Match candidates</Button>}
        />
        {apps.length > 0 && (
          <div className="jv-matched-tools">
            <span className="jv-filters-label">Filters</span>
            <div className="jv-chips-row" role="group" aria-label="Show candidates by status">
              {FILTERS.map(([key, label]) => <button key={key} type="button" className={cn('jv-chip', filter === key && 'is-on')} aria-pressed={filter === key} onClick={() => setFilter(key)}>{label}</button>)}
            </div>
            <SearchInput value={query} onChange={setQuery} placeholder="Search candidate or recruiter" className="jv-search" />
          </div>
        )}
        {apps.length === 0 ? (
          <EmptyState
            icon={<Users size={30} strokeWidth={1.4} />}
            title="No candidates matched yet"
            description="Search the Data Bank and match candidates to this order. Once matched, you can work on their stages."
            action={<Button variant="primary" icon={<Link2 className="h-4 w-4" />} disabled={!canMatch} onClick={() => setMatching(true)}>Match candidates</Button>}
          />
        ) : visibleApps.length === 0 ? (
          <EmptyState
            icon={<Users size={30} strokeWidth={1.4} />}
            title="No candidates in this view"
            description="Try a different status or search. Or show everyone on this order."
            action={<Button onClick={() => { setFilter('all'); setQuery('') }}>Show all candidates</Button>}
          />
        ) : !wideScreen ? (
          <>
            <ul className="jv-mcards">
              {visibleApps.slice(0, cardLimit).map((a) => {
                const c = store.candidate(a.candidateId)
                const owner = store.user(c?.ownerId ?? '')?.name
                return (
                  <li key={a.id} className="jv-mcard">
                    <div className="jv-mcard-head">
                      <div className="jv-person"><Avatar name={c?.name ?? '?'} /><div className="jv-cell"><p className="jv-cell-title">{c?.name ?? 'Candidate not found'}</p><p className="jv-cell-sub ident">{c?.phone ?? a.candidateId}</p></div></div>
                      <span className={cn('jv-status', APP_STATUS[a.status].tone)}><i aria-hidden="true" />{APP_STATUS[a.status].text}</span>
                    </div>
                    <StageMini no={a.currentStage} closed={a.status === 'closed'} />
                    {a.status === 'closed' && a.closureReason && <p className="jv-stage-note">{closureText(a.closureReason)}</p>}
                    <dl className="jv-mcard-meta">
                      <div><dt>Recruiter</dt><dd>{owner ? <span className="jv-owner"><Avatar name={owner} size="sm" />{owner.split(' ')[0]}</span> : 'Not assigned'}</dd></div>
                      <div><dt>Updated</dt><dd>{dateTimeLabel(a.updatedAt)}</dd></div>
                    </dl>
                    <div className="jv-mcard-foot">
                      {a.status === 'active' && store.can('map_candidates') && <Button size="sm" icon={<Unlink className="h-3.5 w-3.5" />} onClick={() => { setUnmapping(a.id); setUnmapError('') }}>Remove from order</Button>}
                      <Button size="sm" variant="primary" onClick={() => setOpenApp(a.id)}>Open stages</Button>
                    </div>
                  </li>
                )
              })}
            </ul>
            {visibleApps.length > cardLimit && <div className="jv-more"><Button size="sm" onClick={() => setCardLimit((value) => value + 12)}>Show more candidates</Button></div>}
          </>
        ) : (
          <DataTable
            rows={visibleApps}
            columns={matchedColumns}
            rowKey={(a) => a.id}
            onRowClick={(a) => setOpenApp(a.id)}
            pageSize={15}
            resetPageKey={`${filter}:${query}`}
          />
        )}
      </Panel>

      {matching && <CandidateMatchDrawer key={order.id} onClose={() => setMatching(false)} orderId={order.id} />}

      {unmapping && (
        <UnmapCandidateDialog
          name={store.candidate(unmapApp?.candidateId ?? '')?.name ?? 'This candidate'}
          saving={unmapSaving}
          error={unmapError}
          onClose={() => setUnmapping(null)}
          onConfirm={async () => {
            if (!unmapping || unmapSaving) return
            setUnmapSaving(true); setUnmapError('')
            try { await store.unmap(unmapping); setUnmapping(null) }
            catch (error) { setUnmapError(error instanceof Error ? error.message : 'We could not remove the candidate. Please try again.') }
            finally { setUnmapSaving(false) }
          }}
        />
      )}

      <StageModal applicationId={openApp} open={!!openApp} onClose={() => setOpenApp(null)} />
      {editing && <OrderDialog order={order} onClose={() => setEditing(false)} />}
      {history && <RecordAudit kind="order" id={order.id} onClose={() => setHistory(false)} />}
      <DeleteRecordDialog
        kind="order"
        id={order.id}
        open={deleting}
        onClose={() => setDeleting(false)}
        onDeleted={() => navigate('/jobs')}
        onEdit={() => setEditing(true)}
      />
    </div>
  )
}
