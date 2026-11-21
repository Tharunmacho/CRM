import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'

/*
 * The Reports attendance monitor: the day's check-ins and the month's policy record become one row per person, with a status pill,
 * the shift, first login, last logout, hours and late/early minutes; the five counts above the table come from those rows.
 */

const moduleUrl = (source) => {
  const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } })
  return `data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`
}
const read = (path) => readFile(new URL(path, import.meta.url), 'utf8')
const m = await import(moduleUrl(await read('../src/features/daily-report/attendance-rows.ts')))

const DAY = '2026-11-20'
const TODAY = '2026-11-21'
const presence = (over = {}) => ({ status: 'offline', tracked: true, lastSeenAt: null, activeSeconds: 0, idleSeconds: 0, offlineSeconds: 0, sessions: [], ...over })
const session = (id, from, to) => ({ id, checkedInAt: `${DAY}T${from}:00Z`, checkedOutAt: to ? `${DAY}T${to}:00Z` : null, durationSeconds: to ? (Date.parse(`${DAY}T${to}:00Z`) - Date.parse(`${DAY}T${from}:00Z`)) / 1000 : 600 })
const person = (userId, name, sessions = [], over = {}) => {
  const open = sessions.some((s) => !s.checkedOutAt)
  const closed = sessions.filter((s) => s.checkedOutAt)
  return {
    userId, name, role: 'staff', active: true,
    attendance: { status: open ? 'checked_in' : 'checked_out', checkedInAt: null, lastCheckedOutAt: closed.length ? closed[closed.length - 1].checkedOutAt : null, totalSeconds: sessions.reduce((t, s) => t + s.durationSeconds, 0), sessions },
    presence: presence(),
    ...over,
  }
}
const attendance = (...staff) => ({ date: DAY, timezone: 'Asia/Kolkata', canViewTeam: true, generatedAt: `${TODAY}T00:00:00Z`, clockContext: null, staff })
const policyDay = (status, over = {}) => ({ date: DAY, status, checkIn: null, checkOut: null, shift: { start: '09:30', end: '18:30', breakMinutes: 60 }, requiredMinutes: 480, workedMinutes: 0, lateMinutes: 0, earlyMinutes: 0, provisional: false, ...over })
const month = (...entries) => ({ year: 2026, month: 11, timezone: 'Asia/Kolkata', canManage: true, canCorrect: true, policy: {}, staff: entries.map(([userId, day]) => ({ userId, name: userId, days: [day] })) })
const build = (att, mon, extra = {}) => m.buildAttendanceRows({ day: DAY, today: TODAY, attendance: att, month: mon, ...extra })

test('a present day: policy status, shift, first login, last logout and hours', () => {
  const [row] = build(attendance(person('u1', 'Asha', [session('s1', '04:00', '13:00')])), month(['u1', policyDay('P')]))
  assert.equal(row.kind, 'present')
  assert.equal(row.label, 'Present')
  assert.equal(row.tone, 'green')
  assert.equal(row.shift, '09:30 – 18:30')
  assert.equal(row.firstIn, `${DAY}T04:00:00Z`)
  assert.equal(row.lastOut, `${DAY}T13:00:00Z`)
  assert.equal(row.stillIn, false)
  assert.equal(row.workedSeconds, 9 * 3600)
  assert.equal(m.punctualityText(row), '')
})

test('a late day carries the late minutes (and early minutes) as text', () => {
  const [row] = build(attendance(person('u1', 'Asha', [session('s1', '04:45', '13:00')])), month(['u1', policyDay('LT', { lateMinutes: 15, earlyMinutes: 75 })]))
  assert.equal(row.kind, 'late')
  assert.equal(row.label, 'Late')
  assert.equal(row.lateMinutes, 15)
  assert.equal(m.punctualityText(row), 'Late 15m · Early 1h 15m')
  assert.equal(m.punctualityText({ lateMinutes: 0, earlyMinutes: 5 }), 'Early 5m')
})

test('absent on a past day, not clocked in yet today, weekly off, holiday and leave', () => {
  const nobody = attendance(person('u1', 'Asha'))
  assert.equal(build(nobody, month(['u1', policyDay('A')]))[0].kind, 'absent')
  assert.equal(build(nobody, month(['u1', policyDay('A')]))[0].label, 'Absent')
  assert.equal(build(nobody, null)[0].kind, 'absent')
  const today = { day: TODAY, today: TODAY }
  assert.equal(m.buildAttendanceRows({ ...today, attendance: nobody, month: month(['u1', policyDay('A', { date: TODAY })]) })[0].label, 'Not clocked in yet')
  assert.equal(m.buildAttendanceRows({ ...today, attendance: nobody, month: null })[0].label, 'Not clocked in yet')
  const off = build(nobody, month(['u1', policyDay('WO')]))[0]
  assert.deepEqual([off.kind, off.label, off.shift], ['off', 'Weekly off', ''])
  assert.equal(build(nobody, month(['u1', policyDay('H')]))[0].label, 'Holiday')
  assert.equal(build(nobody, month(['u1', policyDay('PL')]))[0].label, 'Paid leave')
  assert.equal(build(nobody, month(['u1', policyDay('UL')]))[0].kind, 'leave')
  assert.equal(build(nobody, month(['u1', policyDay('WFH')]))[0].label, 'Work from home')
})

test('still logged in: an open session means here, with no last logout, even when the policy says absent', () => {
  const [row] = build(attendance(person('u1', 'Asha', [session('s1', '04:00', null)])), month(['u1', policyDay('A', { provisional: true })]))
  assert.equal(row.stillIn, true)
  assert.equal(row.lastOut, null)
  assert.equal(row.kind, 'present')
  assert.equal(m.attendanceCounts([row]).stillIn, 1)
})

test('without policy data the status comes from the sessions', () => {
  const rows = build(attendance(person('u1', 'Asha', [session('s1', '04:00', '12:00')]), person('u2', 'Ben')), null)
  assert.deepEqual(rows.map((r) => [r.name, r.kind, r.shift, r.fromPolicy]), [['Asha', 'present', '', false], ['Ben', 'absent', '', false]])
  assert.equal(build(null, null).length, 0)
})

test('several sessions: ordered pairs, first login of the first, last logout of the last, total hours', () => {
  const sessions = [session('s2', '09:00', '12:30'), session('s1', '04:00', '08:00')]
  const [row] = build(attendance(person('u1', 'Asha', sessions)), null)
  assert.equal(row.sessions.length, 2)
  assert.equal(row.firstIn, `${DAY}T04:00:00Z`)
  assert.equal(row.lastOut, `${DAY}T12:30:00Z`)
  assert.equal(row.workedSeconds, 7.5 * 3600)
  assert.deepEqual(row.sessions.map((s) => s.checkedInAt.slice(11, 16)), ['04:00', '09:00'])
})

test('presence figures come through; untracked people are flagged as such', () => {
  const tracked = person('u1', 'Asha', [session('s1', '04:00', '08:00')], { presence: presence({ activeSeconds: 7200, idleSeconds: 600 }) })
  const manager = person('u2', 'Dev', [], { role: 'manager', presence: presence({ tracked: false }) })
  const rows = build(attendance(tracked, manager), null)
  assert.deepEqual([rows[0].tracked, rows[0].activeSeconds, rows[0].idleSeconds], [true, 7200, 600])
  assert.equal(rows[1].tracked, false)
})

test('counts: present includes late, plus absent, leave and still logged in', () => {
  const att = attendance(
    person('a', 'A', [session('s', '04:00', '13:00')]), person('b', 'B', [session('s', '05:00', '13:00')]), person('c', 'C'),
    person('d', 'D'), person('e', 'E', [session('s', '04:00', null)]))
  const mon = month(['a', policyDay('P')], ['b', policyDay('LT', { lateMinutes: 30 })], ['c', policyDay('A')], ['d', policyDay('PL')], ['e', policyDay('P')])
  assert.deepEqual(m.attendanceCounts(build(att, mon)), { present: 3, late: 1, absent: 1, leave: 1, stillIn: 1, total: 5 })
})

test('who is listed: one person when narrowed, admins and idle inactive accounts left out of the team view, ordered by name', () => {
  const att = attendance(person('z', 'Zed'), person('b', 'Bea', [session('s', '04:00', '08:00')]), person('adm', 'Admin', [], { role: 'admin' }), person('x', 'Old', [], { active: false }))
  assert.deepEqual(build(att, null).map((r) => r.name), ['Bea', 'Zed'])
  assert.deepEqual(build(att, null, { staff: 'adm' }).map((r) => r.name), ['Admin'])
  assert.deepEqual(build(attendance(person('adm', 'Admin', [], { role: 'admin' })), null).map((r) => r.name), ['Admin'])
})

test('labels', () => {
  assert.equal(m.kindLabel('pending'), 'Not clocked in yet')
  assert.equal(m.kindLabel('early'), 'Early exit')
  assert.equal(m.kindLabel('leave'), 'On leave')
  assert.equal(m.kindTone('absent'), 'red')
  assert.equal(m.shiftLabel({ start: '09:30:00', end: '18:30:00' }), '09:30 – 18:30')
  assert.equal(m.shiftLabel({ start: '', end: '' }), '')
  assert.equal(m.minutesText(45), '45m')
  assert.equal(m.minutesText(125), '2h 05m')
  assert.equal(m.kindOfCode('MP'), 'missed')
  assert.equal(m.kindOfCode('??'), null)
})

test('"Missed check-out" is only for someone who logged in: nobody who has not clocked in is missed, and the Present count agrees', () => {
  // today, no login, the policy says missed punch: "not clocked in yet", not present
  const today = m.buildAttendanceRows({ day: TODAY, today: TODAY, attendance: { ...attendance(person('u1', 'Asha')), date: TODAY },
    month: month(['u1', policyDay('MP', { date: TODAY })]) })
  assert.equal(today[0].kind, 'pending')
  assert.equal(m.attendanceCounts(today).present, 0)
  // a finished day without any login is absent
  const [past] = build(attendance(person('u1', 'Asha')), month(['u1', policyDay('MP')]))
  assert.equal(past.kind, 'absent')
  // logged in today and still in: present, nothing missed yet
  const live = m.buildAttendanceRows({ day: TODAY, today: TODAY, attendance: { ...attendance(person('u2', 'Ravi', [{ ...session('s1', '04:00', null), checkedInAt: `${TODAY}T04:00:00Z` }])), date: TODAY },
    month: month(['u2', policyDay('MP', { date: TODAY })]) })
  assert.equal(live[0].kind, 'present')
  assert.equal(m.attendanceCounts(live).present, 1)
  assert.equal(m.attendanceCounts(live).stillIn, 1)
  // logged in and out on a finished day but the policy flags a missed punch: still shown as missed, and counted as attended
  const [missed] = build(attendance(person('u3', 'Meena', [session('s1', '04:00', '05:00')])), month(['u3', policyDay('MP')]))
  assert.equal(missed.kind, 'missed')
  assert.equal(m.attendanceCounts([missed]).present, 1)
})

test('who is listed on the live board: the MD and recruiters, never the Administrator account', () => {
  const att = attendance(person('md', 'Mahesh', [session('s', '04:00', '08:00')], { role: 'manager' }), person('r', 'Ravi'), person('adm', 'Root', [session('t', '04:00', '08:00')], { role: 'admin' }))
  const names = build(att, null).map((r) => r.name)
  assert.deepEqual(names, ['Mahesh', 'Ravi'])
  assert.equal(m.attendanceCounts(build(att, null)).total, 2)
})
