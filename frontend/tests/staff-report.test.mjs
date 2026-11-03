import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'

const source = await readFile(new URL('../src/features/staff-report/staff-report-model.ts', import.meta.url), 'utf8')
const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } })
const m = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`)

const staff = (over = {}) => ({
  id: 'a', name: 'Asha', score: 70,
  attendance: { hoursWorked: 20, absentDays: 0 },
  tasks: { completionPercent: 60 },
  stages: { moves: 5 },
  outcomes: { forwarded: 1, visaReceived: 0, deployed: 0 },
  stuck: { count: 0 },
  ...over,
})

test('weeks run Monday to Sunday and move by whole weeks', () => {
  assert.equal(m.mondayOf('2026-11-05'), '2026-11-02') // Thursday
  assert.equal(m.mondayOf('2026-11-02'), '2026-11-02')
  assert.equal(m.mondayOf('2026-11-08'), '2026-11-02') // Sunday belongs to the week that began on Monday
  assert.deepEqual(m.weekOf('2026-11-08'), { from: '2026-11-02', to: '2026-11-08' })
  assert.deepEqual(m.shiftRange({ from: '2026-11-02', to: '2026-11-08' }, -1), { from: '2026-10-26', to: '2026-11-01' })
  assert.deepEqual(m.shiftRange({ from: '2026-12-28', to: '2027-01-03' }, 1), { from: '2027-01-04', to: '2027-01-10' }) // across the new year
  assert.deepEqual(m.shiftRange({ from: '2026-11-03', to: '2026-11-05' }, 1), { from: '2026-11-06', to: '2026-11-08' }) // a custom range moves by its length
  assert.equal(m.isWholeWeek({ from: '2026-11-02', to: '2026-11-08' }), true)
  assert.equal(m.isWholeWeek({ from: '2026-11-03', to: '2026-11-09' }), false)
})

test('day arithmetic is calendar based and validates input', () => {
  assert.equal(m.addDays('2026-02-27', 2), '2026-03-01')
  assert.equal(m.addDays('2028-02-28', 1), '2028-02-29')
  assert.equal(m.validDay('2026-11-31'), false)
  assert.equal(m.validDay('2026-11-30'), true)
  assert.equal(m.validDay('11/30/2026'), false)
  assert.equal(m.validDay(null), false)
})

test('labels read plainly', () => {
  assert.equal(m.rangeLabel({ from: '2026-11-02', to: '2026-11-08' }), '2 Nov to 8 Nov 2026')
  assert.equal(m.rangeLabel({ from: '2026-11-02', to: '2026-11-02' }), '2 Nov 2026')
  assert.equal(m.formatHours(8.25), '8.3 h')
  assert.equal(m.formatHours(null), '-')
  assert.equal(m.formatSeconds(0), '0 h')
  assert.equal(m.formatSeconds(3600 * 8 + 1800), '8 h 30 m')
  assert.equal(m.formatSeconds(45 * 60), '45 m')
  assert.equal(m.formatPercent(60), '60%')
  assert.equal(m.formatPercent(null), '-')
})

test('score tone bands and severity order', () => {
  assert.equal(m.scoreTone(null), 'muted')
  assert.equal(m.scoreTone(75), 'good')
  assert.equal(m.scoreTone(74), 'warn')
  assert.equal(m.scoreTone(50), 'warn')
  assert.equal(m.scoreTone(49), 'bad')
  assert.ok(m.severityRank('high') < m.severityRank('medium') && m.severityRank('medium') < m.severityRank('low'))
  assert.equal(m.statusTone('no_attendance'), 'bad')
  assert.equal(m.statusTone('not_scheduled'), 'muted')
  assert.equal(m.stateTone('absent'), 'bad')
})

test('sorting puts people with no score last when descending and keeps names as the tie break', () => {
  const rows = [staff({ id: 'b', name: 'Ravi', score: null }), staff({ id: 'a', name: 'Asha', score: 70 }), staff({ id: 'c', name: 'Meena', score: 70 })]
  assert.deepEqual(m.sortStaff(rows, 'score', 'desc').map(r => r.name), ['Asha', 'Meena', 'Ravi'])
  assert.deepEqual(m.sortStaff(rows, 'score', 'asc').map(r => r.name), ['Ravi', 'Asha', 'Meena'])
  assert.deepEqual(m.sortStaff(rows, 'name', 'asc').map(r => r.name), ['Asha', 'Meena', 'Ravi'])
  const hours = [staff({ name: 'A', attendance: { hoursWorked: 2 } }), staff({ name: 'B', attendance: { hoursWorked: 9 } })]
  assert.deepEqual(m.sortStaff(hours, 'hours', 'desc').map(r => r.name), ['B', 'A'])
  assert.equal(rows[0].name, 'Ravi') // the input is not reordered
})

test('candidate filters', () => {
  const items = [
    { status: 'active', daysInStage: 20, movedInRange: 0 }, { status: 'active', daysInStage: 2, movedInRange: 2 },
    { status: 'closed', daysInStage: null, movedInRange: 1 }, { status: 'deployed', daysInStage: null, movedInRange: 0 },
  ]
  assert.equal(m.filterCandidates(items, 'all', 7).length, 4)
  assert.equal(m.filterCandidates(items, 'active', 7).length, 2)
  assert.equal(m.filterCandidates(items, 'stuck', 7).length, 1)
  assert.equal(m.filterCandidates(items, 'moved', 7).length, 2)
  assert.equal(m.filterCandidates(items, 'closed', 7).length, 1)
  assert.equal(m.filterCandidates(items, 'deployed', 7).length, 1)
})

test('funnel bars are relative to the first stage and never vanish', () => {
  assert.equal(m.funnelWidth(50, 100), 50)
  assert.equal(m.funnelWidth(1, 1000), 2)
  assert.equal(m.funnelWidth(0, 100), 0)
  assert.equal(m.funnelWidth(5, 0), 0)
  assert.equal(m.funnelWidth(120, 100), 100)
})

test('task data counts only when the tables exist and rows were allocated', () => {
  assert.equal(m.hasTaskData({ sources: { dailyTasks: { available: false, hasData: false } } }), false)
  assert.equal(m.hasTaskData({ sources: { dailyTasks: { available: true, hasData: false } } }), false)
  assert.equal(m.hasTaskData({ sources: { dailyTasks: { available: true, hasData: true } } }), true)
})
