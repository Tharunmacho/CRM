import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'

async function loadSource(path) {
  const source = await readFile(new URL(path, import.meta.url), 'utf8')
  const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } })
  return import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`)
}
const { inWindow, isVerified, periodStart, previousCount, ratio, seriesFor, trendPercent, verificationSeries } = await loadSource('../src/features/dashboard/metrics.ts')

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
