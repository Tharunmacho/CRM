import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'

const source = await readFile(new URL('../src/features/daily-work/daily-work-model.ts', import.meta.url), 'utf8')
const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } })
const m = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`)

const MIN = 60_000
const NOW = Date.parse('2026-11-03T05:30:00Z') // 11:00 in Asia/Kolkata

const item = (over = {}) => ({ status: 'open', dueAt: '2026-11-03T06:30:00Z', ...over })
const summary = (over = {}) => ({
  total: 5, done: 2, open: 3, postponed: 0, overdue: 0, morning: { done: 2, total: 3 }, evening: { done: 0, total: 2 },
  nextDueAt: null, oldestPostponedDays: 0, complete: false, ...over,
})

test('countdowns read as time left before the slot and as lateness after it', () => {
  assert.equal(m.spanLabel(30_000), '<1m')
  assert.equal(m.spanLabel(42 * MIN), '42m')
  assert.equal(m.spanLabel(65 * MIN), '1h 05m')
  assert.equal(m.spanLabel(26 * 60 * MIN), '1d 2h')
  assert.equal(m.countdownLabel(42 * MIN), 'in 42m')
  assert.equal(m.countdownLabel(-15 * MIN), '15m overdue')
  assert.equal(m.countdownLabel(0), '<1m overdue')
})

test('the timer goes amber in the last quarter hour and red once the hourly deadline passes', () => {
  assert.equal(m.dueTone(item(), NOW), 'ok') // an hour left
  assert.equal(m.dueTone(item({ dueAt: '2026-11-03T05:44:59Z' }), NOW), 'soon')
  assert.equal(m.dueTone(item({ dueAt: '2026-11-03T05:30:00Z' }), NOW), 'over') // exactly at the deadline is late
  assert.equal(m.dueTone(item({ dueAt: '2026-11-02T10:30:00Z' }), NOW), 'over') // yesterday's slot
  assert.equal(m.dueTone(item({ status: 'done', dueAt: '2026-11-02T10:30:00Z' }), NOW), 'done')
})

test('a wrong PC clock is corrected by the server time offset', () => {
  const received = NOW + 3 * 60 * MIN // the PC is three hours fast
  const skew = m.clockSkew('2026-11-03T05:30:00Z', received)
  assert.equal(skew, -3 * 60 * MIN)
  assert.equal(m.msUntil('2026-11-03T06:30:00Z', received, skew), 60 * MIN)
  assert.equal(m.clockSkew('not a date', received), 0)
})

test('progress wording: morning and evening, postponed age and percent done', () => {
  assert.equal(m.sessionProgress(summary()), 'Morning 2/3 · Evening 0/2')
  assert.equal(m.percentDone(summary()), 40)
  assert.equal(m.percentDone({ done: 0, total: 0 }), 0)
  assert.equal(m.postponedLabel(1), 'Postponed 1 day')
  assert.equal(m.postponedLabel(3), 'Postponed 3 days')
  assert.equal(m.postponedLabel(0), 'Postponed')
  assert.equal(m.sessionLabel('morning'), 'Morning')
  assert.equal(m.sessionLabel('evening'), 'Evening')
})

test('carry-over example: finishing 4 of 5 leaves a list of 6 that the banner explains', () => {
  const next = summary({ total: 6, done: 0, open: 6, postponed: 1, overdue: 1, oldestPostponedDays: 1 })
  assert.equal(m.promptState(next), 'overdue')
  assert.equal(m.promptMessage(next), '6 candidates left to finish today (0 of 6 done). 1 carried over from earlier days. 1 past its hourly deadline.')
  assert.equal(m.promptState(summary({ postponed: 1 })), 'postponed')
  assert.equal(m.promptState(summary()), 'open')
})

test('the banner stays silent when there is no work or everything is finished', () => {
  assert.equal(m.promptState(null), 'none')
  assert.equal(m.promptState(summary({ total: 0, done: 0, open: 0 })), 'none')
  assert.equal(m.promptState(summary({ done: 5, open: 0, complete: true })), 'done')
  assert.equal(m.promptMessage(summary({ total: 5, done: 4, open: 1 })).startsWith('1 candidate left to finish today (4 of 5 done).'), true)
})

test('deadlines display on the agency clock, not the browser clock', () => {
  assert.equal(m.clockTime('2026-11-03T05:30:00Z', 'Asia/Kolkata'), '11:00')
  assert.equal(m.clockTime('2026-11-03T05:30:00Z', 'UTC'), '05:30')
  assert.equal(m.clockTime('bad'), '--:--')
})

test('supervisors see the staff furthest behind first, and idle staff last', () => {
  const staff = [
    { userId: 'a', name: 'Asha', active: true, summary: summary({ overdue: 0, postponed: 0, open: 1 }) },
    { userId: 'b', name: 'Bala', active: true, summary: summary({ overdue: 2, postponed: 1, open: 4 }) },
    { userId: 'c', name: 'Chitra', active: true, summary: summary({ total: 0, done: 0, open: 0 }) },
    { userId: 'd', name: 'Dev', active: true, summary: summary({ overdue: 0, postponed: 2, open: 3 }) },
  ]
  assert.deepEqual(m.staffNeedingAttention(staff).map((s) => s.name), ['Bala', 'Dev', 'Asha', 'Chitra'])
})

test('the referral column names the referrer, otherwise how the CV arrived', () => {
  assert.equal(m.referralLabel({ source: 'referral', label: 'Referral', detail: ' Suresh Kumar ' }), 'Suresh Kumar')
  assert.equal(m.referralLabel({ source: 'referral', label: 'Referral', detail: null }), 'Referral')
  assert.equal(m.referralLabel({ source: 'whatsapp', label: 'WhatsApp', detail: null }), 'WhatsApp')
})

test('goal bars cap at 100 percent and a zero goal means no goal', () => {
  assert.equal(m.goalPercent(2, 5), 40)
  assert.equal(m.goalPercent(7, 5), 100)
  assert.equal(m.goalPercent(3, 0), 0)
  assert.equal(m.goalState(5, 5), 'done')
  assert.equal(m.goalState(2, 5), 'progress')
  assert.equal(m.goalState(2, 0), 'none')
})

test('the pace message compares hourly checkpoints with what is finished', () => {
  const cp = (expectedByNow, doneToday) => ({ points: [{}], expectedByNow, doneToday, behindBy: Math.max(0, expectedByNow - doneToday) })
  assert.equal(m.pacingMessage(null), '')
  assert.equal(m.pacingMessage(cp(0, 0)), '') // before the first deadline there is nothing to judge
  assert.equal(m.pacingMessage(cp(2, 2)), 'On the hourly pace.')
  assert.equal(m.pacingMessage(cp(2, 3)), 'Ahead of the hourly pace: 3 done, 2 due by now.')
  assert.equal(m.pacingMessage(cp(1, 0)), 'Behind the hourly pace: 1 candidate should be finished by now, 0 done.')
  assert.equal(m.pacingMessage(cp(4, 2)), 'Behind the hourly pace: 4 candidates should be finished by now, 2 done.')
  assert.equal(m.checkpointLabel('late'), 'Finished late')
})

test('the screen re-reads exactly when the next open deadline passes', () => {
  const items = [
    { status: 'done', dueAt: '2026-11-03T05:40:00Z' },
    { status: 'open', dueAt: '2026-11-03T06:00:00Z' },
    { status: 'open', dueAt: '2026-11-03T05:00:00Z' }, // already late: not a future crossing
    { status: 'open', dueAt: '2026-11-03T07:00:00Z' },
  ]
  assert.equal(m.nextCrossing(items, NOW), 30 * MIN)
  assert.equal(m.nextCrossing(items, NOW, 10 * MIN), 20 * MIN) // server clock ten minutes ahead
  assert.equal(m.nextCrossing([{ status: 'done', dueAt: '2026-11-03T09:00:00Z' }], NOW), null)
  assert.equal(m.nextCrossing([], NOW), null)
})

test('the quota card shows what each recruiter gets, and the sum only as a labelled total', () => {
  assert.deepEqual(m.quotaLine({ daily: 5, morning: 3, evening: 2 }, false), { value: '5', detail: '3 morning · 2 evening each' })
  assert.deepEqual(m.quotaLine({ daily: 4, morning: 2, evening: 2 }, true), { value: '~4', detail: '2 morning · 2 evening each (goals differ by person)' })
  assert.equal(m.teamTotalLine(10, 2), '10 across 2 recruiters today')
  assert.equal(m.teamTotalLine(5, 1), '5 across 1 recruiter today')
})

test('team progress names how many staff it covers', () => {
  assert.equal(m.progressLine({ done: 3, total: 10 }, 2, true), '3 of 10 done across 2 staff')
  assert.equal(m.progressLine({ done: 3, total: 5 }, 1, false), '3 of 5 done')
  assert.equal(m.progressLine({ done: 0, total: 0 }, 1, true), '0 of 0 done across 1 staff member')
})

test('averages read cleanly: one decimal at most', () => {
  assert.equal(m.fmtCount(2), '2')
  assert.equal(m.fmtCount(1.5), '1.5')
  assert.equal(m.fmtCount(0.333), '0.3')
})

test('the staff board explains an empty or short list instead of showing a bare 0/0', () => {
  const reason = 'No candidates assigned to this staff member. Assign candidates from the Data Bank.'
  assert.deepEqual(m.candidatesCell({ done: 0, total: 0 }, { target: 5, allocated: 0, reason }, false), { text: '0', warn: true, note: reason })
  assert.deepEqual(m.candidatesCell({ done: 0, total: 0 }, null, false), { text: '0', warn: true, note: 'No open candidates to allocate' })
  assert.deepEqual(m.candidatesCell({ done: 0, total: 0 }, null, true), { text: '-', warn: false, note: 'Day off' })
  assert.deepEqual(m.candidatesCell({ done: 1, total: 2 }, { target: 5, allocated: 2, reason: 'x' }, false), { text: '1/2', warn: true, note: 'Only 2 of 5 new candidates today' })
  assert.deepEqual(m.candidatesCell({ done: 1, total: 5 }, null, false), { text: '1/5', warn: false, note: '' })
})
