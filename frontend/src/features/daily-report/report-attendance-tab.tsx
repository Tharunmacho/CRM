import { useCallback, useEffect, useMemo, useState } from 'react'
import { CalendarCheck, ChevronLeft, ChevronRight, Clock3, Download, Percent, RefreshCw, ShieldCheck, TriangleAlert, UserCheck, UserX, WalletCards } from 'lucide-react'
import { api } from '@/shared/api/client'
import { liveInterval } from '@/shared/state/live'
import { useStore } from '@/shared/state/crm-store'
import { agencyDay } from '@/shared/lib/task-time'
import { cn } from '@/shared/lib/cn'
import { initials } from '@/shared/lib/format'
import { roleLabel } from '@/shared/model/role-labels'
import { Button, IconButton, Select } from '@/shared/ui/primitives'
import { Sheet } from '@/shared/ui/overlay'
import { ScrollEdges } from '@/screens/tasks/daily-table'
import { StatStrip, type StatTileData } from '@/screens/tasks/daily-stat-strip'
import { clockTime, monthTitle, shiftMonth } from '../attendance/format'
import { attendanceLabels, type AttendanceRequest, type EmployeeMonth, type MonthlyAttendance } from '../attendance/policy-model'
import { reportPeople } from './daily-report-view'
import { minutesText } from './attendance-rows'
import { buildOverviewRows, dailyRecordRows, dailyRecordTotals, monthTiles, overviewCsv, overviewCsvName, percentText, type OverviewRow } from './attendance-month'
import { saveCsv } from './daily-report-api'
import { AttendancePanel } from './report-attendance'
import '@/styles/report-attendance.css'

const hueOf = (name: string) => [...name].reduce((total, character) => total + character.charCodeAt(0), 0) % 6
const DASH = <span className="dw-none" aria-label="not available">{'–'}</span>
const MONTH_RE = /^\d{4}-\d{2}$/

/**
 * Reports > Attendance: the live login / logout board for one day, then the monthly staff overview (one row per recruiter: attendance
 * percentage, working days, problem days, unpaid time) with the daily record of a person in a side drawer. Self-contained: it reads the
 * attendance screen's own endpoints and draws below the shell's page title and tab strip.
 */
export function AttendanceReportTab() {
  const store = useStore()
  const timeZone = store.agencyTimezone || 'Asia/Kolkata'
  const today = agencyDay(new Date(), timeZone)
  const team = store.me.role !== 'staff'
  const [month, setMonth] = useState(today.slice(0, 7))
  const [pick, setPick] = useState('')
  const staff = team ? pick : store.me.id
  const [data, setData] = useState<{ month: MonthlyAttendance; requests: AttendanceRequest[] } | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [reload, setReload] = useState(0)
  const [loadedAt, setLoadedAt] = useState<string | null>(null)
  const [opened, setOpened] = useState('')

  const people = useMemo(() => reportPeople(store.users).filter((user) => !user.deleted && (user.active || user.id === pick)).sort((a, b) => a.name.localeCompare(b.name)), [store.users, pick])
  const changeMonth = useCallback((value: string) => { if (MONTH_RE.test(value) && value >= '2000-01' && value <= '2100-12') setMonth(value) }, [])

  useEffect(() => {
    if (!MONTH_RE.test(month)) return undefined
    const controller = new AbortController()
    let running = false
    let again = false // a change arrived while a read was running: that read may predate it
    const load = async (initial = false) => {
      if (running) return
      running = true
      if (initial) { setData(null); setLoading(true); setError('') }
      const [year, monthNumber] = month.split('-')
      const params = new URLSearchParams({ year, month: String(Number(monthNumber)), ...(staff ? { staffId: staff } : {}) })
      try {
        const [monthly, requests] = await Promise.all([
          api.get<MonthlyAttendance>('/attendance/month?' + params, { signal: controller.signal }),
          // The requests are a bonus: without them the chips and the pending tile read zero
          api.get<{ requests: AttendanceRequest[] }>('/attendance/requests?' + params, { signal: controller.signal }).then((answer) => answer.requests).catch(() => [] as AttendanceRequest[]),
        ])
        if (!controller.signal.aborted) { setData({ month: monthly, requests }); setLoadedAt(new Date().toISOString()); setError('') }
      } catch (cause) {
        if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : 'We could not load attendance.')
      } finally {
        running = false
        if (!controller.signal.aborted) setLoading(false)
        if (again && !controller.signal.aborted) { again = false; void load() }
      }
    }
    void load(true)
    const stop = liveInterval(() => { if (document.visibilityState === 'visible') void load() }, 30_000, ['attendance', 'users'], {
      onChange: () => { if (running) again = true; else void load() },
    })
    return () => { controller.abort(); stop() }
  }, [month, staff, reload])

  const rows = useMemo(() => buildOverviewRows(data?.month, data?.requests), [data])
  const tiles = useMemo(() => monthTiles(data?.month, data?.requests, staff), [data, staff])
  const zone = data?.month.timezone || timeZone
  const title = monthTitle(month)
  const employee = data?.month.staff.find((person) => person.userId === opened) ?? null

  const strip: StatTileData[] = [
    { key: 'pct', label: 'Attendance', value: percentText(tiles.percentage), detail: `${tiles.people} ${tiles.people === 1 ? 'person' : 'people'}, ${title}`, icon: Percent, tone: 'blue' },
    { key: 'present', label: 'Present days', value: tiles.presentDays, detail: 'Worked days', icon: UserCheck, tone: 'green' },
    { key: 'late', label: 'Late', value: tiles.late, detail: 'Late days', icon: Clock3, tone: tiles.late ? 'amber' : 'blue' },
    { key: 'absent', label: 'Absent', value: tiles.absent, detail: 'No check-in', icon: UserX, tone: tiles.absent ? 'red' : 'blue' },
    { key: 'incomplete', label: 'Incomplete', value: tiles.incomplete, detail: 'Missed check-in or out', icon: TriangleAlert, tone: tiles.incomplete ? 'amber' : 'blue' },
    { key: 'pending', label: 'Requests pending', value: tiles.pendingRequests, detail: 'Waiting for a decision', icon: ShieldCheck, tone: tiles.pendingRequests ? 'amber' : 'blue' },
    { key: 'unpaid', label: 'Unpaid time', value: minutesText(tiles.unpaidMinutes), detail: 'After allowances', icon: WalletCards, tone: tiles.unpaidMinutes ? 'amber' : 'blue' },
  ]

  return <div className="dwr dwa-tab">
    <div className="dw-panel dwa-filters" role="group" aria-label="Attendance filters">
      <div className="dwa-field">
        <span className="dwa-field-label" id="dwa-month-label">Month</span>
        <div className="dwa-stepper">
          <IconButton label="Previous month" onClick={() => changeMonth(shiftMonth(month, -1))}><ChevronLeft size={16} /></IconButton>
          <input type="month" className="dwa-monthbox" aria-labelledby="dwa-month-label" value={month} min="2000-01" max="2100-12" required onChange={(event) => changeMonth(event.target.value)} />
          <IconButton label="Next month" onClick={() => changeMonth(shiftMonth(month, 1))}><ChevronRight size={16} /></IconButton>
        </div>
      </div>
      {team && <div className="dwa-field dwa-field-select">
        <span className="dwa-field-label" id="dwa-who-label">Recruiter</span>
        <Select compact aria-labelledby="dwa-who-label" aria-label="Recruiter" value={pick} onChange={(event) => setPick(event.target.value)}>
          <option value="">All recruiters</option>
          {people.map((person) => <option key={person.id} value={person.id}>{person.name}</option>)}
        </Select>
      </div>}
      <div className="dwa-filter-end">
        {loadedAt && <span className="dwa-updated">Updated {clockTime(loadedAt, zone)}</span>}
        <Button disabled={loading} icon={<RefreshCw size={16} className={loading ? 'animate-spin' : ''} />} onClick={() => setReload((value) => value + 1)}>Refresh</Button>
      </div>
    </div>

    <AttendancePanel day={today} staff={staff} team={team} timeZone={timeZone} onPickStaff={setPick} reload={reload} title="Today's login / logout" />

    <section className="dw-panel dwa dwa-month" aria-label="Monthly staff overview" aria-busy={loading}>
      <div className="dw-panel-head">
        <div><h2><CalendarCheck size={16} aria-hidden="true" /> Monthly staff overview</h2><p>{title}: open an employee to see their daily record.</p></div>
        <div className="dwr-head-tools dwa-tools">
          <span className="dwa-count">{rows.length} staff</span>
          <Button size="sm" disabled={!rows.length} icon={<Download size={14} />} onClick={() => saveCsv(overviewCsv(rows), overviewCsvName(month))} title="Download the table as a file that opens in Excel">Export Excel</Button>
        </div>
      </div>
      {loading && !data ? <div className="dw-state"><p role="status">Loading the month…</p></div>
        : error && !data ? <div className="dw-state"><TriangleAlert size={22} aria-hidden="true" /><p role="alert" className="dwr-empty-title">{error}</p><p>It will try again by itself, or press Refresh.</p></div>
          : !rows.length ? <div className="dw-state" role="status"><p className="dwr-empty-title">No attendance for this month</p><p>{staff ? 'This person has no attendance record for the month.' : 'No recruiter has a record for this month yet.'}</p></div>
            : <>
              <div className="dwa-strip"><StatStrip tiles={strip} label="Attendance for the month" /></div>
              <OverviewTable rows={rows} onOpen={setOpened} />
              <p className="dwa-note">Attendance counts approved paid leave; weekly offs and holidays are left out. A day still open counts for now and may change.</p>
            </>}
    </section>

    <Sheet open={!!employee} onClose={() => setOpened('')} title={employee ? `${employee.name}: daily record` : 'Daily record'} description={`${title}. Times are in ${zone}.`} width="xl">
      {employee && <DailyRecord employee={employee} zone={zone} />}
    </Sheet>
  </div>
}

function OverviewTable({ rows, onOpen }: { rows: OverviewRow[]; onOpen: (id: string) => void }) {
  return <ScrollEdges label="Monthly staff overview, scroll sideways for more columns">
    <table className="dwa-grid dwa-over" aria-label="Monthly staff overview">
      <colgroup><col style={{ width: 'auto' }} /><col style={{ width: '104px' }} /><col style={{ width: '84px' }} /><col style={{ width: '340px' }} /><col style={{ width: '138px' }} /><col style={{ width: '138px' }} /></colgroup>
      <thead><tr>
        <th scope="col" className="is-left">Employee</th>
        <th scope="col" className="is-num">Attendance</th>
        <th scope="col" className="is-num">Working days</th>
        <th scope="col" className="is-num">Exceptions</th>
        <th scope="col" className="is-num">Unpaid time</th>
        <th scope="col" className="is-num"><span className="dwa-vh">Record</span></th>
      </tr></thead>
      <tbody>
        {rows.map((row, index) => <tr key={row.userId} className={cn(index % 2 === 1 && 'is-alt')}>
          <th scope="row" className="is-left">
            <span className="dwr-person">
              <span className={cn('record-avatar', `avatar-hue-${hueOf(row.name)}`)} aria-hidden="true">{initials(row.name)}</span>
              <span className="dwa-who"><span className="dwr-who">{row.name}</span><small>{row.role === 'staff' ? '' : `${roleLabel(row.role)} · `}{row.tracked}{row.active ? '' : ' · Inactive'}</small></span>
            </span>
          </th>
          <td className="is-num" data-label="Attendance">{row.percentage == null
            ? <span className="dwa-muted">No working days yet</span>
            : <span className="dwa-rate"><strong className="tnum">{row.percentText}</strong><span className="dwa-meter" role="meter" aria-label={`${row.name} attendance`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={row.percentage}><i style={{ width: `${row.bar}%` }} /></span></span>}</td>
          <td className="is-num" data-label="Working days"><span className="dwr-plain tnum"><strong>{row.daysText}</strong></span></td>
          <td className="is-num" data-label="Exceptions"><span className="dwa-chips">{row.chips.map((chip) => <span key={chip.key} className={cn('dwa-chip', `tone-${chip.tone}`)}>{chip.label}</span>)}</span></td>
          <td className="is-num" data-label="Unpaid time"><span className="dwa-two"><strong className={cn('tnum', row.unpaidMinutes > 0 && 'dwa-warn')}>{row.unpaidText}</strong><small>After paid allowance</small></span></td>
          <td className="is-num" data-label="Record"><button type="button" className="dwa-view" onClick={() => onOpen(row.userId)} aria-label={`View attendance record for ${row.name}`}>View record <ChevronRight size={14} aria-hidden="true" /></button></td>
        </tr>)}
      </tbody>
    </table>
  </ScrollEdges>
}

function DailyRecord({ employee, zone }: { employee: EmployeeMonth; zone: string }) {
  const rows = useMemo(() => dailyRecordRows(employee), [employee])
  const totals = useMemo(() => dailyRecordTotals(rows), [rows])
  const s = employee.summary
  const facts: [string, string][] = [
    ['Attendance', percentText(s.attendancePercentage)],
    ['Working days', `${s.attendedDays ?? 0} of ${s.scheduledDays ?? 0}`],
    ['Late days', String(s.lateDays ?? 0)],
    ['Absent days', String(s.absenceDays ?? 0)],
    ['Incomplete', String(s.incompleteDays ?? 0)],
    ['Unpaid time', minutesText(s.unpaidMinutes ?? 0)],
  ]
  return <div className="dwa-rec">
    <dl className="dwa-facts">{facts.map(([label, value]) => <div key={label}><dt>{label}</dt><dd className="tnum">{value}</dd></div>)}</dl>
    {!rows.length
      ? <div className="dw-state" role="status"><p className="dwr-empty-title">No records in this month</p><p>Records start with the first check-in, leave or fix.</p></div>
      : <div className="dwa-rec-scroll"><table aria-label={`Daily record of ${employee.name}`}>
        <thead><tr>
          <th scope="col" className="is-left">Date</th><th scope="col">Day</th><th scope="col">Status</th><th scope="col">Shift</th>
          <th scope="col">Login</th><th scope="col">Logout</th><th scope="col">Hours worked</th><th scope="col">Late / early by</th>
        </tr></thead>
        <tbody>
          {rows.map((row) => <tr key={row.date} className={cn(row.off && 'is-off', row.leave && 'is-leave')}>
            <th scope="row" className="is-left tnum">{row.dayLabel}</th>
            <td>{row.weekday}</td>
            <td><span className={cn('dwa-pill', `tone-${row.tone}`)}>{attendanceLabels[row.status as keyof typeof attendanceLabels] ?? row.status}</span>{row.provisional && <small className="dwa-draft">Not final</small>}</td>
            <td className="tnum">{row.shift || DASH}</td>
            <td className="tnum">{row.checkIn ? clockTime(row.checkIn, zone) : DASH}</td>
            <td className="tnum">{row.stillIn ? <span className="dwa-live"><i aria-hidden="true" />Still in</span> : row.checkOut ? clockTime(row.checkOut, zone) : DASH}</td>
            <td className="tnum">{row.workedText || DASH}</td>
            <td className={cn('tnum', row.punctuality && 'dwa-warn')}>{row.punctuality || DASH}</td>
          </tr>)}
        </tbody>
        <tfoot><tr>
          <th scope="row" className="is-left" colSpan={4}>Month total</th>
          <td colSpan={2}>{totals.daysWorked} {totals.daysWorked === 1 ? 'day' : 'days'} worked</td>
          <td className="tnum">{minutesText(totals.workedMinutes)}</td>
          <td className="tnum">{totals.lateMinutes || totals.earlyMinutes ? [totals.lateMinutes ? `Late ${minutesText(totals.lateMinutes)}` : '', totals.earlyMinutes ? `Early ${minutesText(totals.earlyMinutes)}` : ''].filter(Boolean).join(' · ') : DASH}</td>
        </tr></tfoot>
      </table></div>}
    {rows.length > 0 && <p className="dwa-note">{totals.weeklyOffs} weekly {totals.weeklyOffs === 1 ? 'off' : 'offs'}, {totals.holidays} {totals.holidays === 1 ? 'holiday' : 'holidays'}, {totals.leaves} {totals.leaves === 1 ? 'day' : 'days'} of leave. Unpaid time for the month: {minutesText(s.unpaidMinutes ?? 0)} after allowances.</p>}
  </div>
}
