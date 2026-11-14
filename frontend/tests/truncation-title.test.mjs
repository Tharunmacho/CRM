import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'

async function load(relative) {
  const source = await readFile(new URL(relative, import.meta.url), 'utf8')
  const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } })
  return import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`)
}

const { isClipped, installTruncationTitles } = await load('../src/shared/lib/truncation-title.ts')
const fits = { textOverflow: 'clip', lineClamp: 'none', scrollWidth: 100, clientWidth: 100, scrollHeight: 20, clientHeight: 20 }

test('text that fits is never reported as clipped', () => {
  assert.equal(isClipped({ ...fits, textOverflow: 'ellipsis' }), false)
})

test('an ellipsis box whose text is wider than the box is clipped', () => {
  assert.equal(isClipped({ ...fits, textOverflow: 'ellipsis', scrollWidth: 240 }), true)
})

test('a wide box without an ellipsis is not ours to title', () => {
  assert.equal(isClipped({ ...fits, scrollWidth: 240 }), false)
})

test('a line clamp that hides lines is clipped, one that hides nothing is not', () => {
  assert.equal(isClipped({ ...fits, lineClamp: '2', scrollHeight: 60 }), true)
  assert.equal(isClipped({ ...fits, lineClamp: '2', scrollHeight: 40, clientHeight: 40 }), false)
})

test('a one pixel rounding difference does not count', () => {
  assert.equal(isClipped({ ...fits, textOverflow: 'ellipsis', scrollWidth: 101 }), false)
})

test('installing outside a browser does nothing', () => {
  assert.doesNotThrow(() => installTruncationTitles())
})
