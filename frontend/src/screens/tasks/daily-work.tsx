import { useMemo, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { CheckCircle2, ListChecks, RefreshCw, TriangleAlert, Users } from 'lucide-react'
import { useStore } from '@/shared/state/crm-store'
import { Button, Select } from '@/shared/ui/primitives'
import { EmptyState, PageHeader } from '@/shared/ui/display'
import { StageModal } from '@/features/workflow/components/stage-modal'
import { applicationFormHref } from '@/features/application-form/form-model'
import { splitItems, summaryLine } from '@/features/daily-work/daily-work-model'
import { useDailyWork } from '@/features/daily-work/use-daily-work'
import { DailyTable, type DailyGroup } from './daily-table'
import { StaffBoard } from './daily-staff-board'
import { TasksTabs } from './tasks-tabs'
import '@/styles/daily-work.css'

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
      { key: 'to-work', title: 'To work', hint: 'Candidates with prescreening waiting come first, then the newest assigned. Work them in the order that suits you.', icon: <ListChecks size={15} aria-hidden="true" />, items: toWork },
      { key: 'done-today', title: 'Done today', hint: 'Prescreening was finished or the call was answered today. Finished candidates are in Follow-up from tomorrow.', icon: <CheckCircle2 size={15} aria-hidden="true" />, items: done, tone: 'done' as const },
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
  const person = supervisor && staffFilter && staffFilter !== 'all' ? store.user(staffFilter)?.name ?? 'Recruiter' : ''
  const title = mine ? "Today's candidates" : person ? `${person}'s candidates today` : "Everyone's candidates today"

  return (
    <div className="tk dw">
      <PageHeader
        title="Tasks"
        description={mine
          ? "Today's task: the candidates assigned to you who still need prescreening. Finish prescreening and the candidate moves to Follow-up. Work them in whatever order and at whatever time suits you."
          : "Today's task for every recruiter: candidates who still need prescreening, with how many each has left to work and how many were finished today."}
        actions={<>
          <Button onClick={onOpenAll}><ListChecks size={15} aria-hidden="true" />All 48-hour tasks</Button>
          <Button onClick={() => void refresh()}><RefreshCw size={15} aria-hidden="true" />Refresh</Button>
        </>}
      />

      <TasksTabs active="daily" staff={staffFilter} />

      <div className="dw-alerts">
        {data.truncated && <p className="is-warn" role="status"><TriangleAlert size={16} aria-hidden="true" />
          Only the first {data.items.length} of {summary.total} candidates are listed. {supervisor ? 'Pick a recruiter to see all of theirs.' : 'The counts above are complete.'}</p>}
        {error && <p className="is-warn" role="status"><TriangleAlert size={16} aria-hidden="true" />The list could not refresh just now ({error}). Showing the last version.</p>}
      </div>

      {supervisor && <StaffBoard staff={data.staff ?? []} selected={staffFilter ?? 'all'} onSelect={setStaff} />}

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
          <EmptyState title={mine ? 'No candidates to work' : 'No candidates for this selection'}
            description={mine
              ? 'Candidates assigned to you appear here until prescreening is finished. Nothing needs prescreening right now.'
              : 'Candidates appear here once they are assigned to a recruiter and still need prescreening.'} />
        ) : (
          <DailyTable groups={groups} zone={zone} supervisor={supervisor} staffName={(id) => store.user(id)?.name}
            canWork={(item) => !!item.taskId && (store.can('work_any_candidate') || item.ownerId === store.me.id)}
            onOpen={(item) => item.applicationId ? setOpenApp({ id: item.applicationId, stage: item.stage ?? undefined }) : navigate(applicationFormHref(item.candidateId))}
            onProfile={(item) => navigate(`/candidates/${item.candidateId}`)}
            onRemark={async (item, text) => { if (item.taskId) { await store.addRemark(item.taskId, text); await refresh() } }} />
        )}
      </section>
      <StageModal applicationId={openApp?.id ?? null} open={!!openApp} focusStage={openApp?.stage} onClose={() => { setOpenApp(null); void refresh() }} />
    </div>
  )
}
