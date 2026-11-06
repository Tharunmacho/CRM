import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'

async function loadSource(path) {
  const source = await readFile(new URL(path, import.meta.url), 'utf8')
  const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } })
  return import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`)
}

const lo = await loadSource('../src/features/dashboard/lead-overview.ts')
const sr = await loadSource('../src/features/staff-report/staff-report-model.ts')

const team = {
  scope: 'team', staffId: null, newToday: 3, notContacted: 2, followUpsDue: 1, resumesReceived: 1,
  converted: { week: 1, month: 1, quarter: 2, year: 2, all: 2 }, total: 10, assigned: 8, open: 6, convertedTotal: 2, conversionPercent: 25, unassigned: 1,
}
const mine = { ...team, scope: 'mine', staffId: 'ann', unassigned: null, conversionPercent: 20, assigned: 5 }

test('the team sees six cards including unassigned, staff see five', () => {
  assert.deepEqual(lo.leadCards(team, { period: 'month', gate: 'exempt' }).map(card => card.key),
    ['newToday', 'notContacted', 'followUpsDue', 'resumesReceived', 'converted', 'unassigned'])
  assert.deepEqual(lo.leadCards(mine, { period: 'month', gate: 'open' }).map(card => card.key),
    ['newToday', 'notContacted', 'followUpsDue', 'resumesReceived', 'converted'])
})

test('the converted card follows the chosen period and shows the conversion rate', () => {
  const quarter = lo.leadCards(team, { period: 'quarter', gate: 'exempt' }).find(card => card.key === 'converted')
  const week = lo.leadCards(team, { period: 'week', gate: 'exempt' }).find(card => card.key === 'converted')
  assert.equal(quarter.value, 2)
  assert.equal(week.value, 1)
  assert.match(quarter.detail, /25% rate/)
  assert.match(quarter.hint, /8 leads assigned/)
  assert.match(quarter.detail, /in the last 90 days/)
  const none = lo.leadCards({ ...team, conversionPercent: null, assigned: 0 }, { period: 'all', gate: 'exempt' }).find(card => card.key === 'converted')
  assert.match(none.detail, /^No rate yet/)
})

test('a locked staff member sees the lock hint on the work cards only', () => {
  const cards = lo.leadCards(mine, { period: 'month', gate: 'locked' })
  const by = Object.fromEntries(cards.map(card => [card.key, card]))
  for (const key of ['notContacted', 'followUpsDue', 'resumesReceived']) {
    assert.equal(by[key].locked, true, key)
    assert.equal(by[key].detail, "Finish today's tasks first")
  }
  assert.equal(by.newToday.locked, false)
  assert.equal(by.converted.locked, false)
  assert.equal(lo.isLocked('locked'), true)
  // Admin, manager, an open gate and a gate not yet known never show the hint.
  for (const gate of ['exempt', 'open', 'unknown']) assert.equal(lo.leadCards(mine, { period: 'month', gate }).some(card => card.locked), false, gate)
  // The numbers stay visible while locked.
  assert.equal(by.notContacted.value, 2)
})

test('cards that need attention are flagged and empty ones read as done', () => {
  const quiet = { ...team, notContacted: 0, followUpsDue: 0, resumesReceived: 0, unassigned: 0 }
  const cards = Object.fromEntries(lo.leadCards(quiet, { period: 'month', gate: 'exempt' }).map(card => [card.key, card]))
  assert.equal(cards.notContacted.warn, false)
  assert.equal(cards.notContacted.detail, 'Every assigned lead has had a call')
  assert.equal(cards.followUpsDue.detail, 'Nothing due')
  assert.equal(cards.unassigned.detail, 'Every open lead has an owner')
  const busy = Object.fromEntries(lo.leadCards(team, { period: 'month', gate: 'exempt' }).map(card => [card.key, card]))
  assert.equal(busy.notContacted.warn, true)
  assert.equal(busy.unassigned.warn, true)
  assert.match(busy.unassigned.detail, /assign in the Lead sheet/)
})

test('export rows carry every card and the rate, unassigned only for the team', () => {
  const rows = new Map(lo.leadCsvRows(team, 'quarter'))
  assert.equal(rows.get('Lead sheet: new leads today'), 3)
  assert.equal(rows.get('Lead sheet: not contacted'), 2)
  assert.equal(rows.get('Lead sheet: converted to candidates (in the last 90 days)'), 2)
  assert.equal(rows.get('Lead sheet: conversion rate (%)'), 25)
  assert.equal(rows.get('Lead sheet: unassigned leads'), 1)
  assert.equal(new Map(lo.leadCsvRows(mine, 'quarter')).has('Lead sheet: unassigned leads'), false)
  assert.equal(new Map(lo.leadCsvRows({ ...mine, conversionPercent: null }, 'all')).get('Lead sheet: conversion rate (%)'), '')
})

test('scope words, list path and list note', () => {
  assert.equal(lo.scopeWords(mine), 'you')
  assert.equal(lo.scopeWords({ scope: 'person' }, 'Asha'), 'Asha')
  assert.equal(lo.scopeWords({ scope: 'person' }), 'the chosen recruiter')
  assert.equal(lo.scopeWords(team), 'the whole team')
  assert.equal(lo.listPath('notContacted', 'month', ''), '/dashboard/leads?card=notContacted')
  assert.equal(lo.listPath('converted', 'week', 'u 1'), '/dashboard/leads?card=converted&period=week&staff_id=u+1')
  assert.equal(lo.listNote(8, 8), null)
  assert.match(lo.listNote(120, 8), /first 8 of 120/)
})

test('date labels are relative to today', () => {
  const now = new Date(2026, 10, 5, 14, 0).getTime()
  const at = (day, hour = 9) => new Date(2026, 10, day, hour, 0).toISOString()
  assert.equal(lo.whenLabel(at(5), now), 'Today')
  assert.equal(lo.whenLabel(at(4, 23), now), 'Yesterday')
  assert.equal(lo.whenLabel(at(2), now), '3 days ago')
  assert.equal(lo.whenLabel(at(6), now), 'Tomorrow')
  assert.equal(lo.whenLabel(at(9), now), 'in 4 days')
  assert.equal(lo.whenLabel(null, now), '')
  assert.equal(lo.leadSubline({ position: 'Chef', country: 'Dubai' }), 'Chef · Dubai')
  assert.equal(lo.leadSubline({ position: null, country: null }), 'No position yet')
})

/* ------------------------------------------------------------------ weekly staff report */

const lead = (over = {}) => ({ available: true, assigned: 4, contacted: 3, notContacted: 1, calls: 5, worked: 4, interested: 2, resumesReceived: 1, converted: 1, conversionPercent: 25, followUpsOverdue: 1, open: 4, ...over })
const row = (id, name, leads) => ({ id, name, score: 50, attendance: { hoursWorked: 0, absentDays: 0 }, tasks: { completionPercent: null }, stages: { moves: 0 },
  outcomes: { forwarded: 0, visaReceived: 0, deployed: 0 }, stuck: { count: 0 }, ...(leads === undefined ? {} : { leads }) })

test('lead columns sort by their numbers and put people without lead data last', () => {
  const rows = [row('a', 'Asha', lead({ assigned: 4, calls: 5, converted: 1, followUpsOverdue: 1 })), row('b', 'Ravi', lead({ assigned: 9, calls: 2, converted: 0, followUpsOverdue: 7 })),
    row('c', 'Meena', { ...lead(), available: false }), row('d', 'Old', undefined)]
  const names = (key) => sr.sortStaff(rows, key, 'desc').map(r => r.name)
  assert.deepEqual(names('leadsAssigned'), ['Ravi', 'Asha', 'Meena', 'Old'])
  assert.deepEqual(names('leadCalls'), ['Asha', 'Ravi', 'Meena', 'Old'])
  assert.deepEqual(names('leadsConverted'), ['Asha', 'Ravi', 'Meena', 'Old'])
  assert.deepEqual(names('leadsOverdue'), ['Ravi', 'Asha', 'Meena', 'Old'])
})

test('lead wording and tone', () => {
  assert.equal(sr.leadsCalledText({ assigned: 4, contacted: 3 }), '3 of 4 called')
  assert.equal(sr.leadsCalledText({ assigned: 0, contacted: 0 }), 'None assigned')
  assert.equal(sr.leadsTone(undefined), 'muted')
  assert.equal(sr.leadsTone({ assigned: 0, contacted: 0 }), 'muted')
  assert.equal(sr.leadsTone({ assigned: 4, contacted: 0 }), 'bad')
  assert.equal(sr.leadsTone({ assigned: 4, contacted: 1 }), 'warn')
  assert.equal(sr.leadsTone({ assigned: 4, contacted: 2 }), 'good')
  assert.equal(sr.hasLeadSheet({ sources: { dailyTasks: {}, leadSheet: { available: true } } }), true)
  assert.equal(sr.hasLeadSheet({ sources: { dailyTasks: {} } }), false)
})

test('team lead cards come from the summary and vanish when the lead tables are missing', () => {
  const summary = { leadsAvailable: true, leadsWorked: 4, leadsCalls: 5, leadsAssigned: 7, leadsContacted: 3, leadsConverted: 1, leadsConversionPercent: 25, leadsFollowUpsOverdue: 2 }
  const cards = sr.leadSummaryCards(summary)
  assert.equal(cards.worked.value, '4')
  assert.equal(cards.worked.detail, '5 calls logged, 7 assigned, 3 of those called')
  assert.equal(cards.converted.value, '1')
  assert.equal(cards.converted.detail, '25% of leads worked, 2 follow-ups overdue')
  assert.equal(sr.leadSummaryCards({ ...summary, leadsAvailable: false }), null)
  assert.equal(sr.leadSummaryCards({}), null)
  assert.equal(sr.leadSummaryCards({ ...summary, leadsConversionPercent: null, leadsFollowUpsOverdue: 0, leadsCalls: 1 }).converted.detail, 'No conversion rate yet')
})
