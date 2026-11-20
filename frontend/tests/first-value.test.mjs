import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'

async function load(path) {
  const source = await readFile(new URL(path, import.meta.url), 'utf8')
  const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } })
  return import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}#${Math.random()}`)
}
const { createFirstValueFilter } = await load('../src/shared/lib/first-value.ts')

test('the first value to arrive (a second read answering) is not a change; later different values are', () => {
  const seen = createFirstValueFilter()
  assert.equal(seen(''), '')
  assert.equal(seen('A'), '') // summary arrived: the list read at mount already covers it
  assert.equal(seen('A'), '')
  assert.equal(seen('B'), 'B') // the summary moved: re-read
  assert.equal(seen('B'), 'B')
  assert.equal(seen('A'), '') // back to the first: still a change of the dependency ('B' to ''), the caller re-reads once
})

test('another person starts over', () => {
  const seen = createFirstValueFilter()
  seen('A', 'u1')
  assert.equal(seen('B', 'u1'), 'B')
  assert.equal(seen('', 'u2'), '')
  assert.equal(seen('B', 'u2'), '')
})
