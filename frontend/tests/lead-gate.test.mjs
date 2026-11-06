import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'

async function load(path) {
  const source = await readFile(new URL(path, import.meta.url), 'utf8')
  const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText
  return import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`)
}

const g = await load('../src/features/leads/lead-gate.ts')

const locked = { locked: true, open: 3, total: 5, postponed: 1, nextDueAt: '2026-11-06T06:30:00+00:00', message: 'Finish today\'s tasks first.' }
const open = { locked: false, open: 0, total: 5, postponed: 0, nextDueAt: null, message: null }

test('admin and manager are exempt whatever the gate says', () => {
  assert.equal(g.gateState('admin', locked), 'exempt')
  assert.equal(g.gateState('manager', locked), 'exempt')
  assert.equal(g.gateBlocks(g.gateState('admin', locked)), false)
  assert.equal(g.gateReason(g.gateState('manager', locked)), null)
})

test('staff: locked, open, or unknown while the gate has not loaded (the server still decides)', () => {
  assert.equal(g.gateState('staff', locked), 'locked')
  assert.equal(g.gateState('staff', open), 'open')
  assert.equal(g.gateState('staff', null), 'unknown')
  assert.equal(g.gateBlocks('locked'), true)
  assert.equal(g.gateBlocks('open'), false)
  assert.equal(g.gateBlocks('unknown'), false)
  assert.equal(g.gateReason('locked'), "Finish today's tasks first")
  assert.equal(g.gateReason('open'), null)
})

test('gate from the daily-work summary the shell already loads', () => {
  assert.deepEqual(g.gateFromDailySummary({ total: 5, open: 2, postponed: 1, complete: false, nextDueAt: 'x' }), { locked: true, open: 2, total: 5, postponed: 1, nextDueAt: 'x' })
  assert.equal(g.gateFromDailySummary({ total: 5, open: 0, postponed: 0, complete: true }).locked, false)
  assert.equal(g.gateFromDailySummary({ total: 0, open: 0, postponed: 0, complete: false }).locked, false)
  assert.equal(g.gateFromDailySummary(null), null)
})

test('progress, postponed and summary wording', () => {
  assert.deepEqual(g.gateProgress({ open: 3, total: 5 }), { done: 2, total: 5, percent: 40, label: '2 of 5 done' })
  assert.deepEqual(g.gateProgress({ open: 9, total: 5 }), { done: 0, total: 5, percent: 0, label: '0 of 5 done' })
  assert.equal(g.gateProgress({ open: 0, total: 0 }).percent, 0)
  assert.equal(g.gatePostponedLabel(0), null)
  assert.equal(g.gatePostponedLabel(2), '2 carried over from earlier days')
  assert.equal(g.gateSummaryText({ open: 1, total: 5, postponed: 0 }), '1 task left today.')
  assert.equal(g.gateSummaryText({ open: 3, total: 5, postponed: 1 }), '3 tasks left today. 1 carried over from earlier days.')
})

test('deadline countdown before and after the deadline', () => {
  const due = '2026-11-06T06:30:00+00:00'
  const t = Date.parse(due)
  assert.deepEqual(g.deadlineCountdown(due, t - 42 * 60_000), { label: 'Next deadline in 42m', late: false })
  assert.deepEqual(g.deadlineCountdown(due, t - 65 * 60_000), { label: 'Next deadline in 1h 05m', late: false })
  assert.deepEqual(g.deadlineCountdown(due, t + 15 * 60_000), { label: 'Deadline passed 15m ago', late: true })
  assert.equal(g.deadlineCountdown(null, t), null)
  assert.equal(g.deadlineCountdown('nope', t), null)
  // a skewed browser clock is corrected by the server offset
  assert.equal(g.deadlineCountdown(due, t - 100 * 60_000, 60 * 60_000).label, 'Next deadline in 40m')
})

test('the sidebar store publishes once per real change and is scoped to the user', () => {
  let calls = 0
  const stop = g.subscribeGate(() => { calls += 1 })
  g.publishGate('u1', g.gateFromDailySummary({ total: 5, open: 2, postponed: 0, complete: false }))
  assert.equal(calls, 1)
  assert.equal(g.readGate('u1').locked, true)
  assert.equal(g.readGate('u2'), null)
  g.publishGate('u1', g.gateFromDailySummary({ total: 5, open: 2, postponed: 0, complete: false }))
  assert.equal(calls, 1) // same facts: nobody re-renders
  g.publishGate('u1', g.gateFromDailySummary({ total: 5, open: 0, postponed: 0, complete: true }))
  assert.equal(calls, 2)
  assert.equal(g.readGate('u1').locked, false)
  stop()
  g.publishGate('u1', null)
  assert.equal(calls, 2)
})

test('the banner body drops the sentence the heading already says', () => {
  assert.equal(g.gateBody({ open: 3, total: 5, postponed: 1, message: "Finish today's tasks first. You have 3 daily tasks left (1 postponed). Then you can work the lead sheet." }), 'You have 3 daily tasks left (1 postponed). Then you can work the lead sheet.')
  assert.equal(g.gateBody({ open: 1, total: 5, postponed: 0, message: null }), '1 task left today. The lead sheet is read-only until your daily tasks are complete.')
  assert.equal(g.gateBody({ open: 1, total: 5, postponed: 0, message: "Finish today's tasks first." }).startsWith('1 task left today.'), true)
})
