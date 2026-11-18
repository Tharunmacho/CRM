import { useMemo, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { ListChecks, PhoneCall, RefreshCw, TriangleAlert, UserX, Users, Workflow } from 'lucide-react'
import { useStore } from '@/shared/state/crm-store'
import { Button, Select } from '@/shared/ui/primitives'
import { EmptyState, PageHeader } from '@/shared/ui/display'
import { ReminderBanner } from '@/features/daily-report/reminder-banner'
import { StageModal } from '@/features/workflow/components/stage-modal'
import { applicationFormHref } from '@/features/application-form/form-model'
import { NO_WORK, listTitle, recruiterWork, taskRows, workStatus } from '@/features/daily-work/daily-work-model'
import { useDailyWork, useFollowUp } from '@/features/daily-work/use-daily-work'
import { TaskTable } from './task-table'
import { WorkBoard } from './daily-staff-board'
import { StatStrip, type StatTileData } from './daily-stat-strip'
import '@/styles/daily-work.css'
import '@/styles/daily-work-board.css'

/**
 * Every recruiter's candidates on one page: the ones they hold (to call, and moving through the stages) and what happened to each
 * today: called, stage moved, remark written. The cards show, per recruiter, how many candidates they hold and how many of them
 * were called, moved or remarked today; a card filters the list. A recruiter sees only their own.
 */
export function DailyWork({ onOpenAll }: { onOpenAll: () => void }) {
  const store = useStore()
  const [params, setParams] = useSearchParams()
  const navigate = useNavigate()
  const supervisor = store.can('all_reports')
  const staffFilter = supervisor ? params.get('staff') || 'all' : undefined
  const daily = useDailyWork({ staff: staffFilter })
  const follow = useFollowUp({ staff: staffFilter })
  // The cards and the strip always cover everyone, whichever recruiter the list below is filtered to.
  const filtered = supervisor && staffFilter !== 'all'
  const everyoneDaily = useDailyWork({ staff: 'all', enabled: filtered })
  const everyoneFollow = useFollowUp({ staff: 'all', enabled: filtered })
  const [idleOnly, setIdleOnly] = useState(false)
  const [openApp, setOpenApp] = useState<{ id: string; stage?: number } | null>(null)
  const zone = store.agencyTimezone

  const setStaff = (value: string) => setParams((previous) => {
    const next = new URLSearchParams(previous)
    if (value && value !== 'all') next.set('staff', value); else next.delete('staff')
    return next
  })
  const refreshAll = async () => { await Promise.all([daily.refresh(), follow.refresh(), ...(filtered ? [everyoneDaily.refresh(), everyoneFollow.refresh()] : [])]) }

  const today = daily.data?.agencyDate ?? ''
  const rows = useMemo(() => taskRows(daily.data?.items ?? [], follow.data?.items ?? [], today, zone), [daily.data, follow.data, today, zone])
  const boardDaily = filtered ? everyoneDaily.data : daily.data
  const boardFollow = filtered ? everyoneFollow.data : follow.data
  const everyoneRows = useMemo(() => taskRows(boardDaily?.items ?? [], boardFollow?.items ?? [], today, zone), [boardDaily, boardFollow, today, zone])
  const work = useMemo(() => recruiterWork(everyoneRows), [everyoneRows])

  if (daily.loading && !daily.data) return <div className="tk dw"><PageHeader title="Tasks" />
    <section className="dw-panel dw-state" aria-busy="true"><p role="status">Loading your candidates…</p></section></div>
  if (daily.error && !daily.data) return <div className="tk dw"><PageHeader title="Tasks" />
    <section className="dw-panel dw-state"><TriangleAlert size={22} aria-hidden="true" />
      <p role="alert" className="dw-state-title">{daily.error}</p><p>We could not load the list. Check your connection and try again.</p>
      <Button variant="primary" onClick={() => void refreshAll()}>Try again</Button></section></div>
  if (!daily.data) return null

  const data = daily.data
  const mine = !supervisor
  const roster = (data.staff ?? []).map((s) => ({ userId: s.userId, name: s.name, active: s.active }))
  // A recruiter's own card: the same figures, for themselves only.
  if (!supervisor && !roster.some((r) => r.userId === store.me.id)) roster.push({ userId: store.me.id, name: store.me.name, active: true })
  for (const s of follow.data?.staff ?? []) if (!roster.some((r) => r.userId === s.userId)) roster.push({ userId: s.userId, name: s.name, active: s.active })
  const person = supervisor && staffFilter && staffFilter !== 'all' ? roster.find((s) => s.userId === staffFilter)?.name ?? store.user(staffFilter)?.name ?? 'Recruiter' : ''
  const title = mine ? 'My candidates' : listTitle(person)

  // The strip: the whole team for an admin or manager, the person's own numbers for a recruiter.
  const totals = [...work.values()].reduce((sum, w) => ({ assigned: sum.assigned + w.assigned, called: sum.called + w.called, moved: sum.moved + w.moved,
    remarks: sum.remarks + w.remarks, untouched: sum.untouched + w.untouched, calledAll: sum.calledAll + w.calledAll, movedAll: sum.movedAll + w.movedAll }), { ...NO_WORK })
  const idle = roster.filter((s) => workStatus(work.get(s.userId) ?? NO_WORK) === 'idle').length
  const tiles: StatTileData[] = [
    { key: 'assigned', label: 'Candidates assigned', value: totals.assigned, icon: ListChecks, unit: supervisor ? `${roster.length} ${roster.length === 1 ? 'recruiter' : 'recruiters'}` : undefined },
    { key: 'called', label: 'Called today', value: totals.called, unit: `of ${totals.assigned}`, icon: PhoneCall, tone: 'green', progress: { done: totals.called, total: totals.assigned },
      detail: `Overall ${totals.calledAll}`, title: 'Candidates called today: the first call answered, a remark written, or a stage finished. Overall: candidates called at any time.' },
    { key: 'moved', label: 'Stages moved today', value: totals.moved, icon: Workflow, tone: 'blue', detail: `Overall ${totals.movedAll}`, title: 'Stages finished today. Overall: the stages the candidates have been through.' },
    supervisor
      ? { key: 'idle', label: 'Nothing done yet', value: idle, unit: idle === 1 ? 'recruiter' : 'recruiters', icon: UserX, tone: idle > 0 ? 'red' : 'green',
        ...(idle > 0 ? { onSelect: () => { setIdleOnly((v) => !v); document.getElementById('recruiters')?.scrollIntoView({ block: 'nearest', behavior: 'smooth' }) }, active: idleOnly, actionLabel: 'Show only recruiters with nothing done' } : {}) }
      : { key: 'untouched', label: 'Not touched yet', value: totals.untouched, unit: `of ${totals.assigned}`, icon: UserX, tone: totals.untouched > 0 ? 'amber' : 'green' },
  ]

  const summary = `${rows.length} ${rows.length === 1 ? 'candidate' : 'candidates'}${supervisor && !person ? ` · ${data.staffCount} ${data.staffCount === 1 ? 'recruiter' : 'recruiters'}` : ''}`
  const open = (row: { applicationId: string | null; candidateId: string; stage: number | null }) =>
    row.applicationId ? setOpenApp({ id: row.applicationId, stage: row.stage ?? undefined }) : navigate(applicationFormHref(row.candidateId))

  return (
    <div className="tk dw dwb">
      <ReminderBanner />
      <PageHeader title="Tasks"
        actions={<>
          <Button onClick={onOpenAll}><ListChecks size={15} aria-hidden="true" />All 48-hour tasks</Button>
          <Button onClick={() => void refreshAll()}><RefreshCw size={15} aria-hidden="true" />Refresh</Button>
        </>} />

      <div className="dw-alerts">
        {(data.truncated || follow.data?.truncated) && <p className="is-warn" role="status"><TriangleAlert size={16} aria-hidden="true" />
          Not every candidate is listed. {supervisor ? 'Pick a recruiter to see all of theirs.' : ''}</p>}
        {daily.error && <p className="is-warn" role="status"><TriangleAlert size={16} aria-hidden="true" />The list could not refresh just now ({daily.error}). Showing the last version.</p>}
      </div>

      <StatStrip tiles={tiles} label={supervisor ? 'Today at a glance, whole team' : 'Today at a glance'} />
      {supervisor
        ? <WorkBoard people={roster} work={work} selected={staffFilter ?? 'all'} onSelect={setStaff} idleOnly={idleOnly} onClearIdle={() => setIdleOnly(false)} />
        : <WorkBoard solo people={roster.filter((r) => r.userId === store.me.id)} work={work} selected={store.me.id} onSelect={() => undefined} idleOnly={false} onClearIdle={() => undefined} />}

      <section className="dw-panel" aria-label="Candidates">
        <div className="dw-panel-head">
          <div><h2>{supervisor && <Users size={16} aria-hidden="true" />} {title}</h2>
            <p className="dw-summary" aria-live="polite">{summary}</p></div>
          {supervisor && <Select value={staffFilter ?? 'all'} onChange={(e) => setStaff(e.target.value)} className="tk-select" aria-label="Filter by recruiter">
            <option value="all">All recruiters</option>
            {roster.map((s) => <option key={s.userId} value={s.userId}>{s.name}{!s.active ? ' (inactive)' : ''}</option>)}
          </Select>}
        </div>

        {rows.length === 0 ? (
          <EmptyState title={mine ? 'No candidates assigned' : person ? `${person} has no candidates` : 'No candidates assigned'}
            description={mine ? 'Candidates assigned to you appear here.' : person ? 'Pick another recruiter, or show everyone.' : 'Candidates appear here once they are assigned to a recruiter.'}
            action={person ? <Button onClick={() => setStaff('all')}>Show all recruiters</Button> : undefined} />
        ) : (
          <TaskTable rows={rows} zone={zone} supervisor={supervisor} staffName={(id) => store.user(id)?.name}
            canWork={(row) => !!row.taskId && (store.can('work_any_candidate') || row.ownerId === store.me.id)}
            onOpen={open} onProfile={(row) => navigate(`/candidates/${row.candidateId}`)}
            onRemark={async (row, text) => { if (row.taskId) { await store.addRemark(row.taskId, text); await refreshAll() } }} />
        )}
      </section>
      <StageModal applicationId={openApp?.id ?? null} open={!!openApp} focusStage={openApp?.stage} onClose={() => { setOpenApp(null); void refreshAll() }} />
    </div>
  )
}
