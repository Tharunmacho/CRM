import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'

/*
 * Reports > Attendance, real time and honest: today means today's logins and presence only, a shift that crosses midnight or was never checked
 * out is read by one rule, a page that stopped sending heartbeats stops showing as online, and the board never shows another day's answer.
 */
const moduleUrl = (source) => {
  const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } })
  return `data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`
}
const read = (path) => readFile(new URL(path, import.meta.url), 'utf8')
const rows = await import(moduleUrl(await read('../src/features/daily-report/attendance-rows.ts')))
const month = await import(moduleUrl((await read('../src/features/daily-report/attendance-month.ts')).replace("from './attendance-rows'", `from '${moduleUrl(await read('../src/features/daily-report/attendance-rows.ts'))}'`)))
const { answerFor } = await import(moduleUrl(await read('../src/features/daily-report/answer-for.ts')))

const ZONE = 'Asia/Kolkata'
const presence = (over = {}) => ({ status: 'offline', tracked: true, lastSeenAt: null, activeSeconds: 0, idleSeconds: 0, offlineSeconds: 0, sessions: [], ...over })
const person = (sessions, over = {}) => ({
  userId: 'u1', name: 'Asha', role: 'staff', active: true,
  attendance: { status: sessions.some((s) => !s.checkedOutAt) ? 'checked_in' : 'checked_out', checkedInAt: null, lastCheckedOutAt: null, totalSeconds: sessions.reduce((t, s) => t + s.durationSeconds, 0), sessions },
  presence: presence(), ...over,
})
const response = (day, ...staff) => ({ date: day, timezone: ZONE, canViewTeam: true, generatedAt: `${day}T12:00:00Z`, clockContext: null, staff })
const policyDay = (date, status, over = {}) => ({ date, status, checkIn: null, checkOut: null, shift: { start: '09:30', end: '18:30', breakMinutes: 60 }, requiredMinutes: 480, workedMinutes: 0, lateMinutes: 0, earlyMinutes: 0, provisional: false, ...over })
const monthOf = (...days) => ({ year: 2026, month: 11, timezone: ZONE, canManage: true, canCorrect: true, policy: {}, staff: [{ userId: 'u1', name: 'Asha', days }] })

test('dayInZone: the agency calendar day of a stamp, not the UTC day', () => {
  assert.equal(rows.dayInZone('2026-11-20T18:30:00Z', ZONE), '2026-11-21')   // 00:00 IST on the 21st
  assert.equal(rows.dayInZone('2026-11-20T18:29:00Z', ZONE), '2026-11-20')
  assert.equal(rows.dayInZone('2026-11-20T18:30:00Z', undefined), '')
  assert.equal(rows.dayInZone('not a time', ZONE), '')
})

test('a shift that crossed midnight is on the next day\'s list but is not that day\'s arrival: never "late" and its date is shown', () => {
  // 23:50 IST on 20 Nov to 00:20 IST on 21 Nov; the server clips it to 20 minutes on the 21st
  const crossed = { id: 's1', checkedInAt: '2026-11-20T18:20:00Z', checkedOutAt: '2026-11-20T18:50:00Z', durationSeconds: 1200 }
  const [row] = rows.buildAttendanceRows({ day: '2026-11-21', today: '2026-11-22', zone: ZONE, attendance: response('2026-11-21', person([crossed])), month: monthOf(policyDay('2026-11-21', 'P')) })
  assert.equal(row.carriedIn, true)
  assert.equal(row.lateMinutes, 0)
  assert.equal(row.kind, 'present')
  assert.equal(row.workedSeconds, 1200)
  // the 20th: it is that day's own login (and, against a 09:30 shift, a late one: it is what happened)
  const [eve] = rows.buildAttendanceRows({ day: '2026-11-20', today: '2026-11-22', zone: ZONE, attendance: response('2026-11-20', person([{ ...crossed, durationSeconds: 600 }])), month: monthOf(policyDay('2026-11-20', 'P')) })
  assert.equal(eve.carriedIn, false)
  assert.equal(eve.workedSeconds, 600)
  // the two days add up to the whole shift (30 minutes), the same minutes as the shift itself
  assert.equal(eve.workedSeconds + row.workedSeconds, 1800)
})

test('an open shift on a finished day is a missed check-out with no hours; on today it is "still logged in"', () => {
  const open = { id: 's1', checkedInAt: '2026-11-20T04:00:00Z', checkedOutAt: null, durationSeconds: 0, unclosed: true }
  const [past] = rows.buildAttendanceRows({ day: '2026-11-20', today: '2026-11-21', zone: ZONE, attendance: response('2026-11-20', person([open])), month: null })
  assert.deepEqual([past.stillIn, past.unclosed, past.kind, past.workedSeconds], [false, true, 'missed', 0])
  const live = { id: 's2', checkedInAt: '2026-11-21T04:00:00Z', checkedOutAt: null, durationSeconds: 3600 }
  const [now] = rows.buildAttendanceRows({ day: '2026-11-21', today: '2026-11-21', zone: ZONE, attendance: response('2026-11-21', person([live])), month: null })
  assert.deepEqual([now.stillIn, now.unclosed, now.workedSeconds], [true, false, 3600])
})

test('online is only as fresh as the last heartbeat: past 90 seconds it is not online even before the next read', () => {
  const seen = '2026-11-21T04:00:00Z'
  const at = (seconds) => Date.parse(seen) + seconds * 1000
  const live = person([], { presence: presence({ status: 'online', lastSeenAt: seen }) })
  const build = (nowMs) => rows.buildAttendanceRows({ day: '2026-11-21', today: '2026-11-21', zone: ZONE, attendance: response('2026-11-21', live), month: null, nowMs })[0]
  assert.equal(build(at(30)).online, 'online')
  assert.equal(build(at(90)).online, 'online')
  assert.equal(build(at(91)).online, null)
  assert.equal(build(at(600)).online, null)
  // with no clock given the server's reading is trusted (as before)
  assert.equal(build(undefined).online, 'online')
  assert.equal(rows.PRESENCE_OFFLINE_AFTER_SECONDS, 90)
})

test('the answer of another day is never shown: a new day (or month, or person) starts from "loading"', () => {
  const held = { key: '2026-11-20|', attendance: { staff: ['yesterday'] }, loading: false, error: '' }
  const empty = { attendance: null, loading: true, error: '' }
  assert.deepEqual(answerFor(held, '2026-11-21|', empty), empty)            // the day rolled over: yesterday's rows are not today's
  assert.deepEqual(answerFor(held, '2026-11-20|u2', empty), empty)          // another person
  assert.deepEqual(answerFor(held, '2026-11-20|', empty), { attendance: { staff: ['yesterday'] }, loading: false, error: '' })
})

test('a month cell with a login and no logout says "no check-out" on a finished day and "now" only while the day runs', () => {
  const clock = (value) => value.slice(11, 16)
  const date = { date: '2026-11-03', day: 3, weekday: 'T', weekend: false, future: false }
  const cell = { date: date.date, mark: 'M', workedMinutes: 0, lateMinutes: 0, checkIn: '2026-11-03T04:55:00Z', checkOut: null }
  assert.match(month.cellTitle('Asha', date, cell, clock, '2026-11-04'), /04:55 to no check-out/)
  assert.match(month.cellTitle('Asha', date, cell, clock, '2026-11-03'), /04:55 to now/)
  assert.match(month.cellTitle('Asha', date, cell, clock), /04:55 to now/)
})

test('real time: the board listens to attendance and users changes, polls quickly as a safety net, ticks its clock and follows midnight', async () => {
  const data = await read('../src/features/daily-report/use-attendance-data.ts')
  assert.match(data, /\['attendance', 'users'\]/)
  assert.match(data, /DAY_RATES: LiveRates = \{ downMs: 15_000, safetyMs: 20_000, eventGapMs: 0 \}/)   // every change is read at once, the safety net is 20 s
  assert.match(data, /import \{ answerFor \} from '\.\/answer-for'/)
  assert.match(data, /answerFor\(held, dayKey/)
  assert.match(data, /answerFor\(held, key/)
  const tab = await read('../src/features/daily-report/report-attendance-tab.tsx')
  assert.match(tab, /useNowMs\(\)/)
  assert.match(tab, /nowMs/)
  assert.match(tab, /followDay\(current, previous, today\)/)
  assert.match(tab, /day=\{date\}/)
  const clock = await read('../src/features/daily-report/use-agency-today.ts')
  assert.match(clock, /export function useNowMs\(intervalMs = 10_000\)/)
  assert.match(clock, /visibilitychange/)
})

test('the board shows no invented figures: no hours or online time for a shift that has none, the date on a time of another day', async () => {
  const board = await read('../src/features/daily-report/report-attendance.tsx')
  assert.match(board, /row\.tracked && row\.sessions\.length > 0 && !row\.unclosed \?/)         // no "0h 00m / 0h 00m" for someone who is not checked in
  assert.match(board, /!row\.unclosed && \(row\.sessions\.length \|\| row\.workedSeconds > 0\)/)
  assert.match(board, /Not checked out/)
  assert.match(board, /export function boardClock/)
  assert.match(board, /title="Time logged in inside this calendar day/)
})
