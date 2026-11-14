import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'

async function load(relative) {
  const source = await readFile(new URL(relative, import.meta.url), 'utf8')
  const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } })
  return import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`)
}

const { growHeight, clampRows } = await load('../src/shared/lib/auto-grow.ts')

// A 14px / 20px-line box with 10px vertical padding and a 1px border, the shape of the shared Textarea.
const box = { lineHeight: 20, paddingY: 20, borderY: 2, borderBox: true }

test('an empty box keeps its minimum number of rows', () => {
  assert.deepEqual(growHeight({ ...box, scrollHeight: 40, minRows: 3, maxRows: 6 }), { height: 82, scrolls: false })
  assert.deepEqual(growHeight({ ...box, scrollHeight: 40, minRows: 1, maxRows: 6 }), { height: 42, scrolls: false })
})

test('the box grows with the text, line by line', () => {
  assert.equal(growHeight({ ...box, scrollHeight: 20 + 4 * 20, minRows: 2, maxRows: 8 }).height, 102)
  assert.equal(growHeight({ ...box, scrollHeight: 20 + 5 * 20, minRows: 2, maxRows: 8 }).height, 122)
})

test('growth stops at maxRows and the box scrolls inside', () => {
  const r = growHeight({ ...box, scrollHeight: 20 + 30 * 20, minRows: 2, maxRows: 6 })
  assert.deepEqual(r, { height: 6 * 20 + 22, scrolls: true })
})

test('text exactly at the cap does not scroll', () => {
  assert.deepEqual(growHeight({ ...box, scrollHeight: 20 + 6 * 20, minRows: 2, maxRows: 6 }), { height: 142, scrolls: false })
})

test('maxRows 0 or missing means unlimited', () => {
  assert.equal(growHeight({ ...box, scrollHeight: 20 + 50 * 20, minRows: 2, maxRows: 0 }).height, 1022)
  assert.equal(growHeight({ ...box, scrollHeight: 20 + 50 * 20, minRows: 2 }).scrolls, false)
})

test('a cap below the minimum never shrinks the box under its minimum', () => {
  assert.equal(growHeight({ ...box, scrollHeight: 40, minRows: 4, maxRows: 2 }).height, 102)
})

test('content-box sizing subtracts padding and border from the height', () => {
  assert.equal(growHeight({ ...box, borderBox: false, scrollHeight: 20 + 3 * 20, minRows: 1, maxRows: 8 }).height, 60)
})

test('fractional measurements round up so the last line is never clipped', () => {
  assert.equal(growHeight({ ...box, scrollHeight: 64.4, minRows: 1, maxRows: 8 }).height, 67)
  assert.equal(growHeight({ lineHeight: 18.2, paddingY: 12, borderY: 2, borderBox: true, scrollHeight: 30, minRows: 1, maxRows: 4 }).height, 33)
})

test('bad measurements fall back instead of producing NaN', () => {
  const r = growHeight({ scrollHeight: NaN, lineHeight: NaN, paddingY: NaN, borderY: NaN, borderBox: true, minRows: 2, maxRows: 5 })
  assert.ok(Number.isFinite(r.height) && r.height >= 40)
  assert.equal(r.scrolls, false)
})

test('clampRows keeps row counts whole and at least one', () => {
  assert.equal(clampRows(undefined, 3), 3)
  assert.equal(clampRows(0, 3), 1)
  assert.equal(clampRows(2.9, 3), 2)
  assert.equal(clampRows(NaN, 4), 4)
})
