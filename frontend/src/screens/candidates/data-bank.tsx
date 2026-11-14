import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { cn } from '@/shared/lib/cn'
import { ChevronsDownUp, ChevronsUpDown, Download, RefreshCw, Search, UserCog, UserMinus, UserPlus } from 'lucide-react'
import { useStore } from '@/shared/state/crm-store'
import { liveInterval } from '@/shared/state/live'
import { withoutDeleted } from '@/shared/state/candidate-removal'
import { api, ApiError, fileHref } from '@/shared/api/client'
import type { Candidate } from '@/shared/model/types'
import { EmptyState, PageHeader, Panel } from '@/shared/ui/display'
import { LeadsSkeleton } from '@/features/leads/components/leads-skeleton'
import { Button, Select } from '@/shared/ui/primitives'
import { useSkillFacets } from '@/features/candidates/use-skill-facets'
import { ReassignCandidatesDialog } from '@/features/staff/reassign-candidates-dialog'
import { UnassignCandidatesDialog } from '@/features/staff/unassign-candidates-dialog'
import { StageModal } from '@/features/workflow/components/stage-modal'
import { bankContext, dataBankColumns, formOrderId } from '@/features/candidates/components/data-bank-columns'
import { applicationFormHref } from '@/features/application-form/form-model'
import { DataBankExpanded } from '@/features/candidates/components/data-bank-expanded'
import { BANK_GROUPS } from '@/features/candidates/data-bank-fields'
import { useFormOptions } from '@/features/form-options/use-form-options'
import { DataBankFilters } from '@/features/candidates/components/data-bank-filters'
import { DataBankCards } from '@/features/candidates/components/data-bank-cards'
import { DataBankStats } from '@/features/candidates/components/data-bank-stats'
import { poolAfterCardPress, statCards } from '@/features/candidates/data-bank-stats'
import { CandidateEditDialog } from '@/features/candidates/components/candidate-edit-dialog'
import { DbTable, useElementWidth } from '@/features/candidates/components/db-table'
import { fitColumns } from '@/features/candidates/components/column-fit'
import { ColumnMenu, useColumnChoices } from '@/features/candidates/components/column-menu'
import { Pager, ViewToggle, readView, saveView, type DbView } from '@/features/candidates/components/db-parts'
import { normalizeSearch } from '@/features/candidates/data-bank-search'
import { ALL_CANDIDATES_LABEL, MY_NEW_LABEL, SHOW_ALL_LABEL, countLine, myNewEmptyCopy, myNewNote, startsOnMyNew } from '@/features/candidates/data-bank-scope'
import {
  activeFilterCount, assignWaitingLabel, assignWaitingMessage, canAssignWaiting, canUnassignCandidate, emptyPoolCopy, parseTravel, poolCountsPath,
  type AssignWaitingResult, type DataBankPool, type PoolCounts, type TravelFilter,
} from '@/features/candidates/data-bank-pools'
import '@/styles/data-bank-v2.css'
import '@/styles/data-bank-top.css'

const SORT_OPTIONS: [string, string][] = [
  ['new_first:desc', 'New candidates first'],
  ['assigned:desc', 'Recently assigned first'],
  ['added:desc', 'Newest first'], ['added:asc', 'Oldest first'], ['name:asc', 'Name A–Z'], ['name:desc', 'Name Z–A'],
  ['phone:asc', 'Phone number'], ['designation:asc', 'Job title A–Z'], ['experience:desc', 'Most experience'], ['experience:asc', 'Least experience'],
  ['passport:desc', 'Passport saved first'], ['source:asc', 'Source'], ['owner:asc', 'Recruiter'], ['order:asc', 'Order'],
  ['country:asc', 'Preferred country'], ['service:asc', 'Service charges'], ['interest:asc', 'Interest level'],
  ['stage:desc', 'Latest stage first'], ['stage:asc', 'Earliest stage first'], ['status:asc', 'Status'],
]

/** Short, so results follow the typing. Only typing (search text, column filters) waits; page, sort, pool and the first load go at once. */
const SEARCH_DELAY_MS = 180
/** A candidate or staff change is fetched once, this long after the last event of a burst. */
const LIVE_RELOAD_DEBOUNCE_MS = 500
/** Poll while the live stream is down (while it is up the safety net is slower, see live.ts). */
const LIVE_RELOAD_POLL_MS = 60_000

/** Below this box width (a window of a window under 1100px wide) the candidates read as cards; above it the one table always fits. */
const DATA_BANK_TABLE_MIN_WIDTH = 900

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
  // Travel (any / travelled / not travelled) lives in the link like the staff filter, so a refresh or a shared link keeps it.
  const travelled = parseTravel(searchParams.get('travelled'))
  const changeTravel = (travel: TravelFilter) => setSearchParams(previous => {
    const next = new URLSearchParams(previous)
    if (travel) next.set('travelled', travel)
    else next.delete('travelled')
    return next
  })
  // Two link parameters cleared in ONE update (router updates in the same tick do not stack).
  const clearLinkFilters = (keys: string[]) => setSearchParams(previous => {
    const next = new URLSearchParams(previous)
    keys.forEach(key => next.delete(key))
    return next
  })
  const [query, setQuery] = useState('')
  const [pool, setPool] = useState<DataBankPool>(() => { const wanted = searchParams.get('pool'); return wanted === 'needs_review' || wanted === 'unassigned' ? wanted : 'all' })
  const [selected, setSelected] = useState<string[]>([])
  const [reassigning, setReassigning] = useState<string[] | null>(null)
  const [unassigning, setUnassigning] = useState<string[] | null>(null)
  const [openStage, setOpenStage] = useState<string | null>(null)
  const [skills, setSkills] = useState<string[]>([])
  const [skillMode, setSkillMode] = useState<'all' | 'any'>('all')
  const [skillStatus, setSkillStatus] = useState('all')
  const [page, setPage] = useState(0)
  // Staff open on their own new candidates (newest assignment first); one click shows the whole Data Bank. A link that names a
  // recruiter or a pool (the dashboard, the staff screen) is a deliberate request for that list, so it opens as before.
  const [mineNew, setMineNew] = useState(() => startsOnMyNew(store.me.role, searchParams.get('staff'), searchParams.get('pool')))
  const [sort, setSort] = useState<{ key: string; dir: 'asc' | 'desc' }>(() => (mineNew ? { key: 'assigned', dir: 'desc' } : { key: 'new_first', dir: 'desc' }))
  const [columnFilters, setColumnFilters] = useState<Record<string, string>>({})
  const [moreOpen, setMoreOpen] = useState(false)
  const [view, setView] = useState<DbView>(() => readView('data-bank'))
  const [pageSize, setPageSize] = useState(20)
  const [boxRef, boxWidth] = useElementWidth<HTMLDivElement>()
  const [columnChoices, setColumnChoices] = useColumnChoices('data-bank')
  const [expanded, setExpanded] = useState<string[]>([])
  const { options: formOptions } = useFormOptions()
  const [editing, setEditing] = useState<Candidate | null>(null)
  const [snapshot, setSnapshot] = useState<{ items: Candidate[]; total: number; page: number; scope: string } | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState('')
  const [exporting, setExporting] = useState(false)
  const [poolCounts, setPoolCounts] = useState<{ scope: string; counts: PoolCounts } | null>(null)
  const [assigningWaiting, setAssigningWaiting] = useState(false)
  const [mutationError, setMutationError] = useState('')
  const [refreshToken, setRefreshToken] = useState(0)
  // One counter every read of this page follows (list, pool counts, skill facets): bumping it refetches the three in parallel.
  // `silent` reloads (live events, the safety poll) keep the table as it is instead of dimming it and disabling the buttons.
  const [reload, setReload] = useState({ n: 0, silent: false })
  const reloadPage = useCallback((silent = false) => setReload(current => ({ n: current.n + 1, silent })), [])
  useEffect(() => liveInterval(() => reloadPage(true), LIVE_RELOAD_POLL_MS, ['candidates', 'users'], { debounceMs: LIVE_RELOAD_DEBOUNCE_MS }), [reloadPage])
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
  // The list, the pool counts and the skill facets are the server's answers and do not wait for this: it only fills the
  // recruiter names (and stage chips), which show a placeholder meanwhile.
  const directoryError = directoryRead?.key === staffScopeKey ? directoryRead.error : ''
  const ownedCandidates = useMemo(() => store.candidates.filter(candidate => !staffFilter || candidate.ownerId === staffFilter), [store.candidates, staffFilter])
  const showMyNew = () => { setMineNew(true); setPool('all'); clearLinkFilters(['staff', 'travelled']); setSort({ key: 'assigned', dir: 'desc' }) }
  const showAll = () => { setMineNew(false); setSort(current => (current.key === 'assigned' ? { key: 'new_first', dir: 'desc' } : current)) }
  // Any choice that names another list (a pool card, a recruiter) leaves "my new candidates" instead of silently emptying it.
  useEffect(() => { if (mineNew && (staffFilter || pool !== 'all' || travelled)) showAll() }, [staffFilter, pool, travelled])  // eslint-disable-line react-hooks/exhaustive-deps
  const facetParams = new URLSearchParams({ pool, ...(mineNew ? { staff: store.me.id } : staffFilter ? { staff: staffFilter } : {}) }).toString()
  const coverage = useSkillFacets(facetParams, `${refreshToken}:${reload.n}`)
  const filterCount = activeFilterCount(columnFilters, staffFilter, skills, skillStatus, travelled)
  const hasFilters = filterCount > 0 || Boolean(query.trim()) || pool !== 'all'
  const setColumnFilter = (key: string, value: string) => setColumnFilters(previous => ({ ...previous, [key]: value }))
  const resetFilters = () => { setQuery(''); setPool('all'); setSkills([]); setSkillStatus('all'); setSkillMode('all'); setColumnFilters({}); clearLinkFilters(['staff', 'travelled']) }

  const searched = normalizeSearch(query)
  const params = new URLSearchParams({ q: searched, pool, skill_mode: skillMode, missing_skills: String(skillStatus === 'missing'), column_filters: JSON.stringify(columnFilters), sort: sort.key, direction: sort.dir })
  if (mineNew) params.set('mine_new', 'true')
  else if (staffFilter) params.set('staff', staffFilter)
  if (travelled) params.set('travelled', travelled)
  skills.forEach(skill => params.append('skills', skill))
  const filterQuery = params.toString()
  // Keep showing the previous page while a new search loads, so the list never blinks empty and the filters keep their place.
  const result = snapshot
  const stale = !!snapshot && snapshot.scope !== filterQuery
  useEffect(() => { setPage(0); setSelected([]); setReassigning(null); setUnassigning(null) }, [filterQuery, pageSize])
  // Typing waits a moment so results follow it; everything else (first load, page, sort, pool, page size, live reloads) goes at once.
  const searchSignature = `${searched}\u0000${JSON.stringify(columnFilters)}`
  const loadMeta = useRef({ key: `${filterQuery}|${pageSize}`, search: searchSignature, typing: false, reloadN: 0, refreshToken })
  useEffect(() => {
    const meta = loadMeta.current
    const key = `${filterQuery}|${pageSize}`
    if (meta.search !== searchSignature) { meta.search = searchSignature; meta.typing = true }
    const filtersChanged = meta.key !== key
    meta.key = key
    // Only a bare live/poll reload is quiet; a search, a page, the Refresh button or a dialog save shows its progress.
    const background = reload.silent && meta.reloadN !== reload.n && meta.refreshToken === refreshToken && !filtersChanged && !meta.typing
    meta.reloadN = reload.n
    meta.refreshToken = refreshToken
    // A new search starts on page 0 (the effect above moves there, which loads once); do not fetch a page that is about to be left.
    if (filtersChanged && page !== 0) return
    const controller = new AbortController()
    const quiet = background
    if (!quiet) { setLoading(true); setLoadError('') }
    const load = () => {
      meta.typing = false
      api.get<{ items: Candidate[]; total: number; page: number }>(`/candidates?${filterQuery}&page=${page}&page_size=${pageSize}`, { signal: controller.signal })
        .then(data => { if (!controller.signal.aborted) setSnapshot({ ...data, scope: filterQuery }) })
        .catch(error => {
          if (controller.signal.aborted) return
          // An open browser can receive the new screen before its API is updated.
          // Use an available server sort and keep the displayed selection honest.
          if (sort.key === 'new_first' && error instanceof ApiError && error.status === 422 && error.message === 'We cannot sort by this column.') {
            setSort({ key: 'added', dir: 'desc' })
            return
          }
          // A background reload that fails keeps the page that is on screen; the next event or poll tries again.
          if (quiet) return
          setLoadError(error instanceof Error ? error.message : 'We could not search. Please try again.')
        })
        .finally(() => { if (!controller.signal.aborted) setLoading(false) })
    }
    const timer = meta.typing ? window.setTimeout(load, SEARCH_DELAY_MS) : undefined
    if (timer === undefined) load()
    return () => { if (timer !== undefined) window.clearTimeout(timer); controller.abort() }
  }, [filterQuery, searchSignature, page, pageSize, reload, refreshToken, store.me.id])
  const rows = withoutDeleted(result?.items ?? [])
  // Handlers and columns keep their identity between renders (a selection tick, a popover, a typed letter), so the memoised
  // table rows are not rebuilt; they change only with the workspace, the form options or the recruiter directory.
  const openCandidate = useCallback((candidate: Candidate) => navigate(applicationFormHref(candidate.id, formOrderId(store, candidate.id))), [navigate, store])
  const reassignOne = useCallback((candidate: Candidate) => { setMutationError(''); setReassigning([candidate.id]) }, [])
  const directoryPending = !directoryReady && !directoryError
  const allColumns = useMemo(
    () => dataBankColumns({ store, onOpen: openCandidate, onReassign: reassignOne, onEdit: setEditing, onOpenStage: setOpenStage, options: formOptions, directoryPending }),
    [store, openCandidate, reassignOne, formOptions, directoryPending],
  )
  const fieldContext = useMemo(() => bankContext(store, formOptions), [store, formOptions])
  const toggleExpand = useCallback((id: string) => setExpanded(current => (current.includes(id) ? current.filter(value => value !== id) : [...current, id])), [])
  const fit = useMemo(() => fitColumns(allColumns, boxWidth, { ...columnChoices, source: true }), [allColumns, boxWidth, columnChoices])
  const columns = useMemo(() => allColumns.filter((column) => fit.keys.includes(column.key)), [allColumns, fit])
  const countsScope = `${staffFilter}:${refreshToken}`
  useEffect(() => {
    const controller = new AbortController()
    // Best effort: the pools fall back to the loaded directory when the counts are unavailable.
    api.get<PoolCounts>(poolCountsPath(staffFilter || undefined), { signal: controller.signal })
      .then(counts => { if (!controller.signal.aborted) setPoolCounts({ scope: countsScope, counts }) })
      .catch(() => { if (!controller.signal.aborted) setPoolCounts(null) })
    return () => controller.abort()
  }, [countsScope, reload.n])
  const counts = poolCounts?.scope === countsScope ? poolCounts.counts : null
  const changeView = (next: DbView) => { setView(next); saveView('data-bank', next) }
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
  // The ticked candidates who have a recruiter now: the ones the admin can take back.
  const selectedOwned = rows.filter(row => selected.includes(row.id) && canUnassignCandidate(store.me.role, row.ownerId)).map(row => row.id)
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

  // The grid table scrolls sideways (actions and WhatsApp pinned); narrower than GRID_TABLE_MIN_WIDTH the same candidates read as stacked cards.
  const forceCards = boxWidth > 0 && boxWidth < DATA_BANK_TABLE_MIN_WIDTH
  const showCards = forceCards || view === 'cards'
  const allOpen = rows.length > 0 && rows.every(row => expanded.includes(row.id))
  const cards = statCards({ all: counts?.all ?? ownedCandidates.length, unassigned: counts?.unassigned ?? ownedCandidates.filter((candidate) => !candidate.ownerId).length, needs_review: counts?.needs_review ?? ownedCandidates.filter((candidate) => candidate.needsReview || candidate.status === 'needs_review').length }, pool, store.me.role)
  const onFile = counts?.all ?? ownedCandidates.length
  const total = result?.total ?? 0
  const loaded = result
  const failure = loadError
  const searching = loading
  const narrowed = filterCount > 0 || Boolean(query.trim())
  const busy = loading || stale
  const emptyState = searching && !loaded ? (
    <LeadsSkeleton rows={8} columns={7} label="Loading candidates…" />
  ) : failure && !loaded ? (
    <EmptyState title="We could not load the candidates" description={failure} action={<Button variant="primary" onClick={() => setRefreshToken(value => value + 1)}>Try again</Button>} />
  ) : mineNew && !narrowed ? (
    <EmptyState title={myNewEmptyCopy().title} description={myNewEmptyCopy().description} action={<Button variant="primary" onClick={showAll}>{SHOW_ALL_LABEL}</Button>} />
  ) : (
    <EmptyState
      title={searching ? 'Searching candidates…' : narrowed ? 'No matching candidates' : emptyPoolCopy(pool)?.title ?? 'No candidates yet'}
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
            {store.me.role === 'admin' && selectedOwned.length > 0 && (
              <Button variant="secondary" disabled={loading || !!loadError} icon={<UserMinus className="h-4 w-4" />} onClick={() => { setMutationError(''); setUnassigning(selectedOwned) }}>
                Make {selectedOwned.length} unassigned
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
      {(failure || mutationError) && !(failure && !loaded) && <p role="alert" className="db-alert">{failure || mutationError}{failure && loaded && ' Showing the last loaded page.'}</p>}
      {mutationError && failure && !loaded && <p role="alert" className="db-alert">{mutationError}</p>}
      {directoryError && <p role="alert" className="db-alert">{directoryError} Recruiter names and stages may be out of date. <Button size="sm" onClick={() => setRefreshToken(value => value + 1)}>Try again</Button></p>}

      {/* The pool counts are for the whole bank (or one recruiter); in "My new candidates" they would disagree with the list, so the cards wait for All candidates. */}
      {!mineNew && <DataBankStats cards={cards} onPress={card => setPool(current => poolAfterCardPress(current, card))} />}

      <Panel flush className="db-panel">
        {store.me.role === 'staff' && (
          <div className="dbt-scope">
            <div className="db-seg" role="group" aria-label="Which candidates to show">
              <button type="button" className={cn('db-seg-btn', mineNew && 'is-on')} aria-pressed={mineNew} onClick={showMyNew}>{MY_NEW_LABEL}</button>
              <button type="button" className={cn('db-seg-btn', !mineNew && 'is-on')} aria-pressed={!mineNew} onClick={showAll}>{ALL_CANDIDATES_LABEL}</button>
            </div>
            {mineNew && <p className="dbt-scope-note">{myNewNote()}</p>}
          </div>
        )}
        <div className="dbt-toolbar">
          <label className="db-search">
            <Search size={15} aria-hidden="true" />
            <input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search candidates, phone, email…" title="Finds candidate details as you type. Separate words with commas to match all of them, for example: ravi, care agency" aria-label="Search candidates by name, phone, email, agency, recruiter, skills or resume content. Separate words with commas to match all of them." />
          </label>
          <span className="dbt-sort">
            <Select aria-label="Sort candidates by" value={`${sort.key}:${sort.dir}`} onChange={event => { const [key, dir] = event.target.value.split(':'); setSort({ key, dir: dir as 'asc' | 'desc' }) }}>
              {SORT_OPTIONS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            </Select>
          </span>
          <button type="button" className="db-refresh" title="Refresh the list" aria-label="Refresh the list" onClick={() => { setSelected([]); setReassigning(null); setRefreshToken(value => value + 1) }}><RefreshCw size={15} /></button>
        </div>

        <DataBankFilters
          me={store.me}
          filters={columnFilters}
          onFilter={setColumnFilter}
          pool={pool}
          onPool={setPool}
          staff={staffFilter}
          onStaff={changeStaff}
          travel={travelled}
          onTravel={changeTravel}
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

        <div className="dbt-subbar">
          <p className="dbt-count tnum" role="status">{searching && !loaded ? 'Searching…' : `${countLine(mineNew, total, onFile)}${busy ? ' · updating…' : ''}`}</p>
          {!forceCards && (
            <div className="dbt-subtools">
              {!showCards && <>
                <button type="button" className="db-refresh" title={allOpen ? 'Collapse every row' : 'Expand every row to see all fields'} aria-label={allOpen ? 'Collapse all rows' : 'Expand all rows'} onClick={() => setExpanded(allOpen ? [] : rows.map(row => row.id))}>{allOpen ? <ChevronsDownUp size={15} /> : <ChevronsUpDown size={15} />}</button>
                <ColumnMenu columns={allColumns.filter((column) => column.key !== 'actions' && column.key !== 'source')} groups={BANK_GROUPS} shown={fit.keys} choices={columnChoices} onChange={setColumnChoices} hidden={fit.hidden} />
              </>}
              <ViewToggle view={view} onChange={changeView} />
            </div>
          )}
        </div>

        {!showCards && fit.hidden > 0 && <p className="ld-fitnote" role="status">{fit.hidden} {fit.hidden === 1 ? 'column is' : 'columns are'} hidden to fit the screen. Open a row (the arrow at its start) to see every field, or choose Columns.</p>}
        <div ref={boxRef} className={cn('db-box', fit.compact && 'is-compact', busy && loaded && 'is-busy')} aria-busy={busy || undefined}>
          {rows.length === 0 ? emptyState : showCards ? (
            <DataBankCards rows={rows} store={store} onOpen={openCandidate} onReassign={reassignOne} onEdit={setEditing} onOpenStage={setOpenStage} />
          ) : (
            <DbTable
              variant="grid"
              fit={!fit.wide}
              expandedIds={expanded}
              onToggleExpand={toggleExpand}
              renderExpanded={(candidate) => <DataBankExpanded candidate={candidate} store={store} context={fieldContext} onOpen={openCandidate} onReassign={reassignOne} onEdit={setEditing} />}
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
        <p className="db-hint">Candidates from every source. Open a candidate to see their details; view job allocations from Clients or Jobs.</p>
      </Panel>

      {reassigning && <ReassignCandidatesDialog candidateIds={reassigning} onClose={() => setReassigning(null)} onSaved={() => { setSelected([]); reloadPage() }} />}
      {unassigning && <UnassignCandidatesDialog candidateIds={unassigning} onClose={() => setUnassigning(null)} onSaved={() => { setSelected([]); setRefreshToken(value => value + 1) }} />}
      {editing && <CandidateEditDialog candidate={editing} onClose={() => setEditing(null)} onSaved={() => reloadPage()} />}
      <StageModal applicationId={openStage} open={!!openStage} onClose={() => setOpenStage(null)} />
    </div>
  )
}
