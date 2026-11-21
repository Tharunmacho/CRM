import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import test from 'node:test'
import { build } from 'esbuild'

/*
 * Reports > Attendance, monthly staff overview: one row per recruiter (attendance percentage, "x of y" working days, exception chips,
 * unpaid time), the tiles above it, the daily record behind "View record" and the CSV of the overview.
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
const request = (userId, status = 'pending') => ({ userId, status })

test('percentage text: whole numbers stay whole, one decimal at most, empty is a dash', () => {
  assert.equal(m.percentText(100), '100%')
  assert.equal(m.percentText(83.33333), '83.3%')
  assert.equal(m.percentText(90), '90%')
  assert.equal(m.percentText(null), '–')
})

test('an overview row: percentage with bar, "x of y", tracked-from text, unpaid text, ordered by name, admins left out', () => {
  const data = month(person('b', 'Bala', { summary: summary({ attendancePercentage: 83.3, attendedDays: 15, scheduledDays: 18, unpaidMinutes: 174 }) }), person('a', 'Asha'), person('z', 'Boss', { role: 'admin' }))
  const out = m.buildOverviewRows(data, [])
  assert.deepEqual(out.map((r) => r.name), ['Asha', 'Bala'])
  assert.equal(out[0].percentText, '100%')
  assert.equal(out[1].percentText, '83.3%')
  assert.equal(out[1].bar, 83.3)
  assert.equal(out[1].daysText, '15 of 18')
  assert.equal(out[1].unpaidText, '2h 54m')
  assert.equal(out[0].tracked, 'Tracked from 1 Nov 2026')
})

test('a person with no scheduled day has no percentage and an empty bar', () => {
  const [row] = m.buildOverviewRows(month(person('a', 'Asha', { days: [], trackingStart: null, summary: summary({ attendancePercentage: null, attendedDays: 0, scheduledDays: 0 }) })))
  assert.equal(row.percentage, null)
  assert.equal(row.percentText, '–')
  assert.equal(row.bar, 0)
  assert.equal(row.tracked, 'No records yet')
})

test('exception chips: absent, late, incomplete and requests, toned only when above zero; rejected requests do not count', () => {
  const data = month(person('a', 'Asha', { summary: summary({ absenceDays: 0, lateDays: 3, incompleteDays: 1 }) }))
  const [row] = m.buildOverviewRows(data, [request('a'), request('a', 'approved'), request('a', 'rejected'), request('other')])
  assert.deepEqual(row.chips.map((c) => c.label), ['0 absent', '3 late', '1 incomplete', '2 requests'])
  assert.deepEqual(row.chips.map((c) => c.tone), ['neutral', 'amber', 'amber', 'blue'])
  const [one] = m.buildOverviewRows(data, [request('a')])
  assert.equal(one.chips[3].label, '1 request')
})

test('tiles: attended over scheduled across the people shown, counts added up, pending requests of those people only', () => {
  const data = month(
    person('a', 'Asha', { summary: summary({ attendedDays: 15, scheduledDays: 20, presentDays: 14, lateDays: 2, absenceDays: 1, incompleteDays: 1, unpaidMinutes: 60 }) }),
    person('b', 'Bala', { summary: summary({ attendedDays: 20, scheduledDays: 20, presentDays: 20, lateDays: 1, unpaidMinutes: 30 }) }),
  )
  const requests = [request('a'), request('a', 'awaiting_cover'), request('b', 'approved'), request('ghost')]
  const all = m.monthTiles(data, requests)
  assert.equal(all.percentage, 87.5)
  assert.equal(all.presentDays, 34)
  assert.equal(all.late, 3)
  assert.equal(all.absent, 1)
  assert.equal(all.incomplete, 1)
  assert.equal(all.pendingRequests, 2)
  assert.equal(all.unpaidMinutes, 90)
  assert.equal(all.people, 2)
  const bala = m.monthTiles(data, requests, 'b')
  assert.equal(bala.percentage, 100)
  assert.equal(bala.pendingRequests, 0)
  assert.equal(m.monthTiles(null).percentage, null)
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

test('the overview CSV has one header line and one line per person, names with commas quoted', () => {
  const data = month(person('a', 'Asha, K', { summary: summary({ lateDays: 3, unpaidMinutes: 174 }) }))
  const csv = m.overviewCsv(m.buildOverviewRows(data, [request('a')]))
  const lines = csv.split('\r\n')
  assert.equal(lines.length, 2)
  assert.equal(lines[0], 'Employee,Tracked from,Attendance %,Days attended,Working days,Absent,Late,Incomplete,Requests,Unpaid time')
  assert.equal(lines[1], '"Asha, K",1 Nov 2026,100,17,17,0,3,0,1,2h 54m')
  assert.equal(m.overviewCsvName('2026-11'), 'attendance-overview-2026-11.csv')
})

test('the live board line under the status: logged in now, logged out, not clocked in, nothing on a day off', () => {
  const base = { stillIn: false, sessions: [], kind: 'absent' }
  assert.deepEqual(rows.presenceLabel({ ...base, stillIn: true, sessions: [{}] }), { label: 'Logged in now', live: true })
  assert.deepEqual(rows.presenceLabel({ ...base, sessions: [{}] }), { label: 'Logged out', live: false })
  assert.deepEqual(rows.presenceLabel({ ...base, kind: 'pending' }), { label: 'Not clocked in', live: false })
  assert.deepEqual(rows.presenceLabel({ ...base, kind: 'off' }), { label: '', live: false })
})
