import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { CircleCheck, Download, Eye, FileSearch, RefreshCw, UserCog, UserPlus, UserRound, Users, UserSearch } from 'lucide-react'
import { dateLabel } from '@/shared/lib/format'
import { useStore } from '@/shared/state/crm-store'
import { api, fileHref } from '@/shared/api/client'
import { stageByNo } from '@/shared/workflow/stages'
import type { Candidate } from '@/shared/model/types'
import { DataTable, type Column } from '@/shared/ui/table'
import { EmptyState, PageHeader, Panel, PanelHeader, StatusPill } from '@/shared/ui/display'
import { RecordIdentity, SummaryCards } from '@/shared/ui/workspace'
import { Button, IconButton, SearchInput, Select } from '@/shared/ui/primitives'
import { Tabs } from '@/shared/ui/overlay'
import { normalizedSkills } from '@/features/candidates/candidate-skills'
import { useSkillFacets } from '@/features/candidates/use-skill-facets'
import { ReassignCandidatesDialog } from '@/features/staff/reassign-candidates-dialog'
import {
  NEEDS_ASSIGNMENT_LABEL, assignWaitingLabel, assignWaitingMessage, canAssignWaiting, emptyPoolCopy, ownerLabel, poolCountsPath, togglePool,
  type AssignWaitingResult, type DataBankPool, type PoolCounts,
} from '@/features/candidates/data-bank-pools'

const STATUS_LABEL: Record<string, string> = {
  new: 'New',
  in_process: 'In process',
  not_interested: 'Not interested',
  deployed: 'Deployed',
  needs_review: 'Needs review',
}

const SOURCE_LABEL: Record<string, string> = {
  email: 'Email',
  whatsapp: 'WhatsApp',
  walk_in: 'Walk-in',
  referral: 'Referral',
  other: 'Other',
}

export default function DataBankPage() {
  const store = useStore()
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const staffFilter = store.can('all_reports') ? searchParams.get('staff') || '' : ''
  const changeStaff = (staff: string) => setSearchParams(previous => {
    const next = new URLSearchParams(previous)
    if (staff) next.set('staff', staff)
    else next.delete('staff')
    return next
  })
  const [query, setQuery] = useState('')
  const [pool, setPool] = useState<DataBankPool>('all')
  const [selected, setSelected] = useState<string[]>([])
  const [reassigning, setReassigning] = useState<string[] | null>(null)
  const [skills, setSkills] = useState<string[]>([])
  const [skillMode, setSkillMode] = useState<'all' | 'any'>('all')
  const [skillStatus, setSkillStatus] = useState('all')
  const [page, setPage] = useState(0)
  const [sort, setSort] = useState<{ key: string; dir: 'asc' | 'desc' }>({ key: 'added', dir: 'desc' })
  const [columnFilters, setColumnFilters] = useState<Record<string, string>>({})
  const [snapshot, setSnapshot] = useState<{ items: Candidate[]; total: number; page: number; scope: string } | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState('')
  const [exporting, setExporting] = useState(false)
  const [poolCounts, setPoolCounts] = useState<{ scope: string; counts: PoolCounts } | null>(null)
  const [assigningWaiting, setAssigningWaiting] = useState(false)
  const [mutationError, setMutationError] = useState('')
  const [refreshToken, setRefreshToken] = useState(0)
  const refreshRef = useRef({ ensureFresh: store.ensureFresh, refresh: store.refresh })
  refreshRef.current = { ensureFresh: store.ensureFresh, refresh: store.refresh }
  const staffScopeKey = `${store.me.id}:${staffFilter}:${refreshToken}`
  const [directoryRead, setDirectoryRead] = useState<{ key: string; error: string } | null>(null)
  useEffect(() => {
    let active = true
    const request = refreshToken ? refreshRef.current.refresh({ keepCurrent: true }) : refreshRef.current.ensureFresh()
    void request
      .then(() => { if (active) setDirectoryRead({ key: staffScopeKey, error: '' }) })
      .catch(error => { if (active) setDirectoryRead({ key: staffScopeKey, error: error instanceof Error ? error.message : 'Current candidate ownership could not be loaded.' }) })
    return () => { active = false }
  }, [staffScopeKey])
  const directoryReady = directoryRead?.key === staffScopeKey && !directoryRead.error
  const directoryError = directoryRead?.key === staffScopeKey ? directoryRead.error : ''
  const ownedCandidates = useMemo(() => store.candidates.filter(candidate => !staffFilter || candidate.ownerId === staffFilter), [store.candidates, staffFilter])
  const facetParams = new URLSearchParams({ pool, ...(staffFilter ? { staff: staffFilter } : {}) }).toString()
  const coverage = useSkillFacets(facetParams, store.candidates, !!directoryReady)
  const facets = coverage.data?.skills ?? []
  const hasFilters = Boolean(staffFilter || query.trim() || skills.length || skillStatus !== 'all' || Object.values(columnFilters).some(Boolean))
  const resetFilters = () => { setQuery(''); setSkills([]); setSkillStatus('all'); setSkillMode('all'); setColumnFilters({}); changeStaff('') }

  const params = new URLSearchParams({ q: query, pool, skill_mode: skillMode, missing_skills: String(skillStatus === 'missing'), column_filters: JSON.stringify(columnFilters), sort: sort.key, direction: sort.dir })
  if (staffFilter) params.set('staff', staffFilter)
  skills.forEach(skill => params.append('skills', skill))
  const filterQuery = params.toString()
  const result = snapshot?.scope === filterQuery ? snapshot : null
  useEffect(() => { setPage(0); setSelected([]); setReassigning(null) }, [filterQuery])
  useEffect(() => {
    if (!directoryReady) return
    const controller = new AbortController()
    setLoading(true); setLoadError('')
    const timer = window.setTimeout(() => {
      api.get<{ items: Candidate[]; total: number; page: number }>(`/candidates?${filterQuery}&page=${page}&page_size=20`, { signal: controller.signal })
        .then(data => { if (!controller.signal.aborted) setSnapshot({ ...data, scope: filterQuery }) })
        .catch(error => { if (!controller.signal.aborted) setLoadError(error instanceof Error ? error.message : 'Search failed.') })
        .finally(() => { if (!controller.signal.aborted) setLoading(false) })
    }, 250)
    return () => { window.clearTimeout(timer); controller.abort() }
  }, [directoryReady, filterQuery, page, store.candidates, store.me.id])
  const rows = result?.items ?? []
  const countsScope = `${staffFilter}:${refreshToken}`
  useEffect(() => {
    if (!directoryReady) return
    const controller = new AbortController()
    // Best effort: tabs and cards fall back to the loaded directory when the counts are unavailable.
    api.get<PoolCounts>(poolCountsPath(staffFilter || undefined), { signal: controller.signal })
      .then(counts => { if (!controller.signal.aborted) setPoolCounts({ scope: countsScope, counts }) })
      .catch(() => { if (!controller.signal.aborted) setPoolCounts(null) })
    return () => controller.abort()
  }, [directoryReady, countsScope, store.candidates])
  const counts = poolCounts?.scope === countsScope ? poolCounts.counts : null
  const assignWaiting = async () => {
    if (assigningWaiting) return
    setAssigningWaiting(true); setMutationError('')
    try {
      const outcome = await api.post<AssignWaitingResult>('/candidates/assign-waiting')
      const message = assignWaitingMessage(outcome)
      store.notify(message.text, message.tone)
      setSelected([])
      try { await store.refresh({ keepCurrent: true }) } catch { store.notify('Candidates were assigned. Refresh the directory to see current ownership.', 'warn') }
      setRefreshToken(value => value + 1)
    } catch (error) { setMutationError(error instanceof Error ? error.message : 'Waiting candidates could not be assigned.') } finally { setAssigningWaiting(false) }
  }
  const exportExcel = async () => {
    setExporting(true); setMutationError('')
    try {
      const response = await fetch(fileHref(`/api/candidates/export?${filterQuery}`), { credentials: 'include' })
      if (!response.ok) throw new Error('Unable to export this filtered Data Bank. Please retry.')
      const url = URL.createObjectURL(await response.blob())
      const link = document.createElement('a'); link.href = url; link.download = 'candidate-data-bank.xlsx'
      document.body.appendChild(link); link.click(); link.remove(); window.setTimeout(() => URL.revokeObjectURL(url), 1000)
    } catch (error) { setMutationError(error instanceof Error ? error.message : 'Export failed.') } finally { setExporting(false) }
  }

  // A hidden selection must not be reassigned after changing the talent pool.
  useEffect(() => { setSelected([]) }, [query, pool, skills, skillMode, skillStatus, page])

  if (!directoryReady) return <>
    <PageHeader title="Data Bank" eyebrow="Talent directory" description="Current candidates and recruiter ownership." />
    <Panel><div className="space-y-3 p-5">
      {directoryError ? <><p role="alert" className="text-sm text-stamp-700">{directoryError}</p><p className="text-sm text-ink-500">Refresh the directory to load current ownership. Previous summaries and assignment actions stay hidden until this check succeeds.</p><Button onClick={() => setRefreshToken(value => value + 1)}>Retry directory refresh</Button></>
        : <p role="status" className="text-sm text-ink-500">Loading current candidates and ownership…</p>}
    </div></Panel>
  </>

  const columns: Column<Candidate>[] = [
    {
      key: 'name',
      header: 'Candidate',
      sortable: true,
      value: (c) => c.name,
      width: '27%',
      render: (c) => (
        <RecordIdentity
          name={c.name}
          detail={<>
            <span className="ident block max-w-[16rem] truncate" title={c.ref}>ID · {c.ref}</span>
            <span className="block whitespace-nowrap text-[12.5px] text-ink-400">Added {dateLabel(c.addedAt)}{c.passportNo ? ' · Passport' : ''}</span>
          </>}
        />
      ),
    },
    {
      key: 'designation',
      header: 'Role',
      sortable: true,
      value: (c) => c.designations[0] ?? '',
      width: '21%',
      render: (c) => {
        const skills = normalizedSkills(c.skills)
        return (
          <div className="min-w-[11rem] max-w-[17rem]">
            <p className="font-medium text-ink">{c.designations[0] || <span className="font-normal text-ink-400">Not recorded</span>}</p>
            {skills.length > 0 && <p className="mt-0.5 truncate text-[13px] text-ink-500" title={skills.join(', ')}>{skills.slice(0, 2).join(' · ')}{skills.length > 2 ? ` +${skills.length - 2}` : ''}</p>}
          </div>
        )
      },
    },
    {
      key: 'experience',
      header: 'Experience',
      sortable: true,
      align: 'right',
      width: '96px',
      className: 'whitespace-nowrap',
      value: (c) => c.totalExperience,
      render: (c) => <span className="tnum font-medium text-ink">{c.totalExperience} {c.totalExperience === 1 ? 'yr' : 'yrs'}</span>,
    },
    {
      key: 'phone',
      header: 'Contact',
      value: (c) => c.phone,
      width: '22%',
      render: (c) => (
        <div className="min-w-[12rem]">
          <p className="ident whitespace-nowrap font-medium text-ink">{c.phone}</p>
          {c.email && <p className="max-w-[15rem] truncate text-[13px] text-ink-500" title={c.email}>{c.email}</p>}
          <p className="text-[12.5px] text-ink-400">Source · {SOURCE_LABEL[c.source] ?? c.source}</p>
        </div>
      ),
    },
    {
      key: 'owner',
      header: 'Assignment & status',
      sortable: true,
      value: (c) => ownerLabel(store.user(c.ownerId)?.name),
      width: '20%',
      render: (c) => {
        const app = store.activeApplicationFor(c.id)
        const owner = c.ownerId ? store.user(c.ownerId) : undefined
        return (
          <div className="min-w-[10rem] space-y-1">
            <p className="flex items-center gap-1.5 whitespace-nowrap">
              <UserRound size={14} className="shrink-0 text-ink-400" aria-hidden />
              {owner ? <span className="font-medium text-ink">{owner.name}</span> : <span className="font-medium text-amber-700">{ownerLabel(null)}</span>}
            </p>
            <StatusPill status={c.needsReview && c.status !== 'needs_review' ? 'needs_review' : c.status} dot />
            <p className="text-[12.5px] text-ink-400">
              {app ? `${store.order(app.orderId)?.ref ?? 'Order'} · Stage ${app.currentStage} ${stageByNo(app.currentStage).title}` : 'Not mapped to an order'}
            </p>
          </div>
        )
      },
    },
    {
      key: 'actions',
      header: '',
      width: '84px',
      render: (c) => (
        <div className="flex items-center justify-end gap-1" onClick={(event) => event.stopPropagation()}>
          <IconButton label={`Open ${c.name}`} onClick={() => navigate(`/candidates/${c.id}`)}><Eye className="h-4 w-4" /></IconButton>
          {store.can('reassign') && <IconButton label={`Reassign ${c.name}`} onClick={() => { setMutationError(''); setReassigning([c.id]) }}><UserCog className="h-4 w-4" /></IconButton>}
        </div>
      ),
    },
  ]

  return (
    <>
      <PageHeader
        title="Data Bank"
        eyebrow="Talent directory"
        description="Your next great placement starts here. Find talent across profiles, skills, and resumes."
        actions={
          <>
            <Button variant="secondary" icon={<RefreshCw className="h-4 w-4" />} onClick={() => { setSelected([]); setReassigning(null); setRefreshToken(value => value + 1) }}>Refresh directory</Button>
            {canAssignWaiting(counts, store.can('reassign')) && (
              <Button variant="primary" disabled={loading || !!loadError || assigningWaiting} icon={<UserCog className="h-4 w-4" />} onClick={() => void assignWaiting()}>
                {assignWaitingLabel(counts?.unassigned ?? 0, assigningWaiting)}
              </Button>
            )}
            {store.can('reassign') && selected.length > 0 && (
              <Button variant="secondary" disabled={loading || !!loadError} icon={<UserCog className="h-4 w-4" />} onClick={() => { setMutationError(''); setReassigning(selected.filter(id => rows.some(row => row.id === id))) }}>
                Reassign {selected.length}
              </Button>
            )}
            {store.can('export') && (
              <Button variant="secondary" disabled={loading || !!loadError || exporting} icon={<Download className="h-4 w-4" />} onClick={() => void exportExcel()}>
                {exporting ? 'Preparing Excel…' : 'Export to Excel'}
              </Button>
            )}
            <Button variant="primary" icon={<UserPlus className="h-4 w-4" />} onClick={() => navigate('/add-candidate')}>
              Add candidate
            </Button>
          </>
        }
      />
      {(loadError || mutationError) && <p role="alert" className="mb-4 rounded-panel bg-stamp-50 p-4 text-sm text-stamp-600">{loadError || mutationError}{loadError && result && ' Showing the last loaded page.'}</p>}

      <SummaryCards
        items={[
          {
            label: 'Total candidates',
            value: ownedCandidates.length,
            detail: staffFilter ? 'Assigned to this staff member' : 'Talent in your database',
            icon: Users,
          },
          {
            label: 'Not mapped',
            value: ownedCandidates.filter((candidate) => !store.activeApplicationFor(candidate.id))
              .length,
            detail: 'Available to match with an order',
            icon: UserSearch,
            tone: 'violet',
            active: pool === 'unmapped',
            onSelect: () => setPool(current => togglePool(current, 'unmapped')),
          },
          {
            label: 'Needs review',
            value: counts?.needs_review ?? ownedCandidates.filter(
              (candidate) => candidate.needsReview || candidate.status === 'needs_review',
            ).length,
            detail: pool === 'needs_review' ? 'Showing these profiles. Select again to show everyone' : 'Select to show profiles that need attention',
            icon: FileSearch,
            tone: 'amber',
            active: pool === 'needs_review',
            onSelect: () => setPool(current => togglePool(current, 'needs_review')),
          },
          {
            label: 'Deployed',
            value: ownedCandidates.filter((candidate) => candidate.status === 'deployed').length,
            detail: 'Successful candidate placements',
            icon: CircleCheck,
            tone: 'green',
          },
        ]}
      />

      <Panel flush>
        <PanelHeader title="All candidates" meta={loading ? 'Searching…' : `${result?.total ?? 0} matching${counts ? ` of ${counts.all} on file` : ''}`} />
        <Tabs tabs={[
          { key: 'all', label: 'Everyone', count: counts?.all ?? ownedCandidates.length },
          { key: 'unmapped', label: 'Not mapped', count: counts?.unmapped },
          { key: 'unassigned', label: NEEDS_ASSIGNMENT_LABEL, count: counts?.unassigned, tone: counts?.unassigned ? 'amber' : undefined },
          { key: 'needs_review', label: 'Needs review', count: counts?.needs_review, tone: counts?.needs_review ? 'amber' : undefined },
          { key: 'not_interested', label: 'Not interested', count: counts?.not_interested },
        ]} active={pool} onChange={(key) => setPool(key as DataBankPool)} />
        <div className="workspace-list-toolbar">
          <SearchInput
            value={query}
            onChange={setQuery}
            placeholder="Search name, role, skills, passport or email"
            className="min-w-0 max-w-xl flex-1"
          />
          {store.can('all_reports') && <Select className="w-full sm:w-52" aria-label="Filter candidates by staff" value={staffFilter} onChange={event => changeStaff(event.target.value)}>
            <option value="">All staff</option>
            {store.users.filter(user => user.role !== 'admin' || user.id === staffFilter).map(user => <option key={user.id} value={user.id}>{user.name}{!user.active ? ' (inactive)' : ''}</option>)}
          </Select>}
          <Select className="w-full sm:w-44" aria-label="Sort candidates" value={`${sort.key}:${sort.dir}`} onChange={event => { const [key, dir] = event.target.value.split(':'); setSort({ key, dir: dir as 'asc' | 'desc' }) }}>
            <option value="added:desc">Newest first</option>
            <option value="added:asc">Oldest first</option>
            <option value="name:asc">Name A–Z</option>
            <option value="experience:desc">Most experience</option>
            {!['added:desc', 'added:asc', 'name:asc', 'experience:desc'].includes(`${sort.key}:${sort.dir}`) && <option value={`${sort.key}:${sort.dir}`}>Sorted by column</option>}
          </Select>
        </div>
        {staffFilter && <p className="border-b border-rule px-4 pb-3 text-sm text-ink-500">Candidates currently owned by <strong className="font-medium text-ink">{store.user(staffFilter)?.name || 'the selected staff member'}</strong>. <button className="font-medium text-navy-700" onClick={() => changeStaff('')}>Show all staff</button></p>}

        <div className="flex flex-wrap items-center gap-2 border-y border-rule-soft bg-field/50 px-4 py-3" aria-label="Candidate filters">
          <span className="mr-1 text-[11px] font-semibold uppercase tracking-[0.14em] text-ink-500">Filters</span>
          <Select className="w-44" aria-label="Filter by status" value={columnFilters.status ?? ''} onChange={event => setColumnFilters(previous => ({ ...previous, status: event.target.value }))}>
            <option value="">Any status</option>
            {Object.entries(STATUS_LABEL).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </Select>
          <Select className="w-40" aria-label="Filter by source" value={columnFilters.source ?? ''} onChange={event => setColumnFilters(previous => ({ ...previous, source: event.target.value }))}>
            <option value="">Any source</option>
            {Object.values(SOURCE_LABEL).map(label => <option key={label} value={label}>{label}</option>)}
          </Select>
          <Select className="w-40" aria-label="Filter by passport" value={columnFilters.passport ?? ''} onChange={event => setColumnFilters(previous => ({ ...previous, passport: event.target.value }))}>
            <option value="">Any passport</option>
            <option value="Yes">Passport on file</option>
            <option value="No">No passport</option>
          </Select>
          <Select className="w-44" aria-label="Filter by recruitment stage" value={columnFilters.stage ?? ''} onChange={event => setColumnFilters(previous => ({ ...previous, stage: event.target.value }))}>
            <option value="">Any stage</option>
            <option value="0">Not mapped</option>
            {Array.from({ length: 11 }, (_, index) => index + 1).map(no => <option key={no} value={String(no)}>Stage {no} · {stageByNo(no).title}</option>)}
          </Select>
          <SearchInput className="w-40" value={columnFilters.order ?? ''} onChange={value => setColumnFilters(previous => ({ ...previous, order: value }))} placeholder="Order ref" />
          <SearchInput className="w-44" value={columnFilters.designation ?? ''} onChange={value => setColumnFilters(previous => ({ ...previous, designation: value }))} placeholder="Role contains" />
          <Select className="w-52" aria-label="Filter by recorded skill" value="" disabled={coverage.loading || !!coverage.error} onChange={(event) => {
            if (event.target.value) { setSkills((current) => [...current, event.target.value]); setSkillStatus('all') }
          }}>
            <option value="">{coverage.loading ? 'Loading skills…' : 'Any skill'}</option>
            {facets.filter((facet) => !skills.includes(facet.key)).slice().sort((a, b) => b.count - a.count || a.label.localeCompare(b.label)).map((facet) => (
              <option key={facet.key} value={facet.key}>{facet.label} ({facet.count})</option>
            ))}
          </Select>
          {skills.length > 1 && <Select className="w-44" aria-label="Skill matching" value={skillMode} onChange={(event) => setSkillMode(event.target.value as 'all' | 'any')}>
            <option value="all">Match all skills</option>
            <option value="any">Match any skill</option>
          </Select>}
          <Select className="w-48" aria-label="Skill coverage" value={skillStatus} onChange={(event) => {
            setSkillStatus(event.target.value)
            if (event.target.value === 'missing') setSkills([])
          }}>
            <option value="all">All candidates</option>
            <option value="missing">No skills recorded{coverage.data ? ` (${coverage.data.missing})` : ''}</option>
          </Select>
          {skills.map((key) => <Button key={key} size="sm" onClick={() => setSkills((current) => current.filter((skill) => skill !== key))}
            aria-label={`Remove skill ${facets.find((facet) => facet.key === key)?.label ?? key}`}>
            {facets.find((facet) => facet.key === key)?.label ?? key} ×
          </Button>)}
          {hasFilters && <Button size="sm" variant="ghost" onClick={resetFilters}>Clear filters</Button>}
        </div>

        <DataTable
          resetPageKey={`${staffFilter}:${pool}:${query}:${skills.join('|')}:${skillMode}:${skillStatus}`}
          rows={rows}
          columns={columns}
          rowKey={(c) => c.id}
          selectable={store.can('reassign')}
          selected={selected}
          onSelectedChange={setSelected}
          onRowClick={(c) => navigate(`/candidates/${c.id}`)}
          pageSize={20}
          server={{ page: result?.page ?? page, total: result?.total ?? 0, onPageChange: setPage, sort,
            onSortChange: next => setSort(next), filters: columnFilters,
            onFilterChange: (key, value) => setColumnFilters(previous => ({ ...previous, [key]: value })) }}
          empty={
            <EmptyState
              title={loading ? 'Searching candidates…' : hasFilters ? 'No matching candidates' : emptyPoolCopy(pool)?.title ?? 'No candidates yet'}
              description={
                hasFilters ? 'Try another search or filter, or clear filters to show the whole directory.'
                  : emptyPoolCopy(pool)?.description ?? 'Add a candidate, or connect an email account so resumes arrive on their own.'
              }
              action={hasFilters ? <Button onClick={resetFilters}>Clear filters</Button> : <Button variant="primary" onClick={() => navigate('/add-candidate')}>Add candidate</Button>}
            />
          }
        />
      </Panel>

      <p className="workspace-footnote">
        Select a candidate to view their profile. Manage recruitment stages from Mapped Candidates.
      </p>

      {reassigning && <ReassignCandidatesDialog candidateIds={reassigning} onClose={() => setReassigning(null)} onSaved={() => setSelected([])} />}

    </>
  )
}
