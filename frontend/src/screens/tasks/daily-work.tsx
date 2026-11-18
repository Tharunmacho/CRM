import { useMemo, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { CheckCircle2, Layers, ListChecks, RefreshCw, TriangleAlert, UserX, Users } from 'lucide-react'
import { useStore } from '@/shared/state/crm-store'
import { Button, Select } from '@/shared/ui/primitives'
import { EmptyState, PageHeader } from '@/shared/ui/display'
import { ReminderBanner } from '@/features/daily-report/reminder-banner'
import { StageModal } from '@/features/workflow/components/stage-modal'
import { applicationFormHref } from '@/features/application-form/form-model'
import { listTitle, splitItems, statusCounts, summaryLine, teamTotals } from '@/features/daily-work/daily-work-model'
import { useDailyWork, useFollowUp, useTicker } from '@/features/daily-work/use-daily-work'
import { DailyTable, type DailyGroup } from './daily-table'
import { StaffBoard } from './daily-staff-board'
import { StatStrip, type StatTileData } from './daily-stat-strip'
import { FollowUpTable } from './follow-up-table'
import { TravelledPanel } from './travelled-panel'
import '@/styles/daily-work.css'
import '@/styles/daily-work-board.css'

/**
 * The recruiter's own candidates that are still being worked, as one flat list: no daily quota, no morning/evening, no hourly
 * slots, no deadlines. Candidates with a stage waiting come first, then the newest assigned; the ones finished today sit under
 * "Done today". Admin and manager see everyone's candidates with a Recruiter column and can filter by recruiter.
 */
export function DailyWork({ onOpenAll }: { onOpenAll: () => void }) {
  const store = useStore()
  const [params, setParams] = useSearchParams()
  const navigate = useNavigate()
  const supervisor = store.can('all_reports')
  const staffFilter = supervisor ? params.get('staff') || 'all' : undefined
  const { data, error, loading, refresh } = useDailyWork({ staff: staffFilter })
  // The follow-up total for the summary strip: the whole team's, whichever recruiter the list below is showing (same request the tab counts make).
  const followUp = useFollowUp({ staff: 'all', enabled: supervisor })
  const followUpList = useFollowUp({ staff: staffFilter })
  const now = useTicker(60_000, !!followUpList.data)
  const [idleOnly, setIdleOnly] = useState(false)
  const [openApp, setOpenApp] = useState<{ id: string; stage?: number } | null>(null)
  const zone = store.agencyTimezone

  const setStaff = (value: string) => setParams((previous) => {
    const next = new URLSearchParams(previous)
    if (value && value !== 'all') next.set('staff', value); else next.delete('staff')
    return next
  })

  const items = data?.items
  const groups: DailyGroup[] = useMemo(() => {
    const { toWork, done } = splitItems(items ?? [])
    return [
      { key: 'to-work', title: 'To work', icon: <ListChecks size={15} aria-hidden="true" />, items: toWork },
      { key: 'done-today', title: 'Done today', icon: <CheckCircle2 size={15} aria-hidden="true" />, items: done, tone: 'done' as const },
    ].filter((g) => g.items.length > 0)
  }, [items])

  if (loading && !data) return <div className="tk dw"><PageHeader title="Tasks" description="Your candidates." />
    <section className="dw-panel dw-state" aria-busy="true"><p role="status">Loading your candidates…</p></section></div>
  if (error && !data) return <div className="tk dw"><PageHeader title="Tasks" description="Your candidates." />
    <section className="dw-panel dw-state"><TriangleAlert size={22} aria-hidden="true" />
      <p role="alert" className="dw-state-title">{error}</p><p>We could not load the list. Check your connection and try again.</p>
      <Button variant="primary" onClick={() => void refresh()}>Try again</Button></section></div>
  if (!data) return null

  const summary = data.summary
  const mine = !supervisor
  const roster = data.staff ?? []
  const person = supervisor && staffFilter && staffFilter !== 'all' ? roster.find((s) => s.userId === staffFilter)?.name ?? store.user(staffFilter)?.name ?? 'Recruiter' : ''
  const title = mine ? "Today's candidates" : listTitle(person)
  // The strip is always the whole team (the per-recruiter list covers everyone), so picking a recruiter below never changes it.
  const team = teamTotals(roster)
  const counts = statusCounts(roster)
  const tiles: StatTileData[] = [
    { key: 'to-work', label: 'Candidates to work today', value: team.toWork, icon: ListChecks, detail: `Across ${roster.length} ${roster.length === 1 ? 'recruiter' : 'recruiters'}` },
    { key: 'done', label: 'Done today', value: team.doneToday, unit: `of ${team.total}`, icon: CheckCircle2, tone: 'green', progress: { done: team.doneToday, total: team.total } },
    { key: 'follow-up', label: 'Follow-up candidates', value: followUp.data?.summary.total ?? '–', icon: Layers, detail: 'Selected and still moving', actionLabel: 'Show below', onSelect: () => document.getElementById('follow-up')?.scrollIntoView({ block: 'start', behavior: 'smooth' }) },
    { key: 'idle', label: 'Nothing done yet', value: counts.idle, unit: counts.idle === 1 ? 'recruiter' : 'recruiters', icon: UserX, tone: counts.idle > 0 ? 'red' : 'green',
      detail: counts.idle > 0 ? (idleOnly ? 'Showing them above' : 'Select to see who') : 'Everyone has started',
      ...(counts.idle > 0 ? { onSelect: () => { setIdleOnly((v) => !v); document.getElementById('recruiters')?.scrollIntoView({ block: 'nearest', behavior: 'smooth' }) }, active: idleOnly, actionLabel: 'Show only these recruiters' } : {}) },
  ]

  return (
    <div className="tk dw dwb">
      <ReminderBanner />
      <PageHeader
        title="Tasks"
        description={mine
          ? 'Candidates assigned to you who still need prescreening.'
          : 'Candidates who still need prescreening, for every recruiter.'}
        actions={<>
          <Button onClick={onOpenAll}><ListChecks size={15} aria-hidden="true" />All 48-hour tasks</Button>
          <Button onClick={() => void refresh()}><RefreshCw size={15} aria-hidden="true" />Refresh</Button>
        </>}
      />

      <div className="dw-alerts">
        {data.truncated && <p className="is-warn" role="status"><TriangleAlert size={16} aria-hidden="true" />
          Only the first {data.items.length} of {summary.total} candidates are listed. {supervisor ? 'Pick a recruiter to see all of theirs.' : 'The counts above are complete.'}</p>}
        {error && <p className="is-warn" role="status"><TriangleAlert size={16} aria-hidden="true" />The list could not refresh just now ({error}). Showing the last version.</p>}
      </div>

      {supervisor && <StatStrip tiles={tiles} label="Today at a glance, whole team" />}
      {supervisor && <StaffBoard staff={roster} selected={staffFilter ?? 'all'} onSelect={setStaff} idleOnly={idleOnly} onClearIdle={() => setIdleOnly(false)} />}

      <section className="dw-panel" aria-label="Today's candidates">
        <div className="dw-panel-head">
          <div><h2>{supervisor && <Users size={16} aria-hidden="true" />} {title}</h2>
            <p className="dw-summary" aria-live="polite">{summaryLine(summary)}{supervisor && !person ? ` · ${data.staffCount} ${data.staffCount === 1 ? 'recruiter' : 'recruiters'}` : ''}</p></div>
          {supervisor && <Select value={staffFilter ?? 'all'} onChange={(e) => setStaff(e.target.value)} className="tk-select" aria-label="Filter by recruiter">
            <option value="all">All recruiters</option>
            {(data.staff ?? []).map((s) => <option key={s.userId} value={s.userId}>{s.name}{!s.active ? ' (inactive)' : ''}</option>)}
          </Select>}
        </div>

        {data.items.length === 0 ? (
          <EmptyState title={mine ? 'No candidates to work' : person ? `${person} has no candidates today` : 'No candidates to work today'}
            description={mine
              ? 'Candidates assigned to you appear here until prescreening is finished. Nothing needs prescreening right now.'
              : person ? 'Nothing is assigned to this recruiter that still needs prescreening. Pick another recruiter, or show everyone.'
                : 'Candidates appear here once they are assigned to a recruiter and still need prescreening.'}
            action={person ? <Button onClick={() => setStaff('all')}>Show all recruiters</Button> : undefined} />
        ) : (
          <DailyTable groups={groups} zone={zone} supervisor={supervisor} staffName={(id) => store.user(id)?.name}
            canWork={(item) => !!item.taskId && (store.can('work_any_candidate') || item.ownerId === store.me.id)}
            onOpen={(item) => item.applicationId ? setOpenApp({ id: item.applicationId, stage: item.stage ?? undefined }) : navigate(applicationFormHref(item.candidateId))}
            onProfile={(item) => navigate(`/candidates/${item.candidateId}`)}
            onRemark={async (item, text) => { if (item.taskId) { await store.addRemark(item.taskId, text); await refresh() } }} />
        )}
      </section>

      <section className="dw-panel" aria-label="Follow-up" id="follow-up">
        <div className="dw-panel-head">
          <div><h2><Layers size={16} aria-hidden="true" /> Follow-up</h2>
            <p className="dw-summary" aria-live="polite">{followUpList.data?.summary.total ?? 0} {(followUpList.data?.summary.total ?? 0) === 1 ? 'candidate' : 'candidates'}</p></div>
        </div>
        {!followUpList.data ? <p className="dw-state" role="status">Loading follow-up candidates…</p>
          : followUpList.data.items.length === 0 ? <EmptyState title={mine ? 'No candidates to follow up' : 'No follow-up candidates for this selection'} description="A candidate shows here once prescreening is finished and stays while the application is still going forward." />
            : <FollowUpTable items={followUpList.data.items} now={now} supervisor={supervisor} staffName={(id) => store.user(id)?.name}
              canWork={(item) => !!item.taskId && (store.can('work_any_candidate') || item.ownerId === store.me.id)}
              onOpen={(item) => navigate(applicationFormHref(item.candidateId))}
              onStage={(item) => setOpenApp({ id: item.applicationId, stage: item.stage })}
              onProfile={(item) => navigate(`/candidates/${item.candidateId}`)}
              onRemark={async (item, text) => { if (item.taskId) { await store.addRemark(item.taskId, text); await followUpList.refresh(); await refresh() } }} />}
      </section>
      <TravelledPanel supervisor={supervisor} recruiterName={person} onOpen={(row) => navigate(applicationFormHref(row.candidateId))} />
      <StageModal applicationId={openApp?.id ?? null} open={!!openApp} focusStage={openApp?.stage} onClose={() => { setOpenApp(null); void refresh(); void followUpList.refresh() }} />
    </div>
  )
}
