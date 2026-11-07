import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'


// data-bank-stats.ts imports data-bank-pools.ts: transpile that one to a temp module the import can reach.
const poolsSource = await readFile(new URL('../src/features/candidates/data-bank-pools.ts', import.meta.url), 'utf8')
const poolsJs = ts.transpileModule(poolsSource, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText
const poolsUrl = `data:text/javascript;base64,${Buffer.from(poolsJs).toString('base64')}`
const statsSource = await readFile(new URL('../src/features/candidates/data-bank-stats.ts', import.meta.url), 'utf8')
const statsJs = ts.transpileModule(statsSource, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText.replace("'./data-bank-pools'", `'${poolsUrl}'`)
const stats = await import(`data:text/javascript;base64,${Buffer.from(statsJs).toString('base64')}`)

const counts = { all: 4, unassigned: 3, needs_review: 0 }

test('admin and manager see Resumes, Not assigned and Needs review; staff see no Not assigned card', () => {
  for (const role of ['admin', 'manager']) {
    assert.deepEqual(stats.statCards(counts, 'all', role).map(card => card.label), ['Resumes', 'Not assigned', 'Needs review'])
  }
  assert.deepEqual(stats.statCards(counts, 'all', 'staff').map(card => card.label), ['Resumes', 'Needs review'])
  assert.deepEqual(stats.statCards(counts, 'all', undefined).map(card => card.key), ['resumes', 'needs_review'])
})

test('the old Jobs, No order yet, Not interested and Deployed cards are gone', () => {
  const labels = stats.statCards({ all: 9, unassigned: 1, needs_review: 1 }, 'all', 'admin').map(card => card.label)
  for (const gone of ['Jobs', 'No order yet', 'Not interested', 'Deployed']) assert.ok(!labels.includes(gone), gone)
})

test('a card is active for its own pool and a zero reads muted unless it is the active filter', () => {
  const cards = stats.statCards(counts, 'all', 'admin')
  assert.deepEqual(cards.map(card => card.active), [true, false, false])
  assert.deepEqual(cards.map(card => card.muted), [false, false, true])
  const review = stats.statCards(counts, 'needs_review', 'admin')
  assert.equal(review[2].active, true)
  assert.equal(review[2].muted, false)
  assert.equal(review[0].active, false)
  assert.equal(stats.statCards({ all: 4, unassigned: 0, needs_review: 0 }, 'all', 'admin')[1].muted, true)
})

test('pressing Resumes shows everyone; pressing another card toggles its pool', () => {
  const [resumes, unassigned, review] = stats.statCards(counts, 'all', 'admin')
  assert.equal(stats.poolAfterCardPress('needs_review', resumes), 'all')
  assert.equal(stats.poolAfterCardPress('all', unassigned), 'unassigned')
  assert.equal(stats.poolAfterCardPress('unassigned', unassigned), 'all')
  assert.equal(stats.poolAfterCardPress('unassigned', review), 'needs_review')
})

test('the visible filters follow the role and Staff is only for admin and manager', () => {
  assert.deepEqual(stats.visibleFilters('admin'), ['scope', 'status', 'stage', 'staff'])
  assert.deepEqual(stats.visibleFilters('manager'), ['scope', 'status', 'stage', 'staff'])
  assert.deepEqual(stats.visibleFilters('staff'), ['scope', 'status', 'stage'])
  assert.deepEqual([...stats.MORE_FILTER_SELECTS], ['pool', 'source', 'passport'])
})

test('Everyone / My candidates maps to the staff filter', () => {
  assert.equal(stats.scopeValue('', 'u1'), 'everyone')
  assert.equal(stats.scopeValue('u1', 'u1'), 'mine')
  assert.equal(stats.scopeValue('u2', 'u1'), 'other')
  assert.equal(stats.staffForScope('mine', 'u1'), 'u1')
  assert.equal(stats.staffForScope('everyone', 'u1'), '')
})

test('the list choices offer Not assigned only to those who can assign, or while it is the active list', () => {
  const names = (role, current) => stats.poolChoices(role, current).map(([value]) => value)
  assert.deepEqual(names('admin', 'all'), ['all', 'unmapped', 'unassigned', 'needs_review', 'not_interested'])
  assert.deepEqual(names('staff', 'all'), ['all', 'unmapped', 'needs_review', 'not_interested'])
  assert.ok(names('staff', 'unassigned').includes('unassigned'))
})
