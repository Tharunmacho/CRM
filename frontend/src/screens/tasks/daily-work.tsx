import { Fragment, useEffect, useMemo, useState, type FormEvent, type ReactNode } from 'react'
import { useSearchParams } from 'react-router-dom'
import { AlertTriangle, ArrowRight, CheckCircle2, ClipboardList, Clock3, History, ListChecks, RefreshCw, Save, SlidersHorizontal, Sunrise, Sunset, Target, TriangleAlert, Users } from 'lucide-react'
import { cn } from '@/shared/lib/cn'
import { api } from '@/shared/api/client'
import { useStore } from '@/shared/state/crm-store'
import { Button, Field, Input, Select, Textarea } from '@/shared/ui/primitives'
import { Dialog } from '@/shared/ui/overlay'
import { EmptyState, PageHeader } from '@/shared/ui/display'
import { CountryFlag } from '@/shared/ui/country-flag'
import { RecordIdentity } from '@/shared/ui/workspace'
import { StageModal } from '@/features/workflow/components/stage-modal'
import {
  checkpointLabel, clockTime, countdownLabel, dueTone, goalPercent, goalState, msUntil, pacingMessage, percentDone, postponedLabel,
  referralLabel, sessionLabel, sessionProgress, spanLabel, staffNeedingAttention,
  type CheckpointSet, type DailyItem, type DailyStaffSummary, type DailySummary, type GoalBlock, type GoalTable,
} from '@/features/daily-work/daily-work-model'
import { useDailyWork, useTicker } from '@/features/daily-work/use-daily-work'
import { StageTrack } from './task-identity'
import '@/styles/daily-work.css'

interface Group { key: string; title: string; hint: string; icon: ReactNode; items: DailyItem[]; tone?: 'late' }

/** The recruiter's daily list: five candidates a day (3 morning, 2 evening), one an hour, with carry-over. */
export function DailyWork({ onOpenAll }: { onOpenAll: () => void }) {
  const store = useStore()
  const [params, setParams] = useSearchParams()
  const supervisor = store.can('all_reports')
  const staffFilter = supervisor ? params.get('staff') || 'all' : undefined
  const { data, error, loading, skew, refresh } = useDailyWork({ staff: staffFilter })
  const now = useTicker(1000, !!data)
  const [openApp, setOpenApp] = useState<{ id: string; stage: number } | null>(null)
  const [editingGoals, setEditingGoals] = useState(false)
  const zone = store.agencyTimezone

  const setStaff = (value: string) => setParams((previous) => {
    const next = new URLSearchParams(previous)
    if (value && value !== 'all') next.set('staff', value); else next.delete('staff')
    return next
  })

  const items = data?.items ?? []
  const today = data?.agencyDate ?? ''
  const groups: Group[] = useMemo(() => {
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
  const nextItem = items.filter((i) => i.status === 'open').sort((a, b) => a.dueAt.localeCompare(b.dueAt))[0]
  const columns = ['S.No', 'Client', 'Country', 'Candidate', 'Position', 'Referral', 'Stage', 'Remarks', 'Deadline'] as const
  // Column shares (percent): the table fits its panel instead of scrolling; the staff column joins for supervisors.
  const widths = showStaffColumn ? [4, 10, 7, 13, 8, 8, 8, 12, 15, 15] : [4, 12, 9, 15, 9, 9, 14, 16, 12]
  let serial = 0

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
          <header><span>{mine ? "Today's progress" : 'Progress for the selected staff'}</span><ClipboardList size={18} aria-hidden="true" /></header>
          <strong className="tnum">{summary.done} <small>of {summary.total} done</small></strong>
          <span className="dw-bar" role="img" aria-label={`${percentDone(summary)} percent done`}><i style={{ width: `${percentDone(summary)}%` }} /></span>
          <p>{summary.total ? sessionProgress(summary) : 'Nothing allocated today'}</p>
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
          <strong className="tnum">{data.dailyQuota}</strong>
          <p>{data.morningQuota} morning · {data.eveningQuota} evening{supervisor && staffFilter === 'all' ? ', all staff together' : ''}</p>
        </section>
      </div>

      <div className="dw-alerts">
        {summary.overdue > 0 && <p className="is-late" role="alert"><AlertTriangle size={16} aria-hidden="true" />
          {summary.overdue} {summary.overdue === 1 ? 'candidate is' : 'candidates are'} past the hourly deadline. {mine ? 'Finish the stage work now; your manager can see this.' : 'These staff members are behind their hourly slots.'}</p>}
        {summary.postponed > 0 && <p className="is-warn"><History size={16} aria-hidden="true" />
          {summary.postponed} {summary.postponed === 1 ? 'candidate was' : 'candidates were'} not finished on an earlier day and {summary.postponed === 1 ? 'is' : 'are'} on today's list on top of the new {data.dailyQuota}.</p>}
        {error && <p className="is-warn" role="status"><TriangleAlert size={16} aria-hidden="true" />The list could not refresh just now ({error}). Showing the last version.</p>}
      </div>

      <GoalsPanel goals={data.goals} checkpoints={data.checkpoints} team={supervisor && (staffFilter ?? 'all') === 'all'} zone={zone}
        admin={store.me.role === 'admin'} onEdit={() => setEditingGoals(true)} />

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
          <EmptyState title={mine ? 'No candidates to work on today' : 'No daily candidates for this selection'}
            description={mine
              ? 'Daily candidates are drawn automatically from candidates assigned to you who are matched to an order and waiting on a stage. None are waiting right now, or today is your day off.'
              : 'Candidates appear here once staff have candidates matched to orders and waiting on a stage.'} />
        ) : (
          <div className="tk-table-wrap">
            <table className="dw-table" aria-label="Daily candidates">
              <colgroup>{widths.map((w, i) => <col key={i} style={{ width: `${w}%` }} />)}</colgroup>
              <thead><tr>
                {columns.map((c, i) => <Fragment key={c}>
                  {i === 6 && showStaffColumn && <th scope="col">Staff</th>}
                  <th scope="col" className={cn(c === 'S.No' && 'is-num')}>{c}</th>
                </Fragment>)}
              </tr></thead>
              <tbody>
                {groups.map((g) => <Fragment key={g.key}>
                  <tr className={cn('dw-group', g.tone === 'late' && 'is-late')}>
                    <th colSpan={columns.length + (showStaffColumn ? 1 : 0)} scope="colgroup">
                      <span className="dw-group-title">{g.icon}{g.title}<b className="tnum">{g.items.filter((i) => i.status === 'open').length} open</b></span>
                      <small>{g.hint}</small>
                    </th>
                  </tr>
                  {g.items.map((item) => {
                    serial += 1
                    return <ItemRow key={item.id} item={item} serial={serial} now={now} skew={skew} zone={zone} showStaff={showStaffColumn}
                      staffName={store.user(item.ownerId)?.name} canWork={store.can('work_any_candidate') || item.ownerId === store.me.id}
                      onOpen={() => setOpenApp({ id: item.applicationId, stage: item.stage })} onRemark={async (text) => { await store.addRemark(item.taskId, text); await refresh() }} />
                  })}
                </Fragment>)}
              </tbody>
            </table>
          </div>
        )}
      </section>
      {editingGoals && <GoalsDialog onClose={() => setEditingGoals(false)} onSaved={() => void refresh()} />}
      <StageModal applicationId={openApp?.id ?? null} open={!!openApp} focusStage={openApp?.stage} onClose={() => { setOpenApp(null); void refresh() }} />
    </div>
  )
}

function ItemRow({ item, serial, now, skew, zone, showStaff, staffName, canWork, onOpen, onRemark }: {
  item: DailyItem; serial: number; now: number; skew: number; zone?: string; showStaff: boolean; staffName?: string
  canWork: boolean; onOpen: () => void; onRemark: (text: string) => Promise<void>
}) {
  const tone = dueTone(item, now, skew)
  const done = item.status === 'done'
  const left = msUntil(item.dueAt, now, skew)
  return (
    <tr className={cn('dw-row', `is-${tone}`, item.postponed && 'is-postponed')}>
      <td className="is-num tnum" data-label="S.No">{serial}</td>
      <td data-label="Client"><span className="dw-cell-strong" title={item.clientContact ? `Contact: ${item.clientContact}` : undefined}>{item.client}</span><small className="dw-sub">{item.orderRef}</small></td>
      <td data-label="Country"><span className="dw-country"><CountryFlag country={item.country} className="tk-flag" globeClassName="tk-flag-globe" />{item.country || '-'}</span></td>
      <td data-label="Candidate"><RecordIdentity name={item.candidate} detail={item.candidateRef} /></td>
      <td data-label="Position">{item.position}</td>
      <td data-label="Referral"><span className={cn('dw-ref', item.referral.source === 'referral' && 'is-referral')}>{referralLabel(item.referral)}</span></td>
      {showStaff && <td data-label="Staff">{staffName ?? '-'}</td>}
      <td data-label="Stage"><StageTrack no={item.stage} /></td>
      <td data-label="Remarks"><RemarkCell item={item} canWork={canWork && !done} onSave={onRemark} /></td>
      <td data-label="Deadline" className="dw-last">
        <div className={cn('dw-slot', `is-${tone}`)}>
          <span className="dw-slot-label">{item.postponed ? postponedLabel(item.daysLate) : `${sessionLabel(item.session)} ${item.slotNo}`}</span>
          {done
            ? <b><CheckCircle2 size={14} aria-hidden="true" />Done {item.completedAt ? clockTime(item.completedAt, zone) : ''}</b>
            : <b className="tnum" aria-live="off">{countdownLabel(left)}</b>}
          {!done && <small>{item.postponed ? 'was due ' : 'due '}{clockTime(item.dueAt, zone)}</small>}
        </div>
        <Button size="sm" variant={done ? 'ghost' : 'primary'} onClick={onOpen} aria-label={`${done ? 'View' : 'Complete'} stage for ${item.candidate}`}>
          {done ? 'View' : 'Complete'}<ArrowRight size={14} aria-hidden="true" />
        </Button>
      </td>
    </tr>
  )
}

/** Latest remark plus a one-line box to add today's update (the same remark the 48-hour list uses). */
function RemarkCell({ item, canWork, onSave }: { item: DailyItem; canWork: boolean; onSave: (text: string) => Promise<void> }) {
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const submit = async (event: FormEvent) => {
    event.preventDefault()
    const value = text.trim()
    if (!value || busy) return
    setBusy(true); setError('')
    try { await onSave(value); setText('') }
    catch (e) { setError(e instanceof Error ? e.message : 'The remark could not be saved.') }
    finally { setBusy(false) }
  }
  return (
    <div className="dw-remark">
      {item.remark && <p className="dw-remark-last" title={`${item.remark.by} · ${new Date(item.remark.at).toLocaleString()}`}>{item.remark.body}</p>}
      {canWork && <form onSubmit={submit}>
        <Textarea rows={1} value={text} onChange={(e) => setText(e.target.value)} placeholder={item.remark ? 'Add update' : 'Add remark'} maxLength={10000} disabled={busy}
          aria-label={`Remark for ${item.candidate}`} onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); void submit(e as unknown as FormEvent) } }} />
        <button type="submit" className="dw-remark-save" disabled={busy || !text.trim()} aria-label="Save remark" title="Save remark"><Save size={14} aria-hidden="true" /></button>
      </form>}
      {!item.remark && !canWork && <span className="tk-muted">-</span>}
      {error && <small className="dw-remark-error" role="alert">{error}</small>}
    </div>
  )
}

/** Supervisor view: every recruiter's progress, worst first; a row filters the table below. */
function StaffBoard({ now, staff, selected, onSelect }: { now: number; staff: DailyStaffSummary[]; selected: string; onSelect: (id: string) => void }) {
  const sorted = useMemo(() => staffNeedingAttention(staff), [staff])
  return (
    <section className="dw-panel dw-board" aria-label="Staff progress">
      <div className="dw-panel-head">
        <div><h2><Users size={16} aria-hidden="true" /> Staff progress today</h2><p>Staff who are furthest behind are first. Select a row to see only their candidates.</p></div>
      </div>
      <div className="tk-table-wrap">
        <table className="dw-table dw-staff" aria-label="Progress by staff">
          <thead><tr><th scope="col">Staff</th><th scope="col">Candidates</th><th scope="col">Morning</th><th scope="col">Evening</th><th scope="col">Forwarded</th><th scope="col">Stage moves</th><th scope="col">Postponed</th><th scope="col">Overdue</th><th scope="col">Next deadline</th></tr></thead>
          <tbody>
            {sorted.length === 0 && <tr><td colSpan={9} className="tk-muted">No staff recruiters yet.</td></tr>}
            {sorted.map((s) => <StaffRow key={s.userId} now={now} s={s} active={selected === s.userId} onSelect={() => onSelect(selected === s.userId ? 'all' : s.userId)} />)}
          </tbody>
        </table>
      </div>
    </section>
  )
}

function StaffRow({ s, now, active, onSelect }: { now: number; s: DailyStaffSummary; active: boolean; onSelect: () => void }) {
  const store = useStore()
  const sum: DailySummary = s.summary
  return (
    <tr className={cn('dw-staff-row', active && 'is-active', sum.overdue > 0 && 'is-late')} tabIndex={0} onClick={onSelect}
      onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onSelect() } }} aria-pressed={active}>
      <td data-label="Staff"><b>{s.name}</b>{!s.active && <small className="dw-sub"> inactive</small>}</td>
      <td data-label="Done"><span className="tnum">{sum.done}/{sum.total}</span><span className="dw-bar dw-bar-sm" aria-hidden="true"><i style={{ width: `${percentDone(sum)}%` }} /></span></td>
      <td data-label="Morning" className="tnum">{sum.morning.done}/{sum.morning.total}</td>
      <td data-label="Evening" className="tnum">{sum.evening.done}/{sum.evening.total}</td>
      <td data-label="Forwarded" className="tnum">{s.goals ? `${s.goals.forwardedToClient.actual}/${s.goals.forwardedToClient.target}` : '-'}</td>
      <td data-label="Stage moves" className="tnum">{s.goals ? `${s.goals.stageMoves.actual}/${s.goals.stageMoves.target}` : '-'}</td>
      <td data-label="Postponed"><span className={cn('dw-chip', sum.postponed > 0 && 'is-warn')}>{sum.postponed}</span></td>
      <td data-label="Overdue"><span className={cn('dw-chip', sum.overdue > 0 && 'is-late')}>{sum.overdue}</span></td>
      <td data-label="Next deadline" className="tnum">{sum.total === 0 ? 'No work' : sum.complete ? 'All done' : sum.nextDueAt ? `${clockTime(sum.nextDueAt, store.agencyTimezone)} (${spanLabel(msUntil(sum.nextDueAt, now))})` : 'Overdue'}</td>
    </tr>
  )
}

/** Today's goals: the candidate target with its hourly checkpoints, plus the day's other targets as progress bars. */
function GoalsPanel({ goals, checkpoints, team, zone, admin, onEdit }: {
  goals: GoalBlock; checkpoints: CheckpointSet | null; team: boolean; zone?: string; admin: boolean; onEdit: () => void
}) {
  const c = goals.candidates
  const pacing = pacingMessage(checkpoints)
  const bars = [
    { key: 'forwarded', label: 'Forwarded to client', hint: 'CVs sent to the client today', actual: goals.forwardedToClient.actual, target: goals.forwardedToClient.target },
    { key: 'moves', label: 'Stage moves', hint: 'Stages completed today', actual: goals.stageMoves.actual, target: goals.stageMoves.target },
  ]
  return (
    <section className="dw-panel dw-goals" aria-label="Today's goals">
      <div className="dw-panel-head">
        <div><h2><Target size={16} aria-hidden="true" /> {team ? "Today's goals (all staff together)" : "Today's goals"}</h2>
          <p>{team ? 'Targets added up across the recruiters shown.' : 'Your targets for today. They update as you finish stages.'}</p></div>
        {admin && <Button size="sm" onClick={onEdit}><SlidersHorizontal size={14} aria-hidden="true" />Set goals</Button>}
      </div>
      <div className="dw-goal-grid">
        <div className={cn('dw-goal dw-goal-main', goalState(c.done, c.target) === 'done' && 'is-done')}>
          <header><b>Candidates</b><span className="tnum">{c.done} / {c.target}</span></header>
          <div className="dw-goal-split" role="img" aria-label={`Morning ${c.morningDone} of ${c.morning}, evening ${c.eveningDone} of ${c.evening}`}>
            <div style={{ flexGrow: Math.max(c.morning, 0.0001) }}><i style={{ width: `${goalPercent(c.morningDone, c.morning)}%` }} /><span>Morning {c.morningDone}/{c.morning}</span></div>
            <div style={{ flexGrow: Math.max(c.evening, 0.0001) }}><i style={{ width: `${goalPercent(c.eveningDone, c.evening)}%` }} /><span>Evening {c.eveningDone}/{c.evening}</span></div>
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
            <header><b>{b.label}</b><span className="tnum">{b.target > 0 ? `${b.actual} / ${b.target}` : b.actual}</span></header>
            <span className="dw-bar" role="img" aria-label={b.target > 0 ? `${goalPercent(b.actual, b.target)} percent of the goal` : 'No goal set'}><i style={{ width: `${goalPercent(b.actual, b.target)}%` }} /></span>
            <p>{b.target > 0 ? b.hint : 'No goal set'}</p>
          </div>
        ))}
      </div>
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

