import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useLocation, useNavigate, useSearchParams } from 'react-router-dom'
import { CalendarPlus, ChevronsDownUp, ChevronsUpDown, Download, FileCheck2, PhoneOff, RefreshCw, Search, ThumbsUp, Users, UserRoundCheck, Upload, BellRing } from 'lucide-react'
import { cn } from '@/shared/lib/cn'
import { useStore } from '@/shared/state/crm-store'
import { EmptyState, PageHeader, Panel } from '@/shared/ui/display'
import { Button, Select } from '@/shared/ui/primitives'
import { DbTable, useElementWidth } from '@/features/candidates/components/db-table'
import { CHOICE_COMPACT, fitColumns } from '@/features/candidates/components/column-fit'
import { ColumnMenu, useColumnChoices } from '@/features/candidates/components/column-menu'
import { KpiCards, Pager, ViewToggle, readView, saveView, type DbView } from '@/features/candidates/components/db-parts'
import { LeadExpanded } from '@/features/leads/components/lead-columns'
import { leadColumns } from '@/features/leads/components/lead-column-definitions'
import { workBlock, type LeadColumnContext } from '@/features/leads/components/lead-actions'
import { FIELD_GROUPS } from '@/features/leads/lead-fields'
import { LeadCards } from '@/features/leads/components/lead-cards'
import { LeadFiltersBar } from '@/features/leads/components/lead-filters'
import { GateBanner } from '@/features/leads/components/gate-banner'
import { UploadLeadsDialog } from '@/features/leads/components/upload-dialog'
import { ImportHistory } from '@/features/leads/components/import-history'
import { LeadDrawer } from '@/features/leads/components/lead-drawer'
import { SafeBoundary } from '@/features/leads/components/safe-boundary'
import { CampaignsTab } from '@/features/leads/components/campaigns-tab'
import { LeadsSkeleton, useDelayedFlag } from '@/features/leads/components/leads-skeleton'
import { WaComposerHost } from '@/features/leads/components/wa-template'
import { SKELETON_GRACE_MS, countText, listView, skeletonRows } from '@/features/leads/leads-view-model'
import { CallDialog } from '@/features/leads/components/call-dialog'
import { ConvertDialog } from '@/features/leads/components/convert-dialog'
import { UnconvertDialog } from '@/features/leads/components/unconvert-dialog'
import { AssignLeadsDialog } from '@/features/leads/components/assign-dialog'
import { useLeadBulk } from '@/features/leads/use-lead-bulk'
import { downloadLeadExport, gateFromRefusal, leadApi, uploadLeadResume, type UploadDeletionResult } from '@/features/leads/lead-api'
import { useLeadData } from '@/features/leads/use-leads'
import { GATE_REASON, gateBlocks, gateState, publishGate, type LeadGate } from '@/features/leads/lead-gate'
import {
  EMPTY_FILTERS, LEAD_SORTS, footerHint, activeCard, activeLeadFilterCount, buildLeadQuery, cardFilters, columnsQuery, isSupervisor, searchHint, summaryScope,
  type ExtraColumn, type Lead, type LeadCard, type LeadFilters,
} from '@/features/leads/lead-model'
import { PAGE_SIZES, defaultPageSize, readPageSize, savePageSize } from '@/features/candidates/pager-model'
import '@/styles/data-bank-v2.css'
import '@/styles/data-bank-positions.css'
import '@/styles/leads.css'
import '@/styles/lead-extras.css'

type Tab = 'leads' | 'campaigns' | 'uploads'
const FRESH_MS = 8_000
const VIEW_SCOPE = 'leads'
/** Below this box width (a window of a window under 1100px wide) the leads read as cards; above it the one table always fits. */
const TABLE_MIN_WIDTH = 900

export default function LeadsPage() {
  const store = useStore()
  const navigate = useNavigate()
  const location = useLocation()
  const [params, setParams] = useSearchParams()
  const me = store.me
  const supervisor = isSupervisor(me)

  const [filters, setFilters] = useState<LeadFilters>(() => ({ ...EMPTY_FILTERS, batchId: params.get('batch') ?? '' }))
  const [sort, setSort] = useState({ key: 'created', dir: 'desc' as 'asc' | 'desc' })
  const [page, setPage] = useState(0)
  const [pageSize, setPageSizeState] = useState(() => readPageSize(VIEW_SCOPE) ?? defaultPageSize(typeof window === 'undefined' ? 900 : window.innerHeight))
  const setPageSize = (size: number) => { setPageSizeState(size); savePageSize(VIEW_SCOPE, size) }
  const [tab, setTab] = useState<Tab>('leads')
  const [view, setView] = useState<DbView>(() => readView(VIEW_SCOPE))
  const [moreOpen, setMoreOpen] = useState(false)
  const [uploading, setUploading] = useState(location.pathname.endsWith('/import'))
  const [open, setOpen] = useState<Lead | null>(null)
  const [calling, setCalling] = useState<Lead | null>(null)
  const [converting, setConverting] = useState<Lead | null>(null)
  const [unconverting, setUnconverting] = useState<Lead | null>(null)
  const [assigning, setAssigning] = useState<string[] | null>(null)
  const [forcedGate, setForcedGate] = useState<LeadGate | null>(null)
  const [fresh, setFresh] = useState('')
  const [historyKey, setHistoryKey] = useState(0)
  const [exporting, setExporting] = useState(false)
  const [message, setMessage] = useState('')
  const [boxRef, boxWidth] = useElementWidth<HTMLDivElement>()
  const [choices, setChoices] = useColumnChoices('leads-details-v2')

  const query = useMemo(() => buildLeadQuery(filters, sort), [filters, sort])
  const [extras, setExtras] = useState<ExtraColumn[]>([])
  const [expanded, setExpanded] = useState<string[]>([])
  /** `extra=*`: every lead comes with all its custom question answers, so a switched-on question column or an opened row never needs another request. */
  const listQuery = `${query}&extra=*`
  const data = useLeadData({ query: listQuery, page, pageSize, scope: summaryScope(filters), enabled: true })
  const { summary } = data

  // The custom question columns of the stored sheets (of the filtered campaign or upload when one is chosen); they follow new uploads.
  const columnsKey = columnsQuery(filters)
  useEffect(() => {
    const controller = new AbortController()
    leadApi.columns(columnsKey, controller.signal).then((result) => { if (!controller.signal.aborted) setExtras(result.columns ?? []) }).catch(() => { /* the core columns stay */ })
    return () => controller.abort()
  }, [columnsKey, data.facets])

  // The daily-work gate: the server's answer, replaced at once by a refusal the server just gave, until the next summary arrives.
  useEffect(() => { setForcedGate(null) }, [summary])
  const gate = forcedGate ?? summary?.dailyWorkGate ?? null
  const gateMode = gateState(me.role, gate)
  const blockedAll = gateBlocks(gateMode)
  useEffect(() => { if (me.role === 'staff' && gate) publishGate(me.id, gate) }, [me.role, me.id, gate])
  const onGateRefusal = useCallback((refused: LeadGate) => { setForcedGate(refused); data.reloadSummary() }, [data])

  const filterKey = query
  useEffect(() => { setPage(0) }, [filterKey, pageSize])
  const patchFilters = (patch: Partial<LeadFilters>) => setFilters((current) => ({ ...current, ...patch }))
  const clearFilters = () => { setFilters(EMPTY_FILTERS); setParams((previous) => { const next = new URLSearchParams(previous); next.delete('batch'); return next }) }
  const filterCount = activeLeadFilterCount(filters)
  const hasFilters = filterCount > 0 || Boolean(filters.q.trim())

  // A new upload shows its rows briefly highlighted.
  const freshTimer = useRef(0)
  const highlight = (batchId: string) => { setFresh(batchId); window.clearTimeout(freshTimer.current); freshTimer.current = window.setTimeout(() => setFresh(''), FRESH_MS) }
  useEffect(() => () => window.clearTimeout(freshTimer.current), [])

  const refreshAll = () => { bulk.clear(); data.refresh(); setHistoryKey((value) => value + 1) }
  const flash = (text: string) => { setMessage(text); window.setTimeout(() => setMessage((current) => (current === text ? '' : current)), 4_000) }
  // /leads?lead=<id> (the follow-up to-do, the candidate's "From lead sheet" badge) opens that lead's drawer.
  const leadParam = params.get('lead')
  useEffect(() => {
    if (!leadParam) return
    const controller = new AbortController()
    setTab('leads')
    leadApi.detail(leadParam, controller.signal).then((detail) => {
      if (controller.signal.aborted) return
      setOpen(detail)
      setParams((previous) => { const next = new URLSearchParams(previous); next.delete('lead'); return next }, { replace: true })
    }).catch(() => { if (!controller.signal.aborted) { flash('That lead was not found. It may have been deleted.'); setParams((previous) => { const next = new URLSearchParams(previous); next.delete('lead'); return next }, { replace: true }) } })
    return () => controller.abort()
  }, [leadParam]) // eslint-disable-line react-hooks/exhaustive-deps
  const bulk = useLeadBulk({
    query, sort, pageSize, rows: data.page?.items ?? [], total: data.page?.total ?? 0, users: store.users, supervisor, isAdmin: me.role === 'admin',
    refresh: data.refresh, flash, onGateRefusal, onDeleted: (lead) => setOpen((current) => (current?.id === lead.id ? null : current)),
  })

  const viewBatch = (batchId: string) => {
    setUploading(false); setTab('leads')
    setFilters({ ...EMPTY_FILTERS, batchId })
    setParams((previous) => { const next = new URLSearchParams(previous); next.set('batch', batchId); return next })
    if (location.pathname.endsWith('/import')) navigate({ pathname: '/leads', search: `?batch=${batchId}` }, { replace: true })
  }

  /** A campaign row on the Campaigns tab: back to the Leads tab with that campaign as the only filter. */
  const viewCampaign = (campaign: string) => {
    setTab('leads')
    setFilters({ ...EMPTY_FILTERS, campaign })
    setParams((previous) => { const next = new URLSearchParams(previous); next.delete('batch'); return next })
  }

  const uploadDeleted = (result: UploadDeletionResult) => {
    setFilters(current => current.batchId === result.batchId ? { ...current, batchId: '' } : current)
    setParams(previous => {
      const next = new URLSearchParams(previous)
      if (next.get('batch') === result.batchId) next.delete('batch')
      next.delete('lead')
      return next
    }, { replace: true })
    setPage(0); setOpen(null); setCalling(null); setConverting(null); setUnconverting(null); setAssigning(null); setExpanded([]); setFresh('')
    refreshAll()
    flash(`Upload permanently deleted. ${result.deletedLeads.toLocaleString()} ${result.deletedLeads === 1 ? 'lead removed' : 'leads removed'}.${result.duplicateRows > 0 ? ' Existing leads from other uploads were kept.' : ''}`)
  }

  const uploadResume = useCallback(async (lead: Lead, file: File) => {
    try { await uploadLeadResume(lead.id, file); flash(`Resume attached to ${lead.name}.`); data.refresh() } catch (error) {
      const refused = gateFromRefusal(error)
      if (refused) onGateRefusal(refused)
      flash(error instanceof Error ? error.message : 'The resume was not uploaded.')
      throw error
    }
  }, [data, onGateRefusal])

  const context: LeadColumnContext = {
    me: { id: me.id, role: me.role },
    gate: gateMode,
    userById: store.user,
    canAssign: supervisor,
    onOpen: setOpen,
    onLogCall: setCalling,
    onConvert: setConverting,
    onUploadResume: (lead, file) => { void uploadResume(lead, file).catch(() => {}) },
    onAssign: (lead) => setAssigning([lead.id]),
    onNotInterested: (lead) => { void bulk.quickStatus(lead, 'not_interested') },
    onChangeStatus: bulk.openStatus,
    canDelete: me.role === 'admin',
    onDelete: bulk.openDelete,
    onUnconvert: setUnconverting,
  }
  const allColumns = useMemo(() => leadColumns(context, extras), [me.id, me.role, gateMode, supervisor, store.users, extras]) // eslint-disable-line react-hooks/exhaustive-deps
  const fit = useMemo(() => fitColumns(allColumns, boxWidth, choices), [allColumns, boxWidth, choices])
  const shownKeys = fit.keys
  const columns = useMemo(() => allColumns.filter((column) => shownKeys.includes(column.key)), [allColumns, shownKeys])
  const menuColumns = useMemo(() => allColumns.filter((column) => column.key !== 'actions').map((column) => ({
    key: column.key, header: column.header, group: column.group, hint: column.key.startsWith('x:') ? String(extras.find((item) => `x:${item.key}` === column.key)?.count ?? '') : undefined,
  })), [allColumns, extras])
  const toggleExpand = (id: string) => setExpanded((current) => (current.includes(id) ? current.filter((value) => value !== id) : [...current, id]))

  const exportExcel = async () => {
    setExporting(true)
    try { await downloadLeadExport(query) } catch (error) { flash(error instanceof Error ? error.message : 'We could not make the Excel file.') } finally { setExporting(false) }
  }

  const rows = data.page?.items ?? []
  const total = data.page?.total ?? 0
  const forceCards = boxWidth > 0 && boxWidth < TABLE_MIN_WIDTH
  const showCards = forceCards || view === 'cards'
  const card = activeCard(filters)
  const pickCard = (next: LeadCard) => patchFilters({ followUp: '', ...(card === 'follow_ups' ? { assigned: '' } : {}), ...(next === 'follow_ups' && card !== 'follow_ups' ? { ...cardFilters('follow_ups'), assigned: 'me' } : card === next && next !== 'total' ? cardFilters('total') : cardFilters(next)) })
  const changeView = (next: DbView) => { setView(next); saveView(VIEW_SCOPE, next) }
  const busy = data.loading || data.stale
  const slowFilter = useDelayedFlag(data.loading && data.stale, SKELETON_GRACE_MS)
  const listState = listView({ loading: data.loading, stale: data.stale, hasPage: Boolean(data.page), error: data.error, rows: rows.length, slow: slowFilter })
  const allOpen = rows.length > 0 && rows.every((row) => expanded.includes(row.id))
  const openLive = open ? rows.find((row) => row.id === open.id) ?? open : null

  const fmt = (value?: number) => (value === undefined ? '–' : value.toLocaleString())
  const kpis = [
    { label: 'Total leads', value: fmt(summary?.total), detail: summary?.scope === 'mine' ? 'Assigned to you' : 'Everyone who answered your ads', icon: Users, active: card === 'total', onSelect: () => pickCard('total') },
    { label: 'New today', value: fmt(summary?.newToday), detail: 'Arrived today', icon: CalendarPlus, active: card === 'new_today', onSelect: () => pickCard('new_today') },
    { label: 'Not contacted', value: fmt(summary?.uncontacted), detail: 'Nobody has called yet', icon: PhoneOff, tone: 'warn' as const, active: card === 'not_contacted', onSelect: () => pickCard('not_contacted') },
    { label: 'Interested', value: fmt(summary?.interested), detail: 'Said yes on the phone', icon: ThumbsUp, tone: 'ok' as const, active: card === 'interested', onSelect: () => pickCard('interested') },
    { label: 'Resume received', value: fmt(summary?.resumeReceived), detail: 'Ready to convert', icon: FileCheck2, tone: 'ok' as const, active: card === 'resume_received', onSelect: () => pickCard('resume_received') },
    { label: 'Converted', value: fmt(summary?.converted), detail: 'Now candidates', icon: UserRoundCheck, tone: 'quiet' as const, active: card === 'converted', onSelect: () => pickCard('converted') },
    { label: 'Follow-ups due', value: fmt(summary?.followUpsDueMine), detail: 'Yours, due today or overdue', icon: BellRing, tone: 'warn' as const, active: card === 'follow_ups', onSelect: () => pickCard('follow_ups') },
  ]

  const neverUploaded = !data.loading && !data.error && total === 0 && !hasFilters && (summary?.total ?? 0) === 0
  const emptyState = data.error && !data.page
    ? <EmptyState title="We could not load the leads" description={data.error} action={<Button variant="primary" onClick={refreshAll}>Try again</Button>} />
    : hasFilters
      ? <EmptyState title={data.loading ? 'Searching leads…' : 'No matching leads'} description="Try another search or filter, or clear the filters to see every lead." action={<Button onClick={clearFilters}>Clear filters</Button>} />
      : neverUploaded
        ? <EmptyState title="No leads yet" description={supervisor ? 'Upload the sheet your ad platform gave you (CSV or Excel). Every person is stored once, so you can keep uploading new sheets.' : 'When a manager uploads a lead sheet and assigns leads to you, they appear here.'} action={supervisor ? <Button variant="primary" icon={<Upload className="h-4 w-4" />} onClick={() => setUploading(true)}>Upload leads</Button> : undefined} />
        : <EmptyState title={data.loading ? 'Loading leads…' : 'No leads to show'} description="Nothing matches yet." />

  return (
    <div className="db ld">
      <PageHeader
        title="Lead sheet"
        description="People who responded to your ads. Call them, collect their updated resume, and turn them into candidates."
        actions={supervisor ? (
          <>
            <Button variant="secondary" icon={<Download className="h-4 w-4" />} disabled={exporting || total === 0} onClick={() => void exportExcel()}>{exporting ? 'Preparing…' : 'Export'}</Button>
            <Button variant="primary" icon={<Upload className="h-4 w-4" />} onClick={() => setUploading(true)}>Upload leads</Button>
          </>
        ) : undefined}
      />

      <GateBanner state={gateMode} gate={gate} />
      {message && <p role="status" className="ld-toast">{message}</p>}
      {data.error && data.page && <p role="alert" className="db-alert">{data.error} Showing the last loaded page.</p>}

      <KpiCards items={kpis} />

      <Panel flush className="db-panel">
        <div className="ld-tabs-bar" role="tablist" aria-label="Lead sheet">
          <button type="button" role="tab" aria-selected={tab === 'leads'} className={cn(tab === 'leads' && 'is-on')} onClick={() => setTab('leads')}>Leads</button>
          <button type="button" role="tab" aria-selected={tab === 'campaigns'} className={cn(tab === 'campaigns' && 'is-on')} onClick={() => setTab('campaigns')}>Campaigns</button>
          {supervisor && <button type="button" role="tab" aria-selected={tab === 'uploads'} className={cn(tab === 'uploads' && 'is-on')} onClick={() => setTab('uploads')}>Uploads</button>}
        </div>

        {tab === 'campaigns' ? (
          <CampaignsTab refreshKey={historyKey} onPickCampaign={viewCampaign} canAdmin={me.role === 'admin'} onManageUploads={() => setTab('uploads')} />
        ) : tab === 'uploads' && supervisor ? (
          <ImportHistory supervisor={supervisor} canAdmin={me.role === 'admin'} refreshKey={historyKey} onViewBatch={viewBatch} onUpload={() => setUploading(true)} onDeleted={uploadDeleted} />
        ) : (
          <>
            <div className="db-panel-head">
              <div className="min-w-0">
                <h2 className="db-panel-title">{card && card !== 'total' ? kpis.find((item) => item.active)?.label : filters.batchId ? 'One upload' : 'All leads'}</h2>
                <p className="db-panel-sub tnum">{countText({ loading: data.loading, hasPage: Boolean(data.page), total, busy, error: data.error })}</p>
              </div>
              <div className="db-panel-tools">
                <label className="db-search">
                  <Search size={15} aria-hidden="true" />
                  <input type="search" value={filters.q} onChange={(event) => patchFilters({ q: event.target.value })} placeholder="Search name, phone, job, campaign…" aria-label="Search leads. Separate words with commas to match all of them." />
                </label>
                <Select className="db-sort" aria-label="Sort leads by" value={`${sort.key}:${sort.dir}`} onChange={(event) => { const [key, dir] = event.target.value.split(':'); setSort({ key, dir: dir as 'asc' | 'desc' }) }}>
                  {LEAD_SORTS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                </Select>
                <button type="button" className="db-refresh" title="Refresh the list" aria-label="Refresh the list" onClick={refreshAll}><RefreshCw size={15} /></button>
                {!showCards && <>
                  <button type="button" className="db-refresh" title={allOpen ? 'Collapse every row' : 'Expand every row to see all fields'} aria-label={allOpen ? 'Collapse all rows' : 'Expand all rows'} onClick={() => setExpanded(allOpen ? [] : rows.map((row) => row.id))}>{allOpen ? <ChevronsDownUp size={15} /> : <ChevronsUpDown size={15} />}</button>
                  <ColumnMenu columns={menuColumns} groups={FIELD_GROUPS} hidden={fit.hidden} shown={shownKeys} choices={choices} onChange={setChoices} />
                </>}
                {!forceCards && <ViewToggle view={view} onChange={changeView} />}
              </div>
            </div>
            <p className="dbp-tip ld-tip">{searchHint(filters.q)}</p>

            <LeadFiltersBar filters={filters} facets={data.facets} users={store.users} meId={me.id} supervisor={supervisor} onChange={patchFilters}
              moreOpen={moreOpen} onMoreToggle={() => setMoreOpen((value) => !value)} canClear={hasFilters} onClear={clearFilters} />

            {fit.hidden > 0 && !showCards && <p className="ld-fitnote" role="status">{fit.hidden} {fit.hidden === 1 ? 'column is' : 'columns are'} hidden to fit the screen. Open a row (the arrow at its start) to see every field, or choose Columns.</p>}
            {bulk.bar}
            <div ref={boxRef} className={cn('db-box', fit.compact && 'is-compact', busy && rows.length > 0 && 'is-busy')} aria-busy={busy || undefined}>
              <SafeBoundary what="the lead table" resetKey={data.page} onRetry={refreshAll}>
              {listState === 'skeleton' ? <LeadsSkeleton rows={skeletonRows(pageSize)} columns={showCards ? 3 : 7} /> : rows.length === 0 ? emptyState : showCards ? (
                <LeadCards rows={rows} context={context} onOpen={setOpen} selectable={supervisor} selected={bulk.selectedIds} onSelect={bulk.onSelectedChange} freshBatch={fresh} />
              ) : (
                <DbTable<Lead>
                  variant="grid" fit={!fit.wide} expandedIds={expanded} onToggleExpand={toggleExpand} renderExpanded={(lead) => <LeadExpanded lead={lead} extras={extras} context={context} />} label="Leads" rows={rows} columns={columns} rowKey={(l) => l.id}
                  selectable={supervisor} selected={bulk.selectedIds} onSelectedChange={bulk.onSelectedChange} onRowClick={setOpen}
                  rowClassName={(l) => cn(fresh && l.batchId === fresh && 'is-fresh')}
                  sort={sort} onSort={(key) => setSort((current) => ({ key, dir: current.key === key && current.dir === 'asc' ? 'desc' : 'asc' }))}
                />
              )}
              </SafeBoundary>
            </div>
            {rows.length > 0 && <Pager page={data.page?.page ?? page} pageSize={pageSize} total={total} noun="leads" sizes={PAGE_SIZES} allowAll filtered={hasFilters} onPage={setPage} onPageSize={setPageSize} />}
            <p className="db-hint ld-hint">{footerHint({
              supervisor, lockReason: blockedAll ? GATE_REASON : null, matching: total, total: summary?.total ?? total, filtersOn: hasFilters,
              assignedToMe: supervisor ? undefined : summary?.total, unassigned: supervisor ? summary?.unassigned : undefined,
            })}{hasFilters && <> <button type="button" className="ld-linkbtn" onClick={clearFilters}>Clear filters</button></>}</p>
          </>
        )}
      </Panel>

      <UploadLeadsDialog open={uploading} users={store.users} onClose={() => { setUploading(false); if (location.pathname.endsWith('/import')) navigate('/leads', { replace: true }) }}
        onImported={(result) => { if (result.batchId) highlight(result.batchId); refreshAll() }} onViewBatch={viewBatch} />

      <SafeBoundary what="this lead" resetKey={openLive?.id} onRetry={() => setOpen(null)}>
      <LeadDrawer
        lead={openLive} me={{ id: me.id, role: me.role }} users={store.users} gate={gateMode} version={data.page} canAssign={supervisor}
        workRefusal={openLive ? workBlock({ me: { id: me.id, role: me.role }, gate: 'open', userById: store.user }, openLive) : null}
        onClose={() => setOpen(null)} onChanged={() => data.refresh()}
        onLogCall={setCalling} onConvert={setConverting} onAssign={(lead) => setAssigning([lead.id])}
        onUploadResume={uploadResume} onGateRefusal={onGateRefusal}
        canDelete={me.role === 'admin'} onDelete={bulk.openDelete} onUnconvert={setUnconverting} onStatusChange={bulk.offerUndo}
      />
      </SafeBoundary>
      {bulk.overlays}
      <WaComposerHost />
      <CallDialog lead={calling} onClose={() => setCalling(null)} blockedReason={calling ? workBlock(context, calling) : null} onGateRefusal={onGateRefusal}
        onSaved={() => { setCalling(null); flash('Call saved.'); data.refresh() }} />
      <ConvertDialog lead={converting} onClose={() => setConverting(null)} blockedReason={converting ? workBlock(context, converting) : null} onGateRefusal={onGateRefusal} onChanged={() => data.refresh()} />
      {me.role === 'admin' && <UnconvertDialog lead={unconverting} onClose={() => setUnconverting(null)} onSaved={(result) => {
        setUnconverting(null); setOpen((current) => current?.id === result.lead.id ? result.lead : current)
        bulk.clear(); data.refresh(); flash(`${result.lead.name} moved back to Lead.`)
        void store.refresh({ keepCurrent: true }).catch(() => store.notify('Moved back to Lead. Reload the workspace to see the latest candidate list.', 'warn'))
      }} />}
      <AssignLeadsDialog ids={assigning} users={store.users} onClose={() => setAssigning(null)}
        onSaved={(assigned, skipped) => { setAssigning(null); bulk.clear(); flash(`${assigned} ${assigned === 1 ? 'lead' : 'leads'} assigned${skipped ? `, ${skipped} skipped` : ''}.`); data.refresh() }} />
    </div>
  )
}

