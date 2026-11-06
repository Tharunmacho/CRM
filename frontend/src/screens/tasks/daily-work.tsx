import { useEffect, useMemo, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { AlertTriangle, CheckCircle2, ClipboardList, Clock3, History, ListChecks, RefreshCw, SlidersHorizontal, Sunrise, Sunset, Target, TriangleAlert } from 'lucide-react'
import { cn } from '@/shared/lib/cn'
import { api } from '@/shared/api/client'
import { useStore } from '@/shared/state/crm-store'
import { Button, Field, Input, Select } from '@/shared/ui/primitives'
import { Dialog } from '@/shared/ui/overlay'
import { EmptyState, PageHeader } from '@/shared/ui/display'
import { StageModal } from '@/features/workflow/components/stage-modal'
import {
  checkpointLabel, clockTime, fmtCount, quotaLine, teamTotalLine, goalPercent, goalState, msUntil, pacingMessage, percentDone, sessionProgress, spanLabel,
  type CheckpointSet, type GoalBlock, type GoalTable,
} from '@/features/daily-work/daily-work-model'
import { useDailyWork, useTicker } from '@/features/daily-work/use-daily-work'
import { DailyTable, type DailyGroup } from './daily-table'
import { StaffBoard } from './daily-staff-board'
import '@/styles/daily-work.css'

/** The recruiter's daily list: five candidates a day (3 morning, 2 evening), one an hour, with carry-over. */
export function DailyWork({ onOpenAll }: { onOpenAll: () => void }) {
  const store = useStore()
  const [params, setParams] = useSearchParams()
  const navigate = useNavigate()
  const supervisor = store.can('all_reports')
  const staffFilter = supervisor ? params.get('staff') || 'all' : undefined
  const { data, error, loading, skew, refresh } = useDailyWork({ staff: staffFilter })
  const now = useTicker(1000, !!data)
  const [openApp, setOpenApp] = useState<{ id: string; stage?: number } | null>(null)
  const [editingGoals, setEditingGoals] = useState(false)
  const zone = store.agencyTimezone

  const setStaff = (value: string) => setParams((previous) => {
    const next = new URLSearchParams(previous)
    if (value && value !== 'all') next.set('staff', value); else next.delete('staff')
    return next
  })

  const items = data?.items ?? []
  const today = data?.agencyDate ?? ''
  const groups: DailyGroup[] = useMemo(() => {
    const postponed = items.filter((i) => i.postponed)
    const cleared = items.filter((i) => i.status === 'done' && i.workDate < today)
    const own = items.filter((i) => i.workDate === today)
    return [
      { key: 'postponed', title: 'Postponed', hint: 'Not finished on an earlier day. Do these first.', icon: <History size={15} aria-hidden="true" />, items: postponed, tone: 'late' as const },
      { key: 'morning', title: 'Morning', hint: 'One candidate each hour from the start of the shift.', icon: <Sunrise size={15} aria-hidden="true" />, items: own.filter((i) => i.session === 'morning') },
      { key: 'evening', title: 'Evening', hint: 'After the break, one candidate each hour.', icon: <Sunset size={15} aria-hidden="true" />, items: own.filter((i) => i.session === 'evening') },
      { key: 'cleared', title: 'Postponed work finished today', hint: 'Counts towards today.', icon: <CheckCircle2 size={15} aria-hidden="true" />, items: cleared },
    ].filter((g) => g.items.length > 0)
  }, [items, today])

  if (loading && !data) return <div className="tk dw"><PageHeader title="Tasks" description="Your daily candidates." />
    <section className="dw-panel dw-state" aria-busy="true"><p role="status">Loading your daily tasks…</p></section></div>
  if (error && !data) return <div className="tk dw"><PageHeader title="Tasks" description="Your daily candidates." />
    <section className="dw-panel dw-state"><TriangleAlert size={22} aria-hidden="true" />
      <p role="alert" className="dw-state-title">{error}</p><p>We could not load today's list. Check your connection and try again.</p>
      <Button variant="primary" onClick={() => void refresh()}>Try again</Button></section></div>
  if (!data) return null

  const summary = data.summary
  const mine = !supervisor
  const showStaffColumn = supervisor
  const lateNext = summary.nextDueAt ? msUntil(summary.nextDueAt, now, skew) : null
  const team = supervisor && data.goalsMode === 'average'
  const personName = supervisor && staffFilter && staffFilter !== 'all' ? store.user(staffFilter)?.name ?? 'Staff' : ''
  const quota = quotaLine({ daily: data.dailyQuota, morning: data.morningQuota, evening: data.eveningQuota }, data.targetsVary)
  const nextItem = items.filter((i) => i.status === 'open').sort((a, b) => a.dueAt.localeCompare(b.dueAt))[0]

  return (
    <div className="tk dw">
      <PageHeader
        title="Tasks"
        description={mine
          ? `Your first job each day: finish today's ${data.dailyQuota} candidates (${data.morningQuota} in the morning, ${data.eveningQuota} in the evening, one an hour). Anything unfinished carries over to the next working day.`
          : 'Each recruiter gets a fixed daily list of candidates, split into morning and evening hours. See who is on track and who is falling behind.'}
        actions={<>
          <Button onClick={onOpenAll}><ListChecks size={15} aria-hidden="true" />All 48-hour tasks</Button>
          <Button onClick={() => void refresh()}><RefreshCw size={15} aria-hidden="true" />Refresh</Button>
        </>}
      />

      <div className="dw-stats">
        <section className="dw-stat dw-stat-main" aria-label="Today's progress">
          <header><span>{mine ? "Today's progress" : team ? 'Team progress' : `${personName}'s progress`}</span><ClipboardList size={18} aria-hidden="true" /></header>
          <strong className="tnum">{summary.done} <small>of {summary.total} done</small></strong>
          <span className="dw-bar" role="img" aria-label={`${percentDone(summary)} percent done`}><i style={{ width: `${percentDone(summary)}%` }} /></span>
          <p>{summary.total
            ? `${team ? `across ${data.staffCount} staff · ` : ''}${sessionProgress(summary)}`
            : data.offToday ? 'Day off: nothing allocated' : data.shortfall ? 'No candidates to work today' : 'Nothing allocated today'}</p>
        </section>
        <section className={cn('dw-stat', summary.overdue > 0 && 'is-late')} aria-label="Next deadline">
          <header><span>Next deadline</span><Clock3 size={18} aria-hidden="true" /></header>
          {summary.complete
            ? <><strong>All done</strong><p>Everything on your list is finished.</p></>
            : summary.overdue > 0
              ? <><strong className="tnum">{summary.overdue} overdue</strong><p>{summary.overdue === 1 ? 'A candidate is' : 'Candidates are'} past the hourly deadline.</p></>
              : lateNext !== null && nextItem
                ? <><strong className="tnum">{spanLabel(lateNext)}</strong><p>{nextItem.candidate} · due {clockTime(nextItem.dueAt, zone)}</p></>
                : <><strong>-</strong><p>No open candidates.</p></>}
        </section>
        <section className={cn('dw-stat', summary.postponed > 0 && 'is-warn')} aria-label="Postponed">
          <header><span>Postponed</span><History size={18} aria-hidden="true" /></header>
          <strong className="tnum">{summary.postponed}</strong>
          <p>{summary.postponed ? `Oldest is ${summary.oldestPostponedDays} ${summary.oldestPostponedDays === 1 ? 'day' : 'days'} late` : 'Nothing carried over'}</p>
        </section>
        <section className="dw-stat" aria-label="Quota">
          <header><span>Daily quota</span><Target size={18} aria-hidden="true" /></header>
          <strong className="tnum">{quota.value} <small>{team ? 'per staff' : mine ? 'a day' : 'for this person'}</small></strong>
          <p>{quota.detail}</p>
          {team && <p className="dw-quota-total">{teamTotalLine(data.teamGoals.candidates.target, data.staffCount)}</p>}
        </section>
      </div>

      <div className="dw-alerts">
        {summary.overdue > 0 && <p className="is-late" role="alert"><AlertTriangle size={16} aria-hidden="true" />
          {summary.overdue} {summary.overdue === 1 ? 'candidate is' : 'candidates are'} past the hourly deadline. {mine ? 'Finish the stage work now; your manager can see this.' : 'These staff members are behind their hourly slots.'}</p>}
        {summary.postponed > 0 && <p className="is-warn"><History size={16} aria-hidden="true" />
          {summary.postponed} {summary.postponed === 1 ? 'candidate was' : 'candidates were'} not finished on an earlier day and {summary.postponed === 1 ? 'is' : 'are'} on today's list on top of the new {data.dailyQuota}.</p>}
        {data.shortfall && !data.offToday && data.goalsMode === 'person' && <p className="is-warn" role="status"><AlertTriangle size={16} aria-hidden="true" />
          <span><b>Only {data.shortfall.allocated} {data.shortfall.allocated === 1 ? 'candidate' : 'candidates'} to work today</b> (the goal is {data.shortfall.target}). {data.shortfall.reason}</span></p>}
        {error && <p className="is-warn" role="status"><TriangleAlert size={16} aria-hidden="true" />The list could not refresh just now ({error}). Showing the last version.</p>}
      </div>

      <GoalsPanel goals={data.goals} teamGoals={data.teamGoals} checkpoints={data.checkpoints} average={team} staffCount={data.staffCount}
        targetsVary={data.targetsVary} personName={mine ? '' : personName} zone={zone} admin={store.me.role === 'admin'} onEdit={() => setEditingGoals(true)} />

      {supervisor && <StaffBoard now={now + skew} staff={data.staff ?? []} selected={staffFilter ?? 'all'} onSelect={setStaff} />}

      <section className="dw-panel" aria-label="Daily candidates">
        <div className="dw-panel-head">
          <div><h2>{mine ? "Today's candidates" : staffFilter && staffFilter !== 'all' ? `${store.user(staffFilter)?.name ?? 'Staff'}'s candidates` : "Everyone's candidates"}</h2>
            <p>Postponed first, then the hourly slots. Finish a stage to tick the candidate off; there is no manual checkbox.</p></div>
          {supervisor && <Select value={staffFilter ?? 'all'} onChange={(e) => setStaff(e.target.value)} className="tk-select" aria-label="Filter by staff">
            <option value="all">All staff</option>
            {(data.staff ?? []).map((s) => <option key={s.userId} value={s.userId}>{s.name}{!s.active ? ' (inactive)' : ''}</option>)}
          </Select>}
        </div>

        {items.length === 0 ? (
          <EmptyState title={data.offToday ? 'Day off' : data.shortfall ? 'No candidates to work today' : mine ? 'No candidates to work on today' : 'No daily candidates for this selection'}
            description={data.offToday ? 'No candidates are allocated on a day off or leave.' : data.shortfall ? data.shortfall.reason : mine
              ? 'Daily candidates are drawn automatically from candidates assigned to you who are matched to an order and waiting on a stage. None are waiting right now, or today is your day off.'
              : 'Candidates appear here once staff have candidates matched to orders and waiting on a stage.'} />
        ) : (
          <DailyTable groups={groups} now={now} skew={skew} zone={zone} supervisor={showStaffColumn} staffName={(id) => store.user(id)?.name}
            canWork={(item) => !!item.taskId && (store.can('work_any_candidate') || item.ownerId === store.me.id)}
            onOpen={(item) => item.applicationId ? setOpenApp({ id: item.applicationId, stage: item.stage ?? undefined }) : navigate(`/candidates/${item.candidateId}`)}
            onProfile={(item) => navigate(`/candidates/${item.candidateId}`)}
            onRemark={async (item, text) => { if (item.taskId) { await store.addRemark(item.taskId, text); await refresh() } }} />
        )}
      </section>
      {editingGoals && <GoalsDialog onClose={() => setEditingGoals(false)} onSaved={() => void refresh()} />}
      <StageModal applicationId={openApp?.id ?? null} open={!!openApp} focusStage={openApp?.stage} onClose={() => { setOpenApp(null); void refresh() }} />
    </div>
  )
}

/** Today's goals, per recruiter: one person's own, or the average per recruiter with the team total shown beside it. */
function GoalsPanel({ goals, teamGoals, checkpoints, average, staffCount, targetsVary, personName, zone, admin, onEdit }: {
  goals: GoalBlock; teamGoals: GoalBlock; checkpoints: CheckpointSet | null; average: boolean; staffCount: number; targetsVary: boolean
  personName: string; zone?: string; admin: boolean; onEdit: () => void
}) {
  const c = goals.candidates
  const pacing = pacingMessage(checkpoints)
  const bars = [
    { key: 'forwarded', label: 'Forwarded to client', hint: 'CVs sent to the client today', actual: goals.forwardedToClient.actual, target: goals.forwardedToClient.target },
    { key: 'moves', label: 'Stage moves', hint: 'Stages completed today', actual: goals.stageMoves.actual, target: goals.stageMoves.target },
  ]
  const title = average ? 'Goals per staff member' : personName ? `${personName}'s goals today` : "Today's goals"
  const sub = average
    ? `Each recruiter's target against the average done per person${targetsVary ? ' (targets differ by person, so these are approximate)' : ''}. Select a staff member below to see their own hourly checkpoints.`
    : personName ? 'Their targets for today. They update as stages are finished.' : 'Your targets for today. They update as you finish stages.'
  const t = teamGoals
  return (
    <section className="dw-panel dw-goals" aria-label="Today's goals">
      <div className="dw-panel-head">
        <div><h2><Target size={16} aria-hidden="true" /> {title}</h2><p>{sub}</p></div>
        {admin && <Button size="sm" onClick={onEdit}><SlidersHorizontal size={14} aria-hidden="true" />Set goals</Button>}
      </div>
      <div className="dw-goal-grid">
        <div className={cn('dw-goal dw-goal-main', goalState(c.done, c.target) === 'done' && 'is-done')}>
          <header><b>Candidates</b><span className="tnum">{fmtCount(c.done)} / {c.target}</span></header>
          <div className="dw-goal-split" role="img" aria-label={`Morning ${fmtCount(c.morningDone)} of ${c.morning}, evening ${fmtCount(c.eveningDone)} of ${c.evening}`}>
            <div style={{ flexGrow: Math.max(c.morning, 0.0001) }}><i style={{ width: `${goalPercent(c.morningDone, c.morning)}%` }} /><span>Morning {fmtCount(c.morningDone)}/{c.morning}</span></div>
            <div style={{ flexGrow: Math.max(c.evening, 0.0001) }}><i style={{ width: `${goalPercent(c.eveningDone, c.evening)}%` }} /><span>Evening {fmtCount(c.eveningDone)}/{c.evening}</span></div>
          </div>
          {checkpoints && checkpoints.points.length > 0 && (
            <ol className="dw-checkpoints" aria-label="Hourly checkpoints">
              {checkpoints.points.map((p) => (
                <li key={p.slotNo} className={cn('dw-cp', `is-${p.state}`)} title={`${p.candidate}: ${checkpointLabel(p.state)}`}>
                  <span className="dw-cp-dot" aria-hidden="true">{p.state === 'met' || p.state === 'late' ? <CheckCircle2 size={14} /> : p.state === 'missed' ? <AlertTriangle size={14} /> : <Clock3 size={14} />}</span>
                  <b className="tnum">{clockTime(p.dueAt, zone)}</b>
                  <small>{checkpointLabel(p.state)}</small>
                </li>
              ))}
            </ol>
          )}
          {pacing && <p className={cn('dw-pacing', checkpoints && checkpoints.behindBy > 0 && 'is-late')}>{pacing}</p>}
        </div>
        {bars.map((b) => (
          <div key={b.key} className={cn('dw-goal', goalState(b.actual, b.target) === 'done' && 'is-done')}>
            <header><b>{b.label}</b><span className="tnum">{b.target > 0 ? `${fmtCount(b.actual)} / ${b.target}` : fmtCount(b.actual)}</span></header>
            <span className="dw-bar" role="img" aria-label={b.target > 0 ? `${goalPercent(b.actual, b.target)} percent of the goal` : 'No goal set'}><i style={{ width: `${goalPercent(b.actual, b.target)}%` }} /></span>
            <p>{b.target > 0 ? `${b.hint}${average ? ' (average per staff)' : ''}` : 'No goal set'}</p>
          </div>
        ))}
      </div>
      {average && (
        <p className="dw-team-total" aria-label="Team total">
          <b>Team total ({staffCount} {staffCount === 1 ? 'staff member' : 'staff'})</b>
          <span className="tnum">Candidates {t.candidates.done} / {t.candidates.target}</span>
          <span className="tnum">Forwarded {t.forwardedToClient.actual} / {t.forwardedToClient.target}</span>
          <span className="tnum">Stage moves {t.stageMoves.actual} / {t.stageMoves.target}</span>
        </p>
      )}
    </section>
  )
}

/** Admin: the agency default goals, and a person's own goals that replace them. All stored in the database. */
function GoalsDialog({ onClose, onSaved }: { onClose: () => void; onSaved: () => void }) {
  const store = useStore()
  const [table, setTable] = useState<GoalTable | null>(null)
  const [scope, setScope] = useState('default')
  const [form, setForm] = useState({ candidates: '5', morning: '3', forwardedToClient: '2', stageMoves: '5' })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const load = async () => {
    try { setTable(await api.get<GoalTable>('/daily-goals')) } catch (e) { setError(e instanceof Error ? e.message : 'Goals could not be loaded.') }
  }
  useEffect(() => { void load() }, [])
  const current = !table ? null : scope === 'default' ? { goals: table.default, overrides: [] as string[] } : table.staff.find((s) => s.userId === scope) ?? null
  useEffect(() => {
    if (!current) return
    const g = current.goals
    setForm({ candidates: String(g.candidates), morning: String(g.morning), forwardedToClient: String(g.forwardedToClient), stageMoves: String(g.stageMoves) })
  }, [scope, table]) // eslint-disable-line react-hooks/exhaustive-deps
  const n = (v: string) => (v.trim() === '' ? NaN : Number(v))
  const invalid = [n(form.candidates), n(form.morning), n(form.forwardedToClient), n(form.stageMoves)].some((v) => !Number.isInteger(v) || v < 0)
    || n(form.candidates) < 1 || n(form.morning) > n(form.candidates)
  const save = async () => {
    setBusy(true); setError('')
    try {
      const body = { candidates: n(form.candidates), morning: n(form.morning), forwardedToClient: n(form.forwardedToClient), stageMoves: n(form.stageMoves) }
      await api.put(scope === 'default' ? '/daily-goals/default' : `/daily-goals/staff/${scope}`, body)
      store.notify(scope === 'default' ? 'Agency goals saved.' : 'Goals saved for this person.')
      await load(); onSaved()
    } catch (e) { setError(e instanceof Error ? e.message : 'The goals could not be saved.') }
    finally { setBusy(false) }
  }
  const reset = async () => {
    setBusy(true); setError('')
    try { await api.del(`/daily-goals/staff/${scope}`); store.notify('This person now follows the agency goals.'); await load(); onSaved() }
    catch (e) { setError(e instanceof Error ? e.message : 'The goals could not be reset.') }
    finally { setBusy(false) }
  }
  const field = (key: keyof typeof form, label: string, hint?: string) => (
    <label className="dw-goal-field">{label}<Input type="number" min={0} max={key === 'candidates' ? 50 : 200} value={form[key]} onChange={(e) => setForm({ ...form, [key]: e.target.value })} />{hint && <small>{hint}</small>}</label>
  )
  const evening = n(form.candidates) - n(form.morning)
  const own = current && 'userId' in current ? current.overrides.length > 0 : false
  return (
    <Dialog open onClose={() => { if (!busy) onClose() }} title="Daily goals" description="Set how many candidates and stage moves each person is expected to finish every working day. A person's own goals replace the agency goals."
      footer={<>
        {scope !== 'default' && own && <Button variant="ghost" disabled={busy} onClick={() => void reset()}>Use agency goals</Button>}
        <Button variant="ghost" disabled={busy} onClick={onClose}>Close</Button>
        <Button variant="primary" disabled={busy || invalid || !current} onClick={() => void save()}>{busy ? 'Saving…' : scope === 'default' ? 'Save agency goals' : 'Save for this person'}</Button>
      </>}>
      {error && <p role="alert" className="dw-remark-error">{error}</p>}
      <Field label="Goals for">
        <Select value={scope} onChange={(e) => setScope(e.target.value)} aria-label="Whose goals">
          <option value="default">Agency default (everyone without their own)</option>
          {(table?.staff ?? []).map((s) => <option key={s.userId} value={s.userId}>{s.name}{s.overrides.length ? ' (own goals)' : ''}</option>)}
        </Select>
      </Field>
      <div className="dw-goal-form">
        {field('candidates', 'Candidates per day', 'Handed out automatically')}
        {field('morning', 'of which morning', Number.isFinite(evening) && evening >= 0 ? `${evening} in the evening` : 'Cannot exceed the daily candidates')}
        {field('forwardedToClient', 'Forwarded to client', '0 means no goal')}
        {field('stageMoves', 'Stage moves', '0 means no goal')}
      </div>
    </Dialog>
  )
}

