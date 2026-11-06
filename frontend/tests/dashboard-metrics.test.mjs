import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'

async function loadSource(path) {
  const source = await readFile(new URL(path, import.meta.url), 'utf8')
  const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } })
  return import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`)
}
const { intakeBuckets, axisFor, arrivalGrid, stageReach, countBuckets, countBucketsOpenEnd, inWindow, isVerified, periodStart, previousCount, ratio, seriesFor, trendPercent, verificationSeries } = await loadSource('../src/features/dashboard/metrics.ts')

const DAY = 86_400_000
const now = new Date('2026-10-24T12:00:00Z')
const at = (daysAgo, extra = {}) => ({ addedAt: new Date(now.getTime() - daysAgo * DAY).toISOString(), source: 'email', status: 'new', ownerId: 'u3', ...extra })

test('period windows start where the picker says', () => {
  assert.equal(periodStart('week', now, []), now.getTime() - 7 * DAY)
  assert.equal(periodStart('month', now, []), now.getTime() - 30 * DAY)
  assert.equal(periodStart('year', now, []), new Date(now.getFullYear(), 0, 1).getTime())
  assert.equal(periodStart('all', now, [at(400)]), now.getTime() - 400 * DAY)
})

test('cumulative growth only ever rises and ends at the window total', () => {
  const rows = [at(1), at(2), at(3), at(20), at(25)]
  const start = periodStart('month', now, rows)
  const series = seriesFor(inWindow(rows, start, now.getTime()), start, now.getTime(), undefined, true)
  assert.equal(series.length, 7)
  assert.deepEqual(series.map((point) => point.value), [...series.map((point) => point.value)].sort((a, b) => a - b))
  assert.equal(series.at(-1).value, 5)
})

test('trend compares with the equal window before and never invents a baseline', () => {
  const rows = [at(1), at(2), at(40), at(41), at(42), at(43)]
  const start = periodStart('month', now, rows)
  const previous = previousCount(rows, start, now.getTime())
  assert.equal(previous, 4)
  assert.equal(trendPercent('month', 2, previous), -50)
  assert.equal(trendPercent('month', 2, 0), null)
  assert.equal(trendPercent('all', 2, 4), null)
})

test('verification means no review flag, and empty buckets read zero', () => {
  assert.equal(isVerified({ status: 'new' }), true)
  assert.equal(isVerified({ status: 'new', needsReview: true }), false)
  assert.equal(isVerified({ status: 'needs_review' }), false)
  const rows = [at(1), at(1, { needsReview: true })]
  const start = periodStart('week', now, rows)
  const series = verificationSeries(rows, start, now.getTime())
  assert.ok(series.some((point) => point.value === 50))
  assert.ok(series.some((point) => point.value === 0))
  assert.equal(ratio(1, 0), 0)
  assert.equal(ratio(1, 3), 33)
})

test('bucket counts ignore times outside the window and the open-ended one keeps the tail', () => {
  const times = [0, 5, 10, 25, 99, -3]
  assert.deepEqual(countBuckets(times, 0, 10, 3), [2, 1, 1])
  assert.deepEqual(countBucketsOpenEnd(times, 0, 10, 3), [2, 1, 2])
})

test('intake columns add up to the window total and split by source', () => {
  const sources = ['email', 'whatsapp', 'walk_in', 'referral', 'other']
  const rows = [at(0), at(1, { source: 'whatsapp' }), at(1, { source: 'walk_in' }), at(5, { source: 'mystery' }), at(40), at(200)]
  for (const period of ['week', 'month', 'quarter', 'year', 'all']) {
    const end = now.getTime()
    const start = periodStart(period, now, rows)
    const pool = inWindow(rows, start, end)
    const { buckets, unit } = intakeBuckets(pool, period, start, end, sources)
    assert.equal(buckets.reduce((sum, bucket) => sum + bucket.total, 0), pool.length, period)
    assert.ok(buckets.length >= 1)
    assert.equal(unit, { week: 'day', month: 'day', quarter: 'week', year: 'month', all: 'month' }[period])
    for (const bucket of buckets) assert.equal(Object.values(bucket.counts).reduce((a, b) => a + b, 0), bucket.total)
  }
  const week = intakeBuckets(inWindow(rows, periodStart('week', now, rows), now.getTime()), 'week', periodStart('week', now, rows), now.getTime(), sources)
  assert.equal(week.buckets.reduce((sum, bucket) => sum + bucket.counts.other, 0), 1)
})

test('the chart axis leaves room above the highest column and keeps to a few whole steps', () => {
  assert.deepEqual(axisFor(0), { max: 2, step: 1, ticks: [0, 1, 2] })
  assert.deepEqual(axisFor(2), { max: 3, step: 1, ticks: [0, 1, 2, 3] })
  assert.deepEqual(axisFor(12), { max: 15, step: 5, ticks: [0, 5, 10, 15] })
  assert.deepEqual(axisFor(20), { max: 30, step: 10, ticks: [0, 10, 20, 30] })
  for (const peak of [1, 3, 7, 9, 31, 64, 120, 999]) { const axis = axisFor(peak); assert.ok(axis.max >= peak && axis.ticks.length <= 5, String(peak)) }
})

test('the arrivals grid is 12 weeks of 7 days, Monday first, and counts each candidate once', () => {
  const rows = [at(0), at(0), at(1), at(30), at(200)]
  const grid = arrivalGrid(rows, now.getTime())
  assert.equal(grid.columns.length, 12)
  assert.ok(grid.columns.every(column => column.length === 7))
  assert.equal(new Date(grid.columns[0][0].time).getDay(), 1)
  assert.equal(grid.total, 4)
  assert.equal(grid.max, 2)
  assert.equal(grid.busiest.count, 2)
  assert.ok(grid.activeDays === 3)
})

test('funnel reach only falls and the holdup is the stage that keeps the most people', () => {
  const { reached, moved, holdup } = stageReach([3, 4, 2, 1, 1, 2, 1, 1, 1, 1, 1])
  assert.equal(reached[0], 18)
  assert.ok(reached.every((value, index) => index === 0 || value <= reached[index - 1]))
  assert.equal(moved[10], null)
  assert.equal(holdup, 5)
  assert.equal(stageReach([0, 0, 0]).holdup, null)
  assert.equal(stageReach([0, 0, 2]).holdup, null, 'nobody has moved past stage 3 yet, so there is no holdup to name')
  assert.equal(stageReach([0, 0, 0]).moved[0], null)
})
