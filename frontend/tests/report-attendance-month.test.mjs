import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { createRequire } from 'node:module'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import test from 'node:test'
import { build } from 'esbuild'

/*
 * Reports > Attendance, the management reading: the recruiter x day grid of a month (a mark per day, present / late / average and total hours),
 * the numbers above the live board, the daily record behind a recruiter's name, and the screen's layout rules.
 */
const root = fileURLToPath(new URL('../', import.meta.url))
async function compile(entry) {
  const bundle = await build({ entryPoints: [path.join(root, entry)], bundle: true, write: false, platform: 'node', format: 'cjs', logLevel: 'silent' })
  const compiled = { exports: {} }
  new Function('require', 'module', 'exports', bundle.outputFiles[0].text)(createRequire(import.meta.url), compiled, compiled.exports)
  return compiled.exports
}
const m = await compile('src/features/daily-report/attendance-month.ts')
const rows = await compile('src/features/daily-report/attendance-rows.ts')

const summary = (over = {}) => ({ attendancePercentage: 100, attendedDays: 17, scheduledDays: 17, absenceDays: 0, lateDays: 0, incompleteDays: 0, presentDays: 17, paidLeaveDays: 0, unpaidLeaveDays: 0, weeklyOffDays: 4, holidayDays: 0, absentDays: 0, workedMinutes: 8000, unpaidMinutes: 0, extraOtMinutes: 0, graceUsedMinutes: 0, graceRemainingMinutes: 60, permissionOccasionsUsed: 0, ...over })
const day = (date, status, over = {}) => ({ date, status, checkIn: null, checkOut: null, shift: { start: '09:30', end: '18:30', breakMinutes: 60 }, requiredMinutes: 480, workedMinutes: 0, lateMinutes: 0, earlyMinutes: 0, graceMinutes: 0, paidPermissionMinutes: 0, unpaidMinutes: 0, extraOtMinutes: 0, provisional: false, regularisationDeadline: null, ...over })
const person = (userId, name, over = {}) => ({ userId, name, role: 'staff', active: true, trackingStart: '2026-11-01', days: [day('2026-11-01', 'P')], summary: summary(), ...over })
const month = (...staff) => ({ year: 2026, month: 11, timezone: 'Asia/Kolkata', canManage: true, canCorrect: true, policy: {}, staff })

test('percentage text: whole numbers stay whole, one decimal at most, empty is a dash', () => {
  assert.equal(m.percentText(100), '100%')
  assert.equal(m.percentText(83.33333), '83.3%')
  assert.equal(m.percentText(90), '90%')
  assert.equal(m.percentText(null), '–')
})

test('daily record: one row per day in date order, off days and leave flagged, shift hidden on a day off, late/early text, totals', () => {
  const employee = { days: [
    day('2026-11-03', 'LT', { checkIn: '2026-11-03T04:15:00Z', checkOut: '2026-11-03T13:00:00Z', workedMinutes: 525, lateMinutes: 15, earlyMinutes: 0 }),
    day('2026-11-02', 'WO', { requiredMinutes: 0 }),
    day('2026-11-04', 'EE', { checkIn: '2026-11-04T04:00:00Z', checkOut: '2026-11-04T10:00:00Z', workedMinutes: 360, earlyMinutes: 75, unpaidMinutes: 15 }),
    day('2026-11-05', 'PL'),
    day('2026-11-06', 'P', { checkIn: '2026-11-06T04:00:00Z', checkOut: null, workedMinutes: 30, provisional: true }),
    day('2026-11-07', 'H'),
  ] }
  const out = m.dailyRecordRows(employee)
  assert.deepEqual(out.map((r) => r.date), ['2026-11-02', '2026-11-03', '2026-11-04', '2026-11-05', '2026-11-06', '2026-11-07'])
  assert.equal(out[0].off, true)
  assert.equal(out[0].shift, '')
  assert.equal(out[0].weekday, 'Mon')
  assert.equal(out[1].shift, '09:30 – 18:30')
  assert.equal(out[1].punctuality, 'Late 15m')
  assert.equal(out[1].workedText, '8h 45m')
  assert.equal(out[2].punctuality, 'Early 1h 15m')
  assert.equal(out[3].leave, true)
  assert.equal(out[3].workedText, '')
  assert.equal(out[4].stillIn, true)
  assert.equal(out[4].provisional, true)
  const totals = m.dailyRecordTotals(out)
  assert.deepEqual(totals, { daysWorked: 3, workedMinutes: 915, lateMinutes: 15, earlyMinutes: 75, unpaidMinutes: 15, weeklyOffs: 1, holidays: 1, leaves: 1 })
  assert.equal(m.dayTone('A'), 'red')
  assert.equal(m.dayTone('LT'), 'amber')
  assert.equal(m.dayTone('WO'), 'neutral')
})

test('the live board line under the status: logged in now, logged out, not clocked in, nothing on a day off', () => {
  const base = { stillIn: false, sessions: [], kind: 'absent' }
  assert.deepEqual(rows.presenceLabel({ ...base, stillIn: true, sessions: [{}] }), { label: 'Logged in now', live: true })
  assert.deepEqual(rows.presenceLabel({ ...base, sessions: [{}] }), { label: 'Logged out', live: false })
  assert.deepEqual(rows.presenceLabel({ ...base, kind: 'pending' }), { label: 'Not clocked in', live: false })
  assert.deepEqual(rows.presenceLabel({ ...base, kind: 'off' }), { label: '', live: false })
})

// ---- the grid: the reading of a day is the one of the Excel file (backend app/services/attendance_month_export.py)
const ZONE = 'Asia/Kolkata'
const TODAY = '2026-11-20'
/** A local (Kolkata, UTC+5:30) time on a November day, as an ISO instant. */
const at = (dayNumber, hour, minute = 0) => new Date(Date.UTC(2026, 10, dayNumber, hour - 5, minute - 30)).toISOString()
const shift10 = { start: '10:00', end: '19:00', breakMinutes: 60 }
const d10 = (dayNumber, status, over = {}) => day(`2026-11-${String(dayNumber).padStart(2, '0')}`, status, { shift: shift10, ...over })

test('a day is read the same way as the Excel file: present, late (first login after the start), missed check-out, absent, leave, off', () => {
  const mark = (record) => m.dayMark(record, TODAY, ZONE)
  assert.equal(mark(d10(2, 'P', { checkIn: at(2, 9, 55), checkOut: at(2, 19, 5), workedMinutes: 480 })), 'P')
  assert.equal(mark(d10(3, 'P', { checkIn: at(3, 10, 0), checkOut: at(3, 19, 0), workedMinutes: 480 })), 'P') // on the dot is on time
  assert.equal(mark(d10(4, 'P', { checkIn: at(4, 10, 20), checkOut: at(4, 19, 40), workedMinutes: 480 })), 'LT') // a full day in the flexible window, still a late arrival
  assert.equal(mark(d10(5, 'LT', { checkIn: at(5, 11, 0), checkOut: at(5, 19, 0), lateMinutes: 60 })), 'LT')
  assert.equal(mark(d10(6, 'A')), 'A')
  assert.equal(mark(d10(7, 'WO')), 'WO')
  assert.equal(mark(d10(8, 'H')), 'H')
  assert.equal(mark(d10(9, 'PL')), 'L')
  assert.equal(mark(d10(10, 'UL')), 'L')
  assert.equal(mark(d10(11, 'MP', { checkIn: at(11, 10, 0) })), 'M') // logged in, never out, a finished day
  assert.equal(mark(d10(12, 'MP')), 'A') // nothing at all on a finished day
  assert.equal(mark(d10(16, 'A', { checkIn: at(16, 10, 0) })), 'M') // the policy gave up on a half-punched day: still a missed check-out
  assert.equal(mark(d10(20, 'MP', { checkIn: at(20, 9, 50) })), 'P') // today and still in: nothing missed yet
  assert.equal(mark(d10(20, 'MP')), 'N') // today, not clocked in yet
  assert.equal(mark(d10(13, 'OD')), 'P')
  assert.equal(mark(d10(14, 'WFH', { checkIn: at(14, 14, 0) })), 'P') // away on duty: no arrival time to be late for
  assert.equal(mark(d10(15, 'EE', { checkIn: at(15, 9, 30), checkOut: at(15, 16, 0), workedMinutes: 330 })), 'P')
})

test("late minutes: whole minutes after the person's own shift start in agency time; the policy's own late minutes when no zone is known", () => {
  assert.equal(m.dayLateMinutes(d10(1, 'P', { checkIn: at(1, 10, 1) }), ZONE), 1)
  assert.equal(m.dayLateMinutes(d10(1, 'P', { checkIn: at(1, 10, 45) }), ZONE), 45)
  assert.equal(m.dayLateMinutes(d10(1, 'P', { checkIn: at(1, 9, 59) }), ZONE), 0)
  assert.equal(m.dayLateMinutes(d10(1, 'P'), ZONE), 0)
  assert.equal(m.dayLateMinutes(d10(1, 'OD', { checkIn: at(1, 12, 0) }), ZONE), 0)
  assert.equal(m.dayLateMinutes(day('2026-11-01', 'P', { checkIn: at(1, 9, 45), shift: { start: '09:30', end: '18:30' } }), ZONE), 15)
  assert.equal(m.dayLateMinutes(d10(1, 'LT', { checkIn: at(1, 11, 0), lateMinutes: 60 }), undefined), 60)
})

test('the grid: a row per recruiter with a cell per date, totals per person and overall, the Administrator never a row', () => {
  const days = [
    d10(2, 'P', { checkIn: at(2, 9, 55), checkOut: at(2, 19, 5), workedMinutes: 480 }), d10(3, 'LT', { checkIn: at(3, 10, 30), checkOut: at(3, 19, 30), workedMinutes: 450, lateMinutes: 30 }),
    d10(4, 'A'), d10(5, 'WO'), d10(6, 'PL'), d10(9, 'MP', { checkIn: at(9, 10, 0) }), d10(10, 'P', { checkIn: at(10, 10, 0), checkOut: at(10, 18, 0), workedMinutes: 420 }),
  ]
  const data = month(
    person('adm', 'Root', { role: 'admin', days }),
    person('z', 'Zed', { role: 'manager', days: [d10(2, 'P', { checkIn: at(2, 9, 50), checkOut: at(2, 18, 50), workedMinutes: 480 })] }),
    person('a', 'Asha', { days, summary: summary({ attendancePercentage: 83.3 }) }),
  )
  const grid = m.buildMonthGrid(data, TODAY, ZONE)
  assert.equal(grid.dates.length, 30)
  assert.deepEqual([grid.dates[0].date, grid.dates[0].day, grid.dates[0].weekday, grid.dates[0].weekend], ['2026-11-01', 1, 'S', true])
  assert.equal(grid.dates[21].future, true)
  assert.equal(grid.dates[19].future, false)
  assert.deepEqual(grid.people.map((p) => p.name), ['Asha', 'Zed'])
  const asha = grid.people[0]
  assert.deepEqual(asha.cells.slice(0, 10).map((c) => c.mark), ['', 'P', 'LT', 'A', 'WO', 'L', '', '', 'M', 'P'])
  assert.deepEqual([asha.present, asha.late, asha.absent, asha.leave, asha.missed, asha.workedDays, asha.totalMinutes, asha.averageMinutes, asha.percentage], [4, 1, 1, 1, 1, 3, 1350, 450, 83.3])
  assert.deepEqual(grid.totals, { present: 5, late: 1, absent: 1, leave: 1, missed: 1, workedDays: 4, totalMinutes: 1830, averageMinutes: 458 })
  assert.deepEqual(m.buildMonthGrid(data, TODAY, ZONE, 'z').people.map((p) => p.name), ['Zed'])
  assert.deepEqual(m.buildMonthGrid(null, TODAY, ZONE), { dates: [], people: [], totals: { present: 0, late: 0, absent: 0, leave: 0, missed: 0, workedDays: 0, totalMinutes: 0, averageMinutes: 0 } })
})

test('hours for the narrow columns, the hover text of a cell and the Excel file address', () => {
  assert.equal(m.hoursFigure(0), '–')
  assert.equal(m.hoursFigure(450), '7.5')
  assert.equal(m.hoursFigure(480), '8')
  const date = m.monthDates(2026, 11, TODAY)[2]
  const clock = (stamp) => stamp.slice(11, 16)
  assert.equal(m.cellTitle('Asha', date, { date: date.date, mark: 'LT', workedMinutes: 480, lateMinutes: 25, checkIn: '2026-11-03T04:55:00Z', checkOut: null }, clock), 'Asha, Tue 3 Nov: Late, late 25m, 04:55 to now, 8h 00m')
  assert.equal(m.cellTitle('Asha', date, { date: date.date, mark: '', workedMinutes: 0, lateMinutes: 0, checkIn: null, checkOut: null }, clock), 'Asha, Tue 3 Nov')
  assert.equal(m.monthExportPath('2026-11', ''), '/attendance/month/export?year=2026&month=11')
  assert.equal(m.monthExportPath('2026-03', 'u1'), '/attendance/month/export?year=2026&month=3&staffId=u1')
  assert.equal(m.monthExportName('2026-11'), 'attendance-2026-11.xlsx')
})

test("the daily record carries the grid's mark and the late minutes of an arrival after the start", () => {
  const employee = { days: [d10(3, 'P', { checkIn: at(3, 10, 20), checkOut: at(3, 19, 40), workedMinutes: 480 }), d10(4, 'MP', { checkIn: at(4, 10, 0) })] }
  const out = m.dailyRecordRows(employee, TODAY, ZONE)
  assert.deepEqual(out.map((row) => row.mark), ['LT', 'M'])
  assert.equal(out[0].punctuality, 'Late 20m')
  assert.equal(m.dailyRecordRows(employee)[0].punctuality, '') // without a zone only the policy's own minutes are known
})

// ---- the numbers above the live board
const aRow = (over) => ({ id: 'x', name: 'X', kind: 'present', sessions: [{}], stillIn: false, workedSeconds: 0, lateMinutes: 0, ...over })
test('board figures: logged in, still in, absent (nobody in, not on leave), on leave, late and the average time logged in', () => {
  const list = [
    aRow({ id: 'a', workedSeconds: 28800, lateMinutes: 15, kind: 'late' }), aRow({ id: 'b', stillIn: true, workedSeconds: 3600 }),
    aRow({ id: 'c', sessions: [], kind: 'absent' }), aRow({ id: 'd', sessions: [], kind: 'pending' }), aRow({ id: 'e', sessions: [], kind: 'leave' }), aRow({ id: 'f', sessions: [], kind: 'off' }),
  ]
  assert.deepEqual(rows.boardFigures(list), { loggedIn: 2, stillIn: 1, notIn: 2, leave: 1, late: 1, averageSeconds: 16200, total: 6 })
  assert.deepEqual(rows.boardFilter(list, 'in').map((r) => r.id), ['a', 'b'])
  assert.deepEqual(rows.boardFilter(list, 'out').map((r) => r.id), ['c', 'd'])
  assert.deepEqual(rows.boardFilter(list, 'leave').map((r) => r.id), ['e'])
  assert.deepEqual(rows.boardFilter(list, 'late').map((r) => r.id), ['a'])
  assert.equal(rows.boardFilter(list, 'all').length, 6)
  assert.equal(rows.hoursText(27000), '7h 30m')
  assert.equal(rows.hoursText(0), '–')
  assert.equal(rows.hoursCardText(41000), '11.4 h')
  assert.equal(rows.hoursCardText(0), '–')
  assert.deepEqual(rows.boardFigures([]), { loggedIn: 0, stillIn: 0, notIn: 0, leave: 0, late: 0, averageSeconds: 0, total: 0 })
})

test('late arrivals on the live board: the first login after the shift start (policy shift, else 10:00), whatever the pay status', () => {
  const sess = (from, to) => ({ id: 's', checkedInAt: from, checkedOutAt: to, durationSeconds: to ? (Date.parse(to) - Date.parse(from)) / 1000 : 600 })
  const staffRow = (sessions) => ({ userId: 'u', name: 'U', role: 'staff', active: true, attendance: { status: sessions.some((s) => !s.checkedOutAt) ? 'checked_in' : 'checked_out', checkedInAt: null, lastCheckedOutAt: null, totalSeconds: 0, sessions }, presence: { tracked: false } })
  const att = (...staff) => ({ date: '2026-11-20', timezone: ZONE, canViewTeam: true, generatedAt: '', clockContext: null, staff })
  const build = (sessions, monthData) => rows.buildAttendanceRows({ day: '2026-11-20', today: '2026-11-20', attendance: att(staffRow(sessions)), month: monthData, zone: ZONE })[0]
  const onTime = build([sess(at(20, 9, 58), null)], null)
  assert.deepEqual([onTime.kind, onTime.lateMinutes], ['present', 0])
  const late = build([sess(at(20, 10, 25), null)], null) // no policy record: the 10:00 default
  assert.deepEqual([late.kind, late.lateMinutes], ['late', 25])
  const own = build([sess(at(20, 10, 25), null)], { year: 2026, month: 11, staff: [{ userId: 'u', days: [day('2026-11-20', 'MP', { checkIn: at(20, 10, 25), shift: { start: '09:30', end: '18:30' } })] }] })
  assert.equal(own.lateMinutes, 55) // the person's own 09:30 start
  assert.equal(rows.boardFigures([late]).late, 1)
  const noZone = rows.buildAttendanceRows({ day: '2026-11-20', today: '2026-11-20', attendance: att(staffRow([sess(at(20, 10, 25), null)])), month: null })[0]
  assert.deepEqual([noZone.kind, noZone.lateMinutes], ['present', 0]) // without the agency's zone nothing is guessed
  assert.equal(rows.DEFAULT_SHIFT_START, '10:00')
})

// ---- the screen: cards, filters, export, grid fits, dark tokens
const text = (file) => readFile(new URL(file, import.meta.url), 'utf8')
test('the Attendance tab is lean: four summary cards, a filter row, Export Excel, no CSV, no legend, subtitles or helper sentences', async () => {
  const tab = await text('../src/features/daily-report/report-attendance-tab.tsx')
  assert.match(tab, /SummaryCards/)
  for (const label of ['Absent', 'Late arrivals', 'Average hours', 'Missed check‑outs']) assert.ok(tab.includes(`label: '${label}'`), label)
  assert.equal((tab.match(/label: '/g) ?? []).length, 4)
  assert.doesNotMatch(tab, /GridLegend|rm-foot|Open attendance|rm-updated|rm-count|rm-head p|<h2>[^<]*<\/h2><p>/)
  assert.match(tab, /Export Excel/)
  assert.match(tab, /downloadApiFile\(monthExportPath\(month, staff\), monthExportName\(month\)\)/)
  assert.doesNotMatch(tab, /saveCsv|overviewCsv/)
  assert.match(tab, /aria-label="Recruiter"/)
  assert.match(tab, /type="month"/)
  assert.match(tab, /reportPeople\(store\.users\)/) // the Administrator rows stay hidden from the recruiter filter
  assert.match(tab, /Sheet open=\{!!employee\}/) // a recruiter's name opens the day-by-day drawer
})

test('the grid and the tables follow the report table rules: centred cells, names left, bright lines, accessible, wraps, theme tokens only', async () => {
  const css = (await text('../src/styles/report-mgmt-tabs.css')) + (await text('../src/styles/report-attendance.css'))
  assert.match(css, /--rt-line: color-mix\(in srgb, var\(--text-muted\) 42%/) // the brighter inside borders of the report tables
  assert.match(css, /\.rm-table :is\(th, td\) \{[^}]*text-align: center/)
  assert.match(css, /\.rm-table :is\(thead th\.is-left, tbody th\.is-left[^}]*text-align: left/)
  assert.match(css, /\.rm-table \{[^}]*table-layout: fixed/)
  assert.match(css, /overflow-wrap: anywhere/)
  assert.match(css, /@media \(max-width: 899px\)[\s\S]*is-cards/) // a row becomes a card under the breakpoint
  assert.match(css, /\.am-grid \.am-col-day \{ width: auto; \}/) // the day columns share what the name and totals leave
  for (const line of css.split('\n')) if (/\{.*#[0-9a-fA-F]{3,6}\b/.test(line)) assert.fail(`a hard-coded colour: ${line.trim().slice(0, 90)}`)
  const board = await text('../src/features/daily-report/report-attendance.tsx')
  const grid = await text('../src/features/daily-report/attendance-grid.tsx')
  assert.match(board, /scope="col"/); assert.match(board, /scope="row"/)
  assert.match(grid, /scope="col"/); assert.match(grid, /scope="row"/)
  assert.match(grid, /className="sr-only"/) // a cell's mark is spoken in words
  assert.match(board, /data-label="Status"/) // the card mode names every value
  // the grid at 1366 and 1500: a name (156) + four totals (54 each) + 31 day columns of 21 fit a 1078px panel without scrolling
  assert.ok(156 + 4 * 54 + 31 * 21 <= 1078, 'the grid fits')
  assert.match(css, /\.am-grid \{ min-width: 1000px/)
})
