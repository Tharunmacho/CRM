import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'

async function load(path) {
  const source = await readFile(new URL(path, import.meta.url), 'utf8')
  const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText
  return import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`)
}

const pools = await load('../src/features/candidates/data-bank-pools.ts')

test('the Data Bank lists the eleven PRD columns in the specified order', () => {
  assert.deepEqual(pools.PRD_COLUMNS.map((column) => column.label), [
    'Name', 'Phone', 'Job title', 'Experience', 'Passport', 'Source', 'Recruiter', 'Order', 'Stage', 'Status', 'Date added',
  ])
})

test('every PRD column key is a column the server can filter and sort on', async () => {
  const server = await readFile(new URL('../../backend/app/services/candidate_search.py', import.meta.url), 'utf8')
  for (const { key } of pools.PRD_COLUMNS) assert.match(server, new RegExp(`"${key}":`), `server has no "${key}" column`)
})

test('the filter count ignores blank column filters and counts staff, skills and coverage', () => {
  assert.equal(pools.activeFilterCount({}, '', [], 'all'), 0)
  assert.equal(pools.activeFilterCount({ name: '  ', source: 'Email' }, '', [], 'all'), 1)
  assert.equal(pools.activeFilterCount({ passport: 'Yes', stage: '3' }, 'u2', ['tig', 'mig'], 'all'), 5)
  assert.equal(pools.activeFilterCount({}, '', [], 'missing'), 1)
})

const trends = await load('../src/features/candidates/data-bank-trends.ts')

test('arrival trend compares the same span of last month and never invents a baseline', () => {
  const now = new Date(2026, 9, 10, 12) // 10 Oct 2026, noon
  const day = (month, d, hour = 9) => new Date(2026, month, d, hour).toISOString()
  // this month: 3 arrivals (1, 5, 9 Oct); last month: 2 inside the first 10 days (2, 8 Sep) plus 1 on 20 Sep (outside the span)
  const dates = [day(9, 1), day(9, 5), day(9, 9), day(8, 2), day(8, 8), day(8, 20), undefined, 'not a date']
  const trend = trends.arrivalTrend(dates, now)
  assert.deepEqual({ current: trend.current, previous: trend.previous }, { current: 3, previous: 2 })
  assert.equal(trend.pct, 50)
  assert.equal(trends.arrivalTrend([day(9, 2)], now).pct, null)
  assert.equal(trends.trendCaption(trends.arrivalTrend([day(9, 2)], now)).rest, 'new this month')
  assert.equal(trends.trendCaption(trends.arrivalTrend([], now)).tone, 'quiet')
  assert.equal(trends.trendCaption(trend).lead, '↑ 50.0%')
})

test('weekly arrivals bucket the last twelve weeks, oldest first', () => {
  const now = new Date(2026, 9, 10, 12)
  const ago = (days) => new Date(now.getTime() - days * 86400000).toISOString()
  const weeks = trends.weeklyArrivals([ago(1), ago(2), ago(8), ago(200)], 12, now)
  assert.equal(weeks.length, 12)
  assert.equal(weeks[11], 2)
  assert.equal(weeks[10], 1)
  assert.equal(weeks.reduce((a, b) => a + b, 0), 3)
})
