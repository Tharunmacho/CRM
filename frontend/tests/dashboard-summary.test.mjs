import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'

const source = await readFile(new URL('../src/features/dashboard/summary.ts', import.meta.url), 'utf8')
const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } })
const { candidatesAfterPrescreening, inView } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`)

test('a candidate with several applications counts once, and only candidates in view count', () => {
  const applications = [{ candidateId: 'a' }, { candidateId: 'a' }, { candidateId: 'b' }, { candidateId: 'z' }]
  assert.equal(candidatesAfterPrescreening(['a', 'b', 'c'], applications), 2)
  assert.equal(candidatesAfterPrescreening([], applications), 0)
})

test('supervisors see everyone or the chosen recruiter, staff only their own', () => {
  const rows = [{ id: 1, ownerId: 'u1' }, { id: 2, ownerId: 'u2' }, { id: 3, ownerId: null }]
  assert.equal(inView(rows, { id: 'u1', isTeam: true }).length, 3)
  assert.deepEqual(inView(rows, { id: 'u1', isTeam: true }, 'u2').map(r => r.id), [2])
  assert.deepEqual(inView(rows, { id: 'u1', isTeam: false }, 'u2').map(r => r.id), [1])
})
