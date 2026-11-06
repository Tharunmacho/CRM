import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'

async function load(path) {
  const source = await readFile(new URL(path, import.meta.url), 'utf8')
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
  })
  return import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}#${Math.random()}`)
}

const { reconcile, reconcileList, keepIfSame } = await load('../src/shared/state/reconcile.ts')

const clone = (value) => JSON.parse(JSON.stringify(value))

test('an identical snapshot comes back as the old one, so the caller can skip the render', () => {
  const before = { settings: { name: 'A' }, candidates: [{ id: 'c1', name: 'Asha' }, { id: 'c2', name: 'Ravi' }], tz: 'Asia/Kolkata' }
  assert.equal(reconcile(before, clone(before)), before)
})

test('only the changed record is new; unchanged records and collections keep their identity', () => {
  const before = {
    candidates: [{ id: 'c1', name: 'Asha' }, { id: 'c2', name: 'Ravi' }],
    orders: [{ id: 'o1', ref: 'ORD-1' }],
    settings: { name: 'A' },
  }
  const next = clone(before)
  next.candidates[1].name = 'Ravi K'
  const merged = reconcile(before, next)
  assert.notEqual(merged, before)
  assert.equal(merged.candidates[0], before.candidates[0])
  assert.notEqual(merged.candidates[1], before.candidates[1])
  assert.equal(merged.candidates[1].name, 'Ravi K')
  assert.equal(merged.orders, before.orders)
  assert.equal(merged.settings, before.settings)
})

test('records are matched by id, so a reordered list reuses the old objects', () => {
  const before = [{ id: 'a', n: 1 }, { id: 'b', n: 2 }, { id: 'c', n: 3 }]
  const merged = reconcileList(before, clone([before[2], before[0], before[1]]))
  assert.deepEqual(merged.map((item) => item.id), ['c', 'a', 'b'])
  assert.equal(merged[0], before[2])
  assert.equal(merged[1], before[0])
  assert.notEqual(merged, before) // the order changed, so a new array
})

test('added and removed records', () => {
  const before = [{ id: 'a', n: 1 }, { id: 'b', n: 2 }]
  const added = reconcileList(before, clone([...before, { id: 'c', n: 3 }]))
  assert.equal(added.length, 3)
  assert.equal(added[0], before[0])
  const removed = reconcileList(before, clone([before[1]]))
  assert.deepEqual(removed, [{ id: 'b', n: 2 }])
  assert.equal(removed[0], before[1])
})

test('lists without ids are compared by position', () => {
  const before = [{ x: 1 }, { x: 2 }]
  assert.equal(reconcileList(before, clone(before)), before)
  const merged = reconcileList(before, [{ x: 1 }, { x: 3 }])
  assert.equal(merged[0], before[0])
  assert.deepEqual(merged[1], { x: 3 })
})

test('a new key or a removed key is a change', () => {
  const before = { a: [], b: 'x' }
  assert.notEqual(reconcile(before, { a: [], b: 'x', c: 1 }), before)
  assert.notEqual(reconcile(before, { a: [] }), before)
})

test('keepIfSame', () => {
  const me = { id: 'u1', name: 'Admin' }
  assert.equal(keepIfSame(me, clone(me)), me)
  const other = { id: 'u1', name: 'Admin 2' }
  assert.equal(keepIfSame(me, other), other)
  assert.equal(keepIfSame(null, other), other)
})

test('a realistic refresh of 5,000 candidates stays cheap', () => {
  const make = (i) => ({
    id: `c${i}`, ref: `CND-${i}`, name: `Candidate ${i}`, phone: `90000${i}`, email: `c${i}@example.test`, ownerId: `u${i % 7}`,
    designation: 'Welder', skills: ['arc', 'tig', 'mig'], experience: { total: 5, gulf: 2 }, notes: 'x'.repeat(300), createdAt: '2026-10-01T00:00:00Z',
  })
  const before = { candidates: Array.from({ length: 5000 }, (_, i) => make(i)) }
  const first = reconcile({ candidates: [] }, clone(before)) // warms nothing: everything is new
  const next = clone(before)
  next.candidates[17].name = 'Changed'
  const started = performance.now()
  const merged = reconcile(first, next)
  const elapsed = performance.now() - started
  assert.equal(merged.candidates[16], first.candidates[16])
  assert.equal(merged.candidates[17].name, 'Changed')
  assert.ok(elapsed < 400, `took ${elapsed.toFixed(1)} ms`)
  // The second comparison against the same previous snapshot reuses the cached text of the old records.
  const again = performance.now()
  reconcile(merged, clone(before))
  assert.ok(performance.now() - again < 400)
})
