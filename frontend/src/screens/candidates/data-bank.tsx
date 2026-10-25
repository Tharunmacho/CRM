import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { cn } from '@/shared/lib/cn'
import { CircleCheck, Download, FileSearch, RefreshCw, Search, UserCheck, UserCog, UserPlus, UserRoundX, Users, UserSearch } from 'lucide-react'
import { useStore } from '@/shared/state/crm-store'
import { api, fileHref } from '@/shared/api/client'
import type { Candidate } from '@/shared/model/types'
import { EmptyState, PageHeader, Panel } from '@/shared/ui/display'
import { Button, Select } from '@/shared/ui/primitives'
import { useSkillFacets } from '@/features/candidates/use-skill-facets'
import { ReassignCandidatesDialog } from '@/features/staff/reassign-candidates-dialog'
import { StageModal } from '@/features/workflow/components/stage-modal'
import { dataBankColumns } from '@/features/candidates/components/data-bank-columns'
import { DataBankFilters } from '@/features/candidates/components/data-bank-filters'
import { DataBankCards } from '@/features/candidates/components/data-bank-cards'
import { CandidateEditDialog } from '@/features/candidates/components/candidate-edit-dialog'
import { DbTable, TABLE_MIN_WIDTH, useElementWidth } from '@/features/candidates/components/db-table'
import { KpiCards, Pager, ViewToggle, readView, saveView, type DbView } from '@/features/candidates/components/db-parts'
import { arrivalTrend, weeklyArrivals } from '@/features/candidates/data-bank-trends'
import {
  NEEDS_ASSIGNMENT_LABEL, activeFilterCount, assignWaitingLabel, assignWaitingMessage, canAssignWaiting, emptyPoolCopy, poolCountsPath, togglePool,
  type AssignWaitingResult, type DataBankPool, type PoolCounts,
} from '@/features/candidates/data-bank-pools'
import '@/styles/data-bank-v2.css'

const SORT_OPTIONS: [string, string][] = [
  ['added:desc', 'Newest first'], ['added:asc', 'Oldest first'], ['name:asc', 'Name A–Z'], ['name:desc', 'Name Z–A'],
  ['phone:asc', 'Phone number'], ['designation:asc', 'Job title A–Z'], ['experience:desc', 'Most experience'], ['experience:asc', 'Least experience'],
  ['passport:desc', 'Passport saved first'], ['source:asc', 'Source'], ['owner:asc', 'Recruiter'], ['order:asc', 'Order'],
  ['stage:desc', 'Latest stage first'], ['stage:asc', 'Earliest stage first'], ['status:asc', 'Status'],
]

export default function DataBankPage() {
  const store = useStore()
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  // Every staff user sees every candidate (PRD), so the staff filter is open to all roles.
  const staffFilter = searchParams.get('staff') || ''
  const changeStaff = (staff: string) => setSearchParams(previous => {
    const next = new URLSearchParams(previous)
    if (staff) next.set('staff', staff)
    else next.delete('staff')
    return next
  })
  const [query, setQuery] = useState('')
  const [pool, setPool] = useState<DataBankPool>(() => { const wanted = searchParams.get('pool'); return wanted === 'needs_review' || wanted === 'unassigned' ? wanted : 'all' })
  const [selected, setSelected] = useState<string[]>([])
  const [reassigning, setReassigning] = useState<string[] | null>(null)
  const [openStage, setOpenStage] = useState<string | null>(null)
  const [skills, setSkills] = useState<string[]>([])
  const [skillMode, setSkillMode] = useState<'all' | 'any'>('all')
  const [skillStatus, setSkillStatus] = useState('all')
  const [page, setPage] = useState(0)
  const [sort, setSort] = useState<{ key: string; dir: 'asc' | 'desc' }>({ key: 'added', dir: 'desc' })
  const [columnFilters, setColumnFilters] = useState<Record<string, string>>({})
  const [moreOpen, setMoreOpen] = useState(false)
  const [view, setView] = useState<DbView>(() => readView('data-bank'))
  const [pageSize, setPageSize] = useState(20)
  const [boxRef, boxWidth] = useElementWidth<HTMLDivElement>()
  const [editing, setEditing] = useState<Candidate | null>(null)
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
      .catch(error => { if (active) setDirectoryRead({ key: staffScopeKey, error: error instanceof Error ? error.message : 'We could not load who looks after each candidate.' }) })
    return () => { active = false }
  }, [staffScopeKey])
  const directoryReady = directoryRead?.key === staffScopeKey && !directoryRead.error
  const directoryError = directoryRead?.key === staffScopeKey ? directoryRead.error : ''
  const ownedCandidates = useMemo(() => store.candidates.filter(candidate => !staffFilter || candidate.ownerId === staffFilter), [store.candidates, staffFilter])
  const facetParams = new URLSearchParams({ pool, ...(staffFilter ? { staff: staffFilter } : {}) }).toString()
  const coverage = useSkillFacets(facetParams, store.candidates, !!directoryReady)
  const filterCount = activeFilterCount(columnFilters, staffFilter, skills, skillStatus)
  const hasFilters = filterCount > 0 || Boolean(query.trim()) || pool !== 'all'
  const setColumnFilter = (key: string, value: string) => setColumnFilters(previous => ({ ...previous, [key]: value }))
  const resetFilters = () => { setQuery(''); setPool('all'); setSkills([]); setSkillStatus('all'); setSkillMode('all'); setColumnFilters({}); changeStaff('') }

  const params = new URLSearchParams({ q: query, pool, skill_mode: skillMode, missing_skills: String(skillStatus === 'missing'), column_filters: JSON.stringify(columnFilters), sort: sort.key, direction: sort.dir })
  if (staffFilter) params.set('staff', staffFilter)
  skills.forEach(skill => params.append('skills', skill))
  const filterQuery = params.toString()
  // Keep showing the previous page while a new search loads, so the list never blinks empty and the filters keep their place.
  const result = snapshot
  const stale = !!snapshot && snapshot.scope !== filterQuery
  useEffect(() => { setPage(0); setSelected([]); setReassigning(null) }, [filterQuery, pageSize])
  useEffect(() => {
    if (!directoryReady) return
    const controller = new AbortController()
    setLoading(true); setLoadError('')
    const timer = window.setTimeout(() => {
      api.get<{ items: Candidate[]; total: number; page: number }>(`/candidates?${filterQuery}&page=${page}&page_size=${pageSize}`, { signal: controller.signal })
        .then(data => { if (!controller.signal.aborted) setSnapshot({ ...data, scope: filterQuery }) })
        .catch(error => { if (!controller.signal.aborted) setLoadError(error instanceof Error ? error.message : 'We could not search. Please try again.') })
        .finally(() => { if (!controller.signal.aborted) setLoading(false) })
    }, 250)
    return () => { window.clearTimeout(timer); controller.abort() }
  }, [directoryReady, filterQuery, page, pageSize, store.candidates, store.me.id])
  const rows = result?.items ?? []
  const countsScope = `${staffFilter}:${refreshToken}`
  useEffect(() => {
    if (!directoryReady) return
    const controller = new AbortController()
    // Best effort: the pools fall back to the loaded directory when the counts are unavailable.
    api.get<PoolCounts>(poolCountsPath(staffFilter || undefined), { signal: controller.signal })
      .then(counts => { if (!controller.signal.aborted) setPoolCounts({ scope: countsScope, counts }) })
      .catch(() => { if (!controller.signal.aborted) setPoolCounts(null) })
    return () => controller.abort()
  }, [directoryReady, countsScope, store.candidates])
  const counts = poolCounts?.scope === countsScope ? poolCounts.counts : null
  const changeView = (next: DbView) => { setView(next); saveView('data-bank', next) }
  // Arrival history per KPI slice, from the real added dates of the loaded directory.
  const slices = useMemo(() => {
    const unmapped = (candidate: Candidate) => !store.activeApplicationFor(candidate.id)
    const make = (keep: (candidate: Candidate) => boolean) => {
      const dates = ownedCandidates.filter(keep).map(candidate => candidate.addedAt)
      return { trend: arrivalTrend(dates), spark: weeklyArrivals(dates) }
    }
    return {
      all: make(() => true),
      unmapped: make(unmapped),
      unassigned: make(candidate => !candidate.ownerId),
      needs_review: make(candidate => !!candidate.needsReview || candidate.status === 'needs_review'),
      not_interested: make(candidate => candidate.status === 'not_interested'),
      deployed: make(candidate => candidate.status === 'deployed'),
    }
  }, [ownedCandidates, store.applications])
  const assignWaiting = async () => {
    if (assigningWaiting) return
    setAssigningWaiting(true); setMutationError('')
    try {
      const outcome = await api.post<AssignWaitingResult>('/candidates/assign-waiting')
      const message = assignWaitingMessage(outcome)
      store.notify(message.text, message.tone)
      setSelected([])
      try { await store.refresh({ keepCurrent: true }) } catch { store.notify('Candidates were assigned. Refresh the list to see the changes.', 'warn') }
      setRefreshToken(value => value + 1)
    } catch (error) { setMutationError(error instanceof Error ? error.message : 'We could not assign the waiting candidates. Please try again.') } finally { setAssigningWaiting(false) }
  }
  const exportExcel = async () => {
    setExporting(true); setMutationError('')
    try {
      const response = await fetch(fileHref(`/api/candidates/export?${filterQuery}`), { credentials: 'include' })
      if (!response.ok) throw new Error('We could not make the Excel file. Please try again.')
      const url = URL.createObjectURL(await response.blob())
      const link = document.createElement('a'); link.href = url; link.download = 'candidate-data-bank.xlsx'
      document.body.appendChild(link); link.click(); link.remove(); window.setTimeout(() => URL.revokeObjectURL(url), 1000)
    } catch (error) { setMutationError(error instanceof Error ? error.message : 'We could not make the Excel file. Please try again.') } finally { setExporting(false) }
  }

  // A hidden selection must not be reassigned after changing the talent pool.
  useEffect(() => { setSelected([]) }, [query, pool, skills, skillMode, skillStatus, page, pageSize])

  if (!directoryReady) return <div className="db">
    <PageHeader title="Data Bank" description="Everyone we have on file, who looks after them, and where they are in the 11 stages" />
    <Panel><div className="space-y-3 p-5">
      {directoryError ? <><p role="alert" className="text-sm text-stamp-700">{directoryError}</p><p className="text-sm text-ink-500">Refresh to load the latest list. The totals and the assign buttons stay hidden until it loads.</p><Button onClick={() => setRefreshToken(value => value + 1)}>Try again</Button></>
        : <p role="status" className="text-sm text-ink-500">Loading candidates…</p>}
    </div></Panel>
  </div>

  // The eleven columns need about TABLE_MIN_WIDTH; narrower than that the same candidates read as stacked cards.
  const forceCards = boxWidth > 0 && boxWidth < TABLE_MIN_WIDTH
  const showCards = forceCards || view === 'cards'
  const openCandidate = (candidate: Candidate) => navigate(`/candidates/${candidate.id}`)
  const reassignOne = (candidate: Candidate) => { setMutationError(''); setReassigning([candidate.id]) }
  const columns = dataBankColumns({ store, onOpen: openCandidate, onReassign: reassignOne, onEdit: setEditing, onOpenStage: setOpenStage })
  const notMapped = counts?.unmapped ?? ownedCandidates.filter((candidate) => !store.activeApplicationFor(candidate.id)).length
  const notInterested = counts?.not_interested ?? ownedCandidates.filter((candidate) => candidate.status === 'not_interested').length
  const needsReview = counts?.needs_review ?? ownedCandidates.filter((candidate) => candidate.needsReview || candidate.status === 'needs_review').length
  const waiting = counts?.unassigned ?? ownedCandidates.filter((candidate) => !candidate.ownerId).length
  const onFile = counts?.all ?? ownedCandidates.length
  const total = result?.total ?? 0
  const POOL_TITLE: Record<DataBankPool, string> = { all: 'All candidates', unmapped: 'No order yet', unassigned: NEEDS_ASSIGNMENT_LABEL, needs_review: 'Needs review', not_interested: 'Not interested' }
  const narrowed = filterCount > 0 || Boolean(query.trim())
  const busy = loading || stale
  const emptyState = loadError && !result ? (
    <EmptyState title="We could not load the candidates" description={loadError} action={<Button variant="primary" onClick={() => setRefreshToken(value => value + 1)}>Try again</Button>} />
  ) : (
    <EmptyState
      title={loading ? 'Searching candidates…' : narrowed ? 'No matching candidates' : emptyPoolCopy(pool)?.title ?? 'No candidates yet'}
      description={
        narrowed ? 'Try another search or filter, or clear the filters to see everyone.'
          : emptyPoolCopy(pool)?.description ?? 'Add a candidate, or connect an email account so resumes come in on their own.'
      }
      action={narrowed ? <Button onClick={resetFilters}>Clear filters</Button> : <Button variant="primary" onClick={() => navigate('/add-candidate')}>Add candidate</Button>}
    />
  )

  return (
    <div className="db">
      <PageHeader
        title="Data Bank"
        description="Everyone we have on file, who looks after them, and where they are in the 11 stages"
        actions={
          <>
            {canAssignWaiting(counts, store.can('reassign')) && (
              <Button variant="secondary" disabled={loading || !!loadError || assigningWaiting} icon={<UserCog className="h-4 w-4" />} onClick={() => void assignWaiting()}>
                {assignWaitingLabel(counts?.unassigned ?? 0, assigningWaiting)}
              </Button>
            )}
            {store.can('reassign') && selected.length > 0 && (
              <Button variant="secondary" disabled={loading || !!loadError} icon={<UserCog className="h-4 w-4" />} onClick={() => { setMutationError(''); setReassigning(selected.filter(id => rows.some(row => row.id === id))) }}>
                Reassign {selected.length}
              </Button>
            )}
            <Button variant="secondary" icon={<UserPlus className="h-4 w-4" />} onClick={() => navigate('/add-candidate')}>
              Add candidate
            </Button>
            {store.can('export') && (
              <Button variant="primary" disabled={loading || !!loadError || exporting} icon={<Download className="h-4 w-4" />} onClick={() => void exportExcel()}>
                {exporting ? 'Preparing Excel…' : 'Export Excel'}
              </Button>
            )}
          </>
        }
      />
      {(loadError || mutationError) && !(loadError && !result) && <p role="alert" className="db-alert">{loadError || mutationError}{loadError && result && ' Showing the last loaded page.'}</p>}
      {mutationError && loadError && !result && <p role="alert" className="db-alert">{mutationError}</p>}

      <KpiCards
        items={[
          { label: 'All candidates', value: counts?.all ?? ownedCandidates.length, detail: staffFilter ? 'Assigned to this staff member' : 'Everyone in the Data Bank', icon: Users, ...slices.all, active: pool === 'all', onSelect: () => setPool('all') },
          { label: 'No order yet', value: notMapped, detail: 'Not matched to an order. Ready to match.', icon: UserSearch, ...slices.unmapped, active: pool === 'unmapped', onSelect: () => setPool(current => togglePool(current, 'unmapped')) },
          ...(waiting > 0 || pool === 'unassigned' ? [{ label: NEEDS_ASSIGNMENT_LABEL, value: waiting, detail: pool === 'unassigned' ? 'Select again to show everyone' : 'Waiting for a recruiter', icon: UserCheck, tone: 'warn' as const, ...slices.unassigned, active: pool === 'unassigned', onSelect: () => setPool(current => togglePool(current, 'unassigned')) }] : []),
          { label: 'Needs review', value: needsReview, detail: pool === 'needs_review' ? 'Select again to show everyone' : 'Profiles that need attention', icon: FileSearch, tone: 'warn', ...slices.needs_review, active: pool === 'needs_review', onSelect: () => setPool(current => togglePool(current, 'needs_review')) },
          { label: 'Not interested', value: notInterested, detail: pool === 'not_interested' ? 'Select again to show everyone' : 'Offer them another order later', icon: UserRoundX, tone: 'quiet', ...slices.not_interested, active: pool === 'not_interested', onSelect: () => setPool(current => togglePool(current, 'not_interested')) },
          { label: 'Deployed', value: ownedCandidates.filter((candidate) => candidate.status === 'deployed').length, detail: 'Placed in a job abroad', icon: CircleCheck, tone: 'ok', ...slices.deployed },
        ]}
      />

      <Panel flush className="db-panel">
        <div className="db-panel-head">
          <div className="min-w-0">
            <h2 className="db-panel-title">{POOL_TITLE[pool]}</h2>
            <p className="db-panel-sub tnum">{loading && !result ? 'Searching…' : `Showing ${total} of ${onFile} candidates${busy ? ' · updating…' : ''}`}</p>
          </div>
          <div className="db-panel-tools">
            <label className="db-search">
              <Search size={15} aria-hidden="true" />
              <input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search name, phone, skills or resume" aria-label="Search name, phone, passport, skills or resume content" />
            </label>
            <Select className="db-sort" aria-label="Sort candidates by" value={`${sort.key}:${sort.dir}`} onChange={event => { const [key, dir] = event.target.value.split(':'); setSort({ key, dir: dir as 'asc' | 'desc' }) }}>
              {SORT_OPTIONS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            </Select>
            <button type="button" className="db-refresh" title="Refresh the list" aria-label="Refresh the list" onClick={() => { setSelected([]); setReassigning(null); setRefreshToken(value => value + 1) }}><RefreshCw size={15} /></button>
            {!forceCards && <ViewToggle view={view} onChange={changeView} />}
          </div>
        </div>

        <DataBankFilters
          filters={columnFilters}
          onFilter={setColumnFilter}
          pool={pool}
          onPool={setPool}
          staff={staffFilter}
          onStaff={changeStaff}
          users={store.users}
          skills={skills}
          onSkills={setSkills}
          skillMode={skillMode}
          onSkillMode={setSkillMode}
          skillStatus={skillStatus}
          onSkillStatus={setSkillStatus}
          coverage={coverage}
          moreOpen={moreOpen}
          onMoreToggle={() => setMoreOpen(open => !open)}
          canClear={hasFilters}
          onClear={resetFilters}
        />

        <div ref={boxRef} className={cn('db-box', busy && result && 'is-busy')} aria-busy={busy || undefined}>
          {rows.length === 0 ? emptyState : showCards ? (
            <DataBankCards rows={rows} store={store} onOpen={openCandidate} onReassign={reassignOne} onEdit={setEditing} onOpenStage={setOpenStage} />
          ) : (
            <DbTable
              label="Candidates"
              rows={rows}
              columns={columns}
              rowKey={(c) => c.id}
              selectable={store.can('reassign')}
              selected={selected}
              onSelectedChange={setSelected}
              onRowClick={openCandidate}
              sort={sort}
              onSort={(key) => setSort(current => ({ key, dir: current.key === key && current.dir === 'asc' ? 'desc' : 'asc' }))}
            />
          )}
        </div>
        {rows.length > 0 && <Pager page={result?.page ?? page} pageSize={pageSize} total={total} noun="candidates" onPage={setPage} onPageSize={setPageSize} />}
        <p className="db-hint">Click a row to open the candidate. Click an order to see its 11 stages.</p>
      </Panel>

      {reassigning && <ReassignCandidatesDialog candidateIds={reassigning} onClose={() => setReassigning(null)} onSaved={() => setSelected([])} />}
      {editing && <CandidateEditDialog candidate={editing} onClose={() => setEditing(null)} />}
      <StageModal applicationId={openStage} open={!!openStage} onClose={() => setOpenStage(null)} />
    </div>
  )
}
