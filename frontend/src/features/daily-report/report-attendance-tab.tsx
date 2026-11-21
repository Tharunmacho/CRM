import { useCallback, useMemo, useState } from 'react'
import { CalendarCheck, ChevronLeft, ChevronRight, Clock3, FileSpreadsheet, LogOut, RefreshCw, Timer, TriangleAlert, UserX, X, Loader2 } from 'lucide-react'
import { useStore } from '@/shared/state/crm-store'
import { agencyDay } from '@/shared/lib/task-time'
import { cn } from '@/shared/lib/cn'
import { downloadApiFile } from '@/shared/api/download'
import { Button, IconButton, Select } from '@/shared/ui/primitives'
import { DatePicker } from '@/shared/ui/date-picker'
import { Sheet } from '@/shared/ui/overlay'
import { SummaryCards } from '@/shared/ui/workspace'
import { dayLabel, monthTitle, shiftMonth, weekdayLabel } from '../attendance/format'
import { reportPeople } from './daily-report-view'
import { boardFigures, boardFilter, buildAttendanceRows, hoursCardText, type BoardFilter } from './attendance-rows'
import { buildMonthGrid, monthExportName, monthExportPath } from './attendance-month'
import { useDayAttendance, useMonthAttendance } from './use-attendance-data'
import { AttendanceBoard } from './report-attendance'
import { DailyRecord, MonthGridTable } from './attendance-grid'
import '@/styles/report-mgmt-tabs.css'
import '@/styles/report-attendance.css'

const MONTH_RE = /^\d{4}-\d{2}$/
const FILTER_NAMES: Partial<Record<BoardFilter, string>> = { in: 'Logged in', out: 'Not logged in', leave: 'On leave', late: 'Late arrivals' }

/**
 * Reports > Attendance, read from the management side: summary cards (who is in, who is absent, on leave, late, hours, missed check-outs), the
 * live login / logout board for one day, then the month as a recruiter x day grid with each person's totals. A recruiter's name opens their
 * day-by-day login and logout times for the month; "Export Excel" downloads the month. Administrator accounts are never listed.
 * Self-contained: it reads the attendance screen's own endpoints and draws below the shell's page title and tab strip.
 */
export function AttendanceReportTab() {
  const store = useStore()
  const notify = store.notify
  const timeZone = store.agencyTimezone || 'Asia/Kolkata'
  const today = agencyDay(new Date(), timeZone)
  const team = store.me.role !== 'staff'
  const [month, setMonth] = useState(today.slice(0, 7))
  const [date, setDate] = useState(today)
  const [pick, setPick] = useState('')
  const [filter, setFilter] = useState<BoardFilter>('all')
  const [opened, setOpened] = useState('')
  const [reload, setReload] = useState(0)
  const [exporting, setExporting] = useState(false)
  const staff = team ? pick : store.me.id

  const people = useMemo(() => reportPeople(store.users).filter((user) => !user.deleted && (user.active || user.id === pick)).sort((a, b) => a.name.localeCompare(b.name)), [store.users, pick])
  const changeMonth = useCallback((value: string) => { if (MONTH_RE.test(value) && value >= '2000-01' && value <= '2100-12') setMonth(value) }, [])

  const monthState = useMonthAttendance(month, staff, reload)
  const dayState = useDayAttendance(date, staff, reload, month, monthState.data)
  const zone = monthState.data?.timezone || dayState.attendance?.timezone || timeZone
  const title = monthTitle(month)

  const allRows = useMemo(() => buildAttendanceRows({ day: date, today, staff, attendance: dayState.attendance, month: dayState.month, zone }), [date, today, staff, dayState.attendance, dayState.month, zone])
  const figures = useMemo(() => boardFigures(allRows), [allRows])
  const rows = useMemo(() => boardFilter(allRows, filter), [allRows, filter])
  const grid = useMemo(() => buildMonthGrid(monthState.data, today, zone, staff), [monthState.data, today, zone, staff])
  const employee = monthState.data?.staff.find((person) => person.userId === opened) ?? null
  const isToday = date === today
  const dayName = `${weekdayLabel(date, 'short')} ${dayLabel(date)}`

  const choose = (next: BoardFilter) => setFilter((current) => (current === next ? 'all' : next))
  const cards = [
    { label: 'Absent', value: figures.notIn, detail: isToday ? 'no login today' : dayName, icon: UserX, tone: figures.notIn ? 'red' as const : 'blue' as const, active: filter === 'out', onSelect: () => choose('out') },
    { label: 'Late arrivals', value: figures.late, detail: isToday ? 'today' : dayName, icon: Clock3, tone: figures.late ? 'amber' as const : 'blue' as const, active: filter === 'late', onSelect: () => choose('late') },
    { label: 'Average hours', value: hoursCardText(figures.averageSeconds), detail: grid.totals.workedDays ? `${hoursCardText(grid.totals.averageMinutes * 60)} a day in ${title.split(' ')[0]}` : isToday ? 'today' : dayName, icon: Timer, tone: 'violet' as const },
    { label: 'Missed check‑outs', value: grid.totals.missed, detail: title, icon: LogOut, tone: grid.totals.missed ? 'amber' as const : 'blue' as const },
  ]

  const exportFile = async () => {
    setExporting(true)
    try { await downloadApiFile(monthExportPath(month, staff), monthExportName(month)) }
    catch (failure) { notify(failure instanceof Error ? failure.message : 'The file could not be created. Please try again.', 'warn') }
    finally { setExporting(false) }
  }

  const monthBusy = monthState.loading && !monthState.data

  return <div className="rm am">
    <div className="rm-cards"><SummaryCards items={cards} /></div>

    <div className="rm-panel am-filterpanel">
      <div className="rm-filters am-filters" role="group" aria-label="Attendance filters">
        {team && <label className="rm-filter rm-filter-wide"><span>Recruiter</span>
          <Select compact aria-label="Recruiter" value={pick} onChange={(event) => setPick(event.target.value)}>
            <option value="">All recruiters</option>
            {people.map((person) => <option key={person.id} value={person.id}>{person.name}</option>)}
          </Select>
        </label>}
        <div className="rm-filter" role="group" aria-label="Day for the live board"><span id="am-day-label">Live board day</span>
          <DatePicker value={date} onChange={(value) => { if (value) setDate(value) }} max={today} required compact aria-label="Day for the live board" />
        </div>
        <div className="rm-filter" role="group" aria-labelledby="am-month-label"><span id="am-month-label">Month</span>
          <div className="rm-stepper">
            <IconButton label="Previous month" onClick={() => changeMonth(shiftMonth(month, -1))}><ChevronLeft size={16} /></IconButton>
            <input type="month" className="rm-month" aria-labelledby="am-month-label" value={month} min="2000-01" max="2100-12" required onChange={(event) => changeMonth(event.target.value)} />
            <IconButton label="Next month" onClick={() => changeMonth(shiftMonth(month, 1))}><ChevronRight size={16} /></IconButton>
          </div>
        </div>
        {filter !== 'all' && <>
          <span className="rm-viewchip">Live board: {FILTER_NAMES[filter]}</span>
          <Button size="sm" variant="ghost" icon={<X size={14} />} onClick={() => setFilter('all')}>Clear</Button>
        </>}
        <div className="rm-filters-end">
          <Button disabled={monthState.loading || dayState.loading} icon={<RefreshCw size={16} className={monthState.loading || dayState.loading ? 'rm-spin' : ''} />} onClick={() => setReload((value) => value + 1)}>Refresh</Button>
          <Button variant="secondary" disabled={exporting || monthBusy || !grid.people.length} icon={exporting ? <Loader2 size={16} className="rm-spin" /> : <FileSpreadsheet size={16} />} onClick={() => void exportFile()}
            title={`Download ${title} as an Excel file: the overview and every day`}>Export Excel</Button>
        </div>
      </div>
    </div>

    <section className="rm-panel" aria-label="Login and logout for the day" aria-busy={dayState.loading || undefined}>
      <div className="rm-head">
        <div><h2><Clock3 size={16} aria-hidden="true" /> {isToday ? "Today's login / logout" : `Login / logout, ${weekdayLabel(date, 'short')} ${dayLabel(date)}`}</h2></div>
      </div>
      {dayState.loading && !dayState.attendance ? <div className="rm-state"><p role="status">Loading attendance…</p></div>
        : dayState.error && !dayState.attendance ? <div className="rm-state"><TriangleAlert size={22} aria-hidden="true" /><p role="alert" className="rm-state-title">{dayState.error}</p><p>It will try again by itself, or press Refresh.</p></div>
          : <AttendanceBoard rows={rows} zone={zone} staff={staff} onOpen={setOpened}
            empty={filter !== 'all' ? 'Nobody matches the card you chose. Press Clear to see everyone.' : staff ? 'This person has no attendance record for the day.' : 'Nobody has a record for this day.'} />}
    </section>

    <section className="rm-panel" aria-label="Monthly overview" aria-busy={monthState.loading || undefined}>
      <div className="rm-head">
        <div><h2><CalendarCheck size={16} aria-hidden="true" /> Monthly overview, {title}</h2></div>
      </div>
      {monthBusy ? <div className="rm-state"><p role="status">Loading the month…</p></div>
        : monthState.error && !monthState.data ? <div className="rm-state"><TriangleAlert size={22} aria-hidden="true" /><p role="alert" className="rm-state-title">{monthState.error}</p><p>It will try again by itself, or press Refresh.</p></div>
          : !grid.people.length ? <div className="rm-state" role="status"><p className="rm-state-title">No attendance for this month</p><p>{staff ? 'This person has no attendance record for the month.' : 'No recruiter has a record for this month yet.'}</p></div>
            : <MonthGridTable grid={grid} zone={zone} onOpen={setOpened} />}
    </section>

    <Sheet open={!!employee} onClose={() => setOpened('')} title={employee ? `${employee.name}: day by day` : 'Daily record'} width="xl">
      {employee && <DailyRecord employee={employee} person={grid.people.find((person) => person.userId === employee.userId) ?? null} zone={zone} today={today} />}
    </Sheet>
  </div>
}
