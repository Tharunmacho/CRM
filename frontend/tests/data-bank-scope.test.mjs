import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import ts from 'typescript'

async function load(path) {
  const source = await readFile(new URL(path, import.meta.url), 'utf8')
  const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText
  return import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`)
}
const scope = await load('../src/features/candidates/data-bank-scope.ts')

test('only staff open on their own new candidates, and only when the link names no recruiter or pool', () => {
  assert.equal(scope.startsOnMyNew('staff', null, null), true)
  assert.equal(scope.startsOnMyNew('staff', '', ''), true)
  assert.equal(scope.startsOnMyNew('staff', 'u2', null), false)
  assert.equal(scope.startsOnMyNew('staff', null, 'unassigned'), false)
  assert.equal(scope.startsOnMyNew('admin', null, null), false)
  assert.equal(scope.startsOnMyNew('manager', null, null), false)
  assert.equal(scope.startsOnMyNew(undefined, null, null), false)
})

test('the count line reads as my new candidates, or as the whole bank exactly as before', () => {
  assert.equal(scope.countLine(false, 20, 140), 'Showing 20 of 140 candidates')
  assert.equal(scope.countLine(true, 1, 140), 'Showing 1 new candidate assigned to you')
  assert.equal(scope.countLine(true, 3, 140), 'Showing 3 new candidates assigned to you')
  assert.match(scope.myNewEmptyCopy().description, /still see everyone/)
  assert.equal(scope.SHOW_ALL_LABEL, 'Show all candidates')
})
