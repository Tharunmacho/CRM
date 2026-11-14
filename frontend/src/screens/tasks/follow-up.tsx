import { useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { Clock, ListChecks, RefreshCw, TriangleAlert, Users } from 'lucide-react'
import { useStore } from '@/shared/state/crm-store'
import { Button, Select } from '@/shared/ui/primitives'
import { EmptyState, PageHeader } from '@/shared/ui/display'
import { StageModal } from '@/features/workflow/components/stage-modal'
import { applicationFormHref } from '@/features/application-form/form-model'
import { listTitle, staleLabel } from '@/features/daily-work/daily-work-model'
import { useFollowUp, useTicker } from '@/features/daily-work/use-daily-work'
import { FollowUpTable } from './follow-up-table'
import { FollowUpBoard } from './daily-staff-board'
import { StatStrip, type StatTileData } from './daily-stat-strip'
import { TasksTabs } from './tasks-tabs'
import '@/styles/daily-work.css'
import '@/styles/daily-work-board.css'

/**
 * Follow-up: the candidates a recruiter owns whose application is active at stage 2 to 11 (selected and still moving), the stalest
 * first. A candidate leaves when the application closes (any negative exit) or is deployed (then it is on Travelled). Admin and
 * manager see everyone's with a Recruiter column and an All recruiters filter.
 */
export function FollowUp({ onOpenAll }: { onOpenAll: () => void }) {
  const store = useStore()
  const [params, setParams] = useSearchParams()
  const navigate = useNavigate()
  const supervisor = store.can('all_reports')
  const staffFilter = supervisor ? params.get('staff') || 'all' : undefined
  const { data, error, loading, refresh } = useFollowUp({ staff: staffFilter })
  const now = useTicker(60_000, !!data)
  const [openApp, setOpenApp] = useState<{ id: string; stage?: number } | null>(null)

  const setStaff = (value: string) => setParams((previous) => {
    const next = new URLSearchParams(previous)
    if (value && value !== 'all') next.set('staff', value); else next.delete('staff')
    return next
  })

  if (loading && !data) return <div className="tk dw"><PageHeader title="Tasks" description="Your follow-up candidates." />
    <section className="dw-panel dw-state" aria-busy="true"><p role="status">Loading your follow-up candidates…</p></section></div>
  if (error && !data) return <div className="tk dw"><PageHeader title="Tasks" description="Your follow-up candidates." />
    <section className="dw-panel dw-state"><TriangleAlert size={22} aria-hidden="true" />
      <p role="alert" className="dw-state-title">{error}</p><p>We could not load the list. Check your connection and try again.</p>
      <Button variant="primary" onClick={() => void refresh()}>Try again</Button></section></div>
  if (!data) return null

  const roster = data.staff ?? []
  const person = supervisor && staffFilter && staffFilter !== 'all' ? roster.find((s) => s.userId === staffFilter)?.name ?? store.user(staffFilter)?.name ?? 'Recruiter' : ''
  const title = !supervisor ? 'Follow-up' : listTitle(person, 'follow-up candidates')
  const total = data.summary.total
  // The strip is the whole team (the per-recruiter list covers everyone); the stalest update is of the list shown, which is sorted stalest first.
  const teamTotal = roster.reduce((sum, s) => sum + s.summary.total, 0)
  const holding = roster.filter((s) => s.summary.total > 0).length
  const stalest = data.items[0]
  const tiles: StatTileData[] = [
    { key: 'total', label: 'Follow-up candidates', value: teamTotal, icon: ListChecks, detail: 'Selected and still moving' },
    { key: 'holding', label: 'Recruiters with follow-ups', value: holding, unit: `of ${roster.length}`, icon: Users, detail: holding === roster.length ? 'Everyone has some' : `${roster.length - holding} with none` },
    { key: 'stalest', label: 'Longest without an update', value: stalest ? staleLabel(stalest.lastUpdateAt, now) : '–', icon: Clock,
      tone: stalest && now - new Date(stalest.lastUpdateAt).getTime() >= 3 * 86_400_000 ? 'amber' : 'blue',
      detail: stalest ? `${stalest.candidate}${store.user(stalest.ownerId)?.name ? ` · ${store.user(stalest.ownerId)?.name}` : ''}` : person ? `${person}'s list` : 'Nothing to follow up' },
  ]

  return (
    <div className="tk dw dwb">
      <PageHeader
        title="Tasks"
        description={supervisor
          ? "Every recruiter's selected candidates, oldest update first."
          : 'Your selected candidates, oldest update first.'}
        actions={<>
          <Button onClick={onOpenAll}><ListChecks size={15} aria-hidden="true" />All 48-hour tasks</Button>
          <Button onClick={() => void refresh()}><RefreshCw size={15} aria-hidden="true" />Refresh</Button>
        </>}
      />
      <TasksTabs active="follow-up" staff={staffFilter} />

      <div className="dw-alerts">
        {data.truncated && <p className="is-warn" role="status"><TriangleAlert size={16} aria-hidden="true" />
          Only the first {data.items.length} of {total} candidates are listed. {supervisor ? 'Pick a recruiter to see all of theirs.' : ''}</p>}
        {error && <p className="is-warn" role="status"><TriangleAlert size={16} aria-hidden="true" />The list could not refresh just now ({error}). Showing the last version.</p>}
      </div>

      {supervisor && <StatStrip tiles={tiles} label="Follow-up at a glance" />}
      {supervisor && <FollowUpBoard staff={roster} selected={staffFilter ?? 'all'} onSelect={setStaff} />}

      <section className="dw-panel" aria-label="Follow-up">
        <div className="dw-panel-head">
          <div><h2>{supervisor && <Users size={16} aria-hidden="true" />} {title}</h2>
            <p className="dw-summary" aria-live="polite">{total} {total === 1 ? 'candidate' : 'candidates'}{supervisor && !person ? ` · ${data.staffCount} ${data.staffCount === 1 ? 'recruiter' : 'recruiters'}` : ''}</p></div>
          {supervisor && <Select value={staffFilter ?? 'all'} onChange={(e) => setStaff(e.target.value)} className="tk-select" aria-label="Filter by recruiter">
            <option value="all">All recruiters</option>
            {(data.staff ?? []).map((s) => <option key={s.userId} value={s.userId}>{s.name} ({s.summary.total}){!s.active ? ' (inactive)' : ''}</option>)}
          </Select>}
        </div>

        {data.items.length === 0 ? (
          <EmptyState title={supervisor ? 'No follow-up candidates for this selection' : 'No candidates to follow up'}
            description="A candidate shows here once prescreening is finished and stays while the application is still going forward. Closed applications leave, and deployed candidates are on Travelled." />
        ) : (
          <FollowUpTable items={data.items} now={now} supervisor={supervisor} staffName={(id) => store.user(id)?.name}
            canWork={(item) => !!item.taskId && (store.can('work_any_candidate') || item.ownerId === store.me.id)}
            onOpen={(item) => navigate(applicationFormHref(item.candidateId))}
            onStage={(item) => setOpenApp({ id: item.applicationId, stage: item.stage })}
            onProfile={(item) => navigate(`/candidates/${item.candidateId}`)}
            onRemark={async (item, text) => { if (item.taskId) { await store.addRemark(item.taskId, text); await refresh() } }} />
        )}
      </section>
      <StageModal applicationId={openApp?.id ?? null} open={!!openApp} focusStage={openApp?.stage} onClose={() => { setOpenApp(null); void refresh() }} />
    </div>
  )
}
