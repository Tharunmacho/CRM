import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'

/* The Lead sheet report's matrix: recruiters by lead status, the Unassigned row, the Total that ties to the sheet, one recruiter, an older server. */

const moduleUrl = (source) => {
  const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } })
  return `data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`
}
const m = await import(moduleUrl(await readFile(new URL('../src/features/daily-report/lead-matrix.ts', import.meta.url), 'utf8')))

const COLUMNS = [
  { key: 'new', label: 'Not contacted' }, { key: 'contacted', label: 'Contacted' }, { key: 'interested', label: 'Interested' },
  { key: 'resume_requested', label: 'Resume requested' }, { key: 'resume_received', label: 'Resume received' }, { key: 'converted', label: 'Converted' },
  { key: 'not_interested', label: 'Not interested' }, { key: 'invalid', label: 'Not reachable' }, { key: 'wrong_number', label: 'Wrong number' },
  { key: 't:ringing', label: 'Ringing', template: true },
]
const zero = Object.fromEntries(COLUMNS.map((c) => [c.key, 0]))
const person = (id, name, assigned, contacted) => ({ id, name, idle: false, metrics: { leadsContacted: contacted }, leads: { assigned, contacted } })
const report = (extra = {}) => ({
  scope: 'team',
  staff: [person('ravi', 'Ravi', 4, 3), person('asha', 'Asha', 6, 5)],
  leadSheet: {
    totalLeads: 11, assigned: 10, contacted: 8, notContacted: 0, unassigned: 1, byStatus: COLUMNS.slice(0, 9).map((c) => ({ ...c, count: 0 })),
    statusColumns: COLUMNS,
    byStatusPerStaff: [
      { id: 'ravi', counts: { ...zero, new: 2, interested: 1, converted: 1 } },
      { id: 'asha', counts: { ...zero, new: 1, contacted: 1, interested: 1, 't:ringing': 1, not_interested: 1, wrong_number: 1 } },
      { id: 'unassigned', counts: { ...zero, new: 1 } },
    ],
    ...extra,
  },
})

test('one row per recruiter by name, the Unassigned row last, columns in the lead sheet order with the templates after the nine statuses', () => {
  const matrix = m.leadMatrix(report(), '', [])
  assert.deepEqual(matrix.columns.map((c) => c.key), COLUMNS.map((c) => c.key))
  assert.deepEqual(matrix.rows.map((r) => r.name), ['Asha', 'Ravi', 'Unassigned'])
  assert.equal(matrix.rows[2].unassigned, true)
  assert.equal(matrix.rows[2].assigned, null)
  assert.equal(matrix.rows[0].counts['t:ringing'], 1)
  assert.equal(matrix.rows[0].pct, 83)
})

test('the Total row is the column sums and ties to the sheet', () => {
  const matrix = m.leadMatrix(report(), '', [])
  const sum = Object.values(matrix.total.counts).reduce((a, b) => a + b, 0)
  assert.equal(sum, 11)
  assert.equal(matrix.total.counts.new, 4)
  assert.equal(matrix.total.assigned, 10)
  assert.equal(matrix.total.contacted, 8)
  for (const row of matrix.rows.filter((r) => !r.unassigned)) assert.equal(Object.values(row.counts).reduce((a, b) => a + b, 0), row.assigned)
})

test('one recruiter shows only that row and a total of that row, with no Unassigned row', () => {
  const matrix = m.leadMatrix(report(), 'ravi', [])
  assert.deepEqual(matrix.rows.map((r) => r.id), ['ravi'])
  assert.equal(matrix.total.assigned, 4)
  assert.equal(matrix.total.counts.new, 2)
  assert.equal(Object.values(matrix.total.counts).reduce((a, b) => a + b, 0), 4)
})

test('a picked recruiter the report has no row for comes from the roster, with nothing counted', () => {
  const r = report()
  r.staff = []
  const matrix = m.leadMatrix(r, 'sam', [{ id: 'sam', name: 'Sam', idle: true, metrics: {} }])
  assert.deepEqual(matrix.rows.map((x) => x.name), ['Sam'])
  assert.equal(matrix.rows[0].counts.new, null)
})

test('an older server without the new field: the nine statuses from byStatus, every cell a dash (null), no Unassigned row, no error', () => {
  const old = report({ statusColumns: undefined, byStatusPerStaff: undefined })
  const matrix = m.leadMatrix(old, '', [])
  assert.equal(matrix.hasCounts, false)
  assert.equal(matrix.columns.length, 9)
  assert.deepEqual(matrix.rows.map((r) => r.id), ['asha', 'ravi'])
  assert.ok(matrix.rows.every((r) => Object.values(r.counts).every((v) => v === null)))
  assert.ok(Object.values(matrix.total.counts).every((v) => v === null))
  assert.equal(matrix.total.assigned, 10)
})

test('a report without a lead sheet gives an empty matrix', () => {
  const matrix = m.leadMatrix({ scope: 'team', staff: [], leadSheet: undefined }, '', [])
  assert.deepEqual([matrix.columns, matrix.rows, matrix.total], [[], [], null])
})

test('Lead status now: the matrix total with shares; the sheet byStatus is the fallback for the whole sheet on an older server', () => {
  const r = report()
  const now = m.statusNow(m.leadMatrix(r, '', []), r, '')
  assert.equal(now.find((x) => x.key === 'new').count, 4)
  assert.equal(now.find((x) => x.key === 'new').pct, 36.4)
  assert.equal(now.reduce((a, x) => a + x.count, 0), 11)
  const old = report({ statusColumns: undefined, byStatusPerStaff: undefined, byStatus: [{ key: 'new', label: 'Not contacted', count: 7 }, { key: 'contacted', label: 'Contacted', count: 3 }] })
  const fallback = m.statusNow(m.leadMatrix(old, '', []), old, '')
  assert.deepEqual(fallback.map((x) => [x.key, x.count]), [['new', 7], ['contacted', 3]])
  assert.deepEqual(m.statusNow(m.leadMatrix(old, 'ravi', []), old, 'ravi'), [])
})
