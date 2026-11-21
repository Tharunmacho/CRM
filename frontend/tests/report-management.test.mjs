import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'

/* The management reading of the Task report and the Lead sheet report: card figures with the previous period, stuck and aging marks, conversion, and the layout rules. */

const moduleUrl = (source) => {
  const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } })
  return `data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`
}
const src = (path) => readFile(new URL(`../src/${path}`, import.meta.url), 'utf8')
const m = await import(moduleUrl(await src('features/daily-report/report-management.ts')))
const viewUrl = moduleUrl(await src('features/daily-report/daily-report-view.ts'))
const lead = await import(moduleUrl((await src('features/daily-report/lead-matrix.ts')).replace("'./daily-report-view'", `'${viewUrl}'`)))

const period = (days, to = '2026-11-21') => ({ from: '2026-11-15', to, days })

test('what the previous period is called: a day against yesterday or the day before, a week against last week, any other length by its days', () => {
  assert.equal(m.compareName(period(1, '2026-11-21'), '2026-11-21'), 'yesterday')
  assert.equal(m.compareName(period(1, '2026-11-20'), '2026-11-21'), 'the day before')
  assert.equal(m.compareName(period(7), '2026-11-21'), 'last week')
  assert.equal(m.compareName(period(21), '2026-11-21'), 'the previous 21 days')
})

test('a trend: arrow direction, the signed number, and whether the change is good, bad or just a change', () => {
  assert.deepEqual(m.trendOf(18, 6, 'last week', 'up'), { kind: 'up', delta: '+12', against: 'last week', tone: 'good' })
  assert.deepEqual(m.trendOf(2, 5, 'last week', 'up'), { kind: 'down', delta: '−3', against: 'last week', tone: 'bad' })
  assert.equal(m.trendOf(8, 3, 'yesterday', 'down').tone, 'bad') // more lost is bad
  assert.equal(m.trendOf(1, 3, 'yesterday', 'down').tone, 'good')
  assert.equal(m.trendOf(4, 1, 'yesterday', 'none').tone, 'plain')
  assert.deepEqual(m.trendOf(5, 5, 'yesterday', 'up'), { kind: 'flat', delta: '0', against: 'yesterday', tone: 'plain' })
  assert.equal(m.trendOf(5, null, 'yesterday'), null) // Total: nothing to compare with
  assert.equal(m.trendOf(null, 5, 'yesterday'), null)
  assert.equal(m.trendOf(1200, 0, 'last week').delta, '+1,200')
})

test('the trend as a sentence for a screen reader', () => {
  assert.equal(m.trendSentence(m.trendOf(18, 6, 'last week')), '12 more than last week')
  assert.equal(m.trendSentence(m.trendOf(2, 5, 'yesterday')), '3 fewer than yesterday')
  assert.equal(m.trendSentence(m.trendOf(5, 5, 'yesterday')), 'same as yesterday')
  assert.equal(m.trendSentence(null), '')
})

test('shares, conversion and days in words', () => {
  assert.equal(m.percentOf(1, 3), 33)
  assert.equal(m.percentOf(2, 3), 67)
  assert.equal(m.percentOf(0, 5), 0)
  assert.equal(m.percentOf(3, 0), null)
  assert.equal(m.percentOf(null, 4), null)
  assert.equal(m.conversion(3, 12), 25)
  assert.equal(m.conversion(3, 0), null)
  assert.equal(m.daysText(1), '1 day')
  assert.equal(m.daysText(18.5), '18.5 days')
  assert.equal(m.daysText(38), '38 days')
  assert.equal(m.daysText(null), '–')
  assert.equal(m.daysShort(12), '12d')
  assert.equal(m.waitTone(5, 7), 'ok')
  assert.equal(m.waitTone(7, 7), 'ok') // stuck means MORE than the line
  assert.equal(m.waitTone(8, 7), 'late')
  assert.equal(m.waitTone(15, 7), 'long')
})

const insight = {
  stuckDays: 7,
  waiting: { total: 16, stuck: 5, busiest: { no: 3, label: 'Interview scheduling', count: 6, stuck: 1, avgDays: 3.2 }, byStage: [{ no: 1, label: 'Prescreening', count: 2, stuck: 1, avgDays: 9 }, { no: 2, label: 'Forwarded to client', count: 4, stuck: 4, avgDays: 12 }] },
  stuckPerStaff: { asha: 2, ravi: 3 },
  longest: [],
  completed: { value: 20, previous: 3 },
  stopped: { value: 8, previous: 9 },
  exitReasons: [{ label: 'Offer declined', count: 1, previous: 0 }, { label: 'Rejected at interview', count: 4, previous: 2 }, { label: 'Visa rejected', count: 4, previous: 1 }],
  exitOther: 0,
}

test('the Task report summary: completed and lost against the previous period, the biggest reason, the share stuck', () => {
  const summary = m.taskSummary({ taskInsight: insight, period: period(1, '2026-11-21') }, '2026-11-21')
  assert.equal(summary.waiting, 16)
  assert.deepEqual(summary.completed, { value: 20, previous: 3 })
  assert.deepEqual(summary.stopped, { value: 8, previous: 9 })
  assert.equal(summary.reason.label, 'Rejected at interview') // ties go to the first listed
  assert.equal(summary.stuck, 5)
  assert.equal(summary.stuckPct, 31)
  assert.equal(summary.busiest.no, 3)
  assert.equal(summary.against, 'yesterday')
  assert.equal(m.taskSummary({ period: period(1) }, '2026-11-21'), null) // an older server: no management reading, no cards
  assert.equal(m.biggestReason([]), null)
})

test('stuck per person and the waits by stage come from the reading; without it they are not counted (a dash, not a zero)', () => {
  assert.equal(m.stuckOf({ taskInsight: insight }, 'ravi'), 3)
  assert.equal(m.stuckOf({ taskInsight: insight }, 'meena'), 0)
  assert.equal(m.stuckOf({}, 'ravi'), null)
  assert.equal(m.waitingByStage({ taskInsight: insight }).get(2).stuck, 4)
  assert.equal(m.waitingByStage({}).size, 0)
})

test('completed and stopped per stage add up the people shown only', () => {
  const report = { stageFlowStaff: [
    { id: 'asha', completed: { 1: 3, 2: 1 }, stopped: { 1: 1, 2: 0 } },
    { id: 'admin', completed: { 1: 9, 2: 9 }, stopped: { 1: 9, 2: 9 } },
    { id: 'ravi', completed: { 1: 2 }, stopped: { 1: 0 } },
  ] }
  const flow = m.flowByStage(report, new Set(['asha', 'ravi']))
  assert.deepEqual(flow.get(1), { completed: 5, stopped: 1 })
  assert.deepEqual(flow.get(2), { completed: 1, stopped: 0 })
  assert.equal(m.flowByStage({}, new Set(['asha'])), null)
})

const sheet = (extra = {}) => ({
  totalLeads: 72, assigned: 60, contacted: 12, notContacted: 0, unassigned: 12, byStatus: [], newInPeriod: 19,
  previous: { assignedInPeriod: 4, contacted: 7, newInPeriod: 10 },
  statusColumns: [{ key: 'new', label: 'Not contacted' }, { key: 'interested', label: 'Interested' }],
  byStatusPerStaff: [{ id: 'asha', counts: { new: 5, interested: 1 } }, { id: 'ravi', counts: { new: 2, interested: 2 } }, { id: 'unassigned', counts: { new: 3, interested: 0 } }],
  ...extra,
})
const leadReport = () => ({
  scope: 'team',
  period: period(1, '2026-11-21'),
  leadSheet: sheet(),
  leadFeedback: { contacted: 12, byOutcome: [{ key: 'notInterested', label: 'Not interested', count: 2 }], resumesReceived: 0, interested: 3, callbacksDue: 11 },
  leadInsight: { staleDays: 3, stale: { total: 6, unassigned: 2, perStaff: { asha: 3, ravi: 1 } }, aging: [], previous: { interested: 1, notInterested: 4 } },
  staff: [
    { id: 'asha', name: 'Asha', role: 'staff', idle: false, metrics: {}, leads: { assigned: 6, contacted: 8, interested: 1 } },
    { id: 'ravi', name: 'Ravi', role: 'staff', idle: false, metrics: {}, leads: { assigned: 4, contacted: 4, interested: 2 } },
    { id: 'root', name: 'Root', role: 'admin', idle: true, metrics: {}, leads: { assigned: 0, contacted: 0, interested: 0 } },
  ],
})

test('the Lead sheet summary: new, interested with its conversion, not interested, follow-up, all against the previous period', () => {
  const summary = m.leadSummary(leadReport(), '2026-11-21')
  assert.deepEqual(summary.newLeads, { value: 19, previous: 10 })
  assert.deepEqual(summary.interested, { value: 3, previous: 1 })
  assert.deepEqual(summary.notInterested, { value: 2, previous: 4 })
  assert.equal(summary.followUp, 11)
  assert.deepEqual(summary.conversion, { value: 25, previous: 14 }) // 3 of 12, against 1 of 7
  assert.equal(summary.stale, 6)
  assert.equal(summary.staleDays, 3)
  assert.equal(summary.against, 'yesterday')
  const total = leadReport()
  total.leadInsight.previous = null
  total.leadSheet.previous = null
  const flat = m.leadSummary(total, '2026-11-21')
  assert.equal(flat.interested.previous, null)
  assert.equal(flat.conversion.previous, null) // Total: nothing to compare with
  const old = leadReport()
  delete old.leadSheet.newInPeriod
  assert.equal(m.leadSummary(old, '2026-11-21'), null)
})

test('the lead matrix adds interested, conversion and aging per recruiter, the Unassigned row and a Total that adds the rows', () => {
  const matrix = lead.leadMatrix(leadReport(), '', [])
  assert.deepEqual(matrix.rows.map((r) => r.name), ['Asha', 'Ravi', 'Unassigned']) // the Administrator account is no row
  const [asha, ravi, unassigned] = matrix.rows
  assert.deepEqual([asha.interested, asha.conversion, asha.stale], [1, 13, 3]) // 1 of 8, rounded
  assert.deepEqual([ravi.interested, ravi.conversion, ravi.stale], [2, 50, 1])
  assert.deepEqual([unassigned.interested, unassigned.conversion, unassigned.stale], [null, null, 2])
  assert.deepEqual([matrix.total.interested, matrix.total.conversion, matrix.total.stale], [3, 25, 6])
  assert.equal(matrix.total.stale, leadReport().leadInsight.stale.total)
  const one = lead.leadMatrix(leadReport(), 'ravi', [])
  assert.deepEqual(one.rows.map((r) => r.id), ['ravi'])
  assert.deepEqual([one.total.interested, one.total.conversion, one.total.stale], [2, 50, 1])
  const old = leadReport()
  delete old.leadInsight
  assert.equal(lead.leadMatrix(old, '', []).rows[0].stale, null) // an older server: aging is not counted, not zero
  const nobody = leadReport()
  nobody.staff[0].leads.contacted = 0
  assert.equal(lead.leadMatrix(nobody, '', []).rows[0].conversion, null)
})

test('the layout: a few cards, centred figures under their headings, names left, no fixed table width wider than the page needs, tokens for the dark theme', async () => {
  const css = await src('styles/report-management.css')
  assert.match(css, /@media \(min-width: 1100px\) \{\s*html:root \.rp-cards \.workspace-summary \{ grid-template-columns: repeat\(var\(--summary-columns, 4\), minmax\(0, 280px\)\)/) // a few cards keep a card's width
  assert.match(css, /@media \(max-width: 1099px\)[\s\S]*repeat\(2, minmax\(0, 1fr\)\)/) // cards below the breakpoint
  assert.match(css, /@media \(max-width: 479px\)[\s\S]*grid-template-columns: minmax\(0, 1fr\);/)
  assert.ok(!/#[0-9a-f]{3,8}\b/i.test(css.replace(/\/\*[\s\S]*?\*\//g, '')), 'colours come from the theme tokens, so the dark theme follows')
  const plain = css.replace(/\/\*[\s\S]*?\*\//g, '')
  assert.equal((plain.match(/\{/g) ?? []).length, (plain.match(/\}/g) ?? []).length, 'every block is closed')
  assert.match(css, /var\(--error-ink\)/)
  assert.match(css, /\.rp-sr \{ position: absolute/) // captions stay for screen readers
  const tables = await src('styles/report-tables.css')
  assert.match(tables, /\.dwr \.dwm-table :is\(th, td\) \{[^}]*text-align: center/) // figures centred
  assert.match(tables, /\.dwr \.dwm-table :is\(thead th\.dwr-name-head, tbody th\.dwr-name, tfoot th\.dwr-name\) \{[^}]*text-align: left/) // names left
  assert.match(tables, /border-right: 1px solid var\(--rt-line\)/) // the brighter line between columns
  for (const file of ['report-task.tsx', 'report-lead.tsx', 'report-lists.tsx']) {
    const source = await src(`features/daily-report/${file}`)
    assert.match(source, /<caption className="rp-sr">/, `${file}: tables have captions`)
    assert.match(source, /scope="row"/, `${file}: row headings`)
    assert.match(source, /scope="col"/, `${file}: column headings`)
  }
})

test('the Stuck figure and the Aging figure open their lists, and the cards that have a list behind them are buttons', async () => {
  const task = await src('features/daily-report/report-task.tsx')
  assert.match(task, /onDrill\('stuck', item\.id\)/)
  assert.match(task, /onDrill\(`stuck:\$\{stage\.no\}`/)
  const leads = await src('features/daily-report/report-lead.tsx')
  assert.match(leads, /onDrill\('leadsStale'/)
  assert.match(leads, /onDrill\('leadsFollowUp'/)
  const view = await src('features/daily-report/daily-report-view.ts')
  assert.match(view, /Stuck: waiting more than 7 days/)
})
