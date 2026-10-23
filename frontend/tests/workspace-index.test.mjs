import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'

const source = await readFile(new URL('../src/shared/state/workspace-index.ts', import.meta.url), 'utf8')
const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } })
const { indexWorkspace } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`)

test('indexed desk preserves historical applications and latest remarks without changing server order', () => {
  const data = { candidates: [{ id: 'c1', ownerId: 'staff1' }], users: [], clients: [], companies: [], orders: [], designations: [],
    tasks: [{ id: 't1', ownerId: 'staff1' }], applications: [
      { id: 'past', candidateId: 'c1', status: 'closed' }, { id: 'current', candidateId: 'c1', status: 'active' },
      { id: 'other', candidateId: 'c2', status: 'active' },
    ], remarks: [
      { id: 'old', taskId: 't1', createdAt: '2026-10-21T09:00:00Z' },
      { id: 'other', taskId: 't2', createdAt: '2026-10-22T11:00:00Z' },
      { id: 'new', taskId: 't1', createdAt: '2026-10-22T10:00:00Z' },
    ] }
  const original = structuredClone(data)
  const index = indexWorkspace(data)
  assert.equal(index.activeApplications.get('c1').id, 'current')
  assert.deepEqual(index.applications.get('c1').map(a => a.id), ['past', 'current'])
  assert.deepEqual(index.remarks.get('t1').map(a => a.id), ['new', 'old'])
  assert.equal(index.candidates.get('missing'), undefined)
  assert.deepEqual(data, original)
  // A real transfer/reopen refresh must discard every old lookup, including an
  // application that is no longer active and task ownership that has changed.
  const next = indexWorkspace({ ...data, candidates: [{ id: 'c1', ownerId: 'staff2' }], tasks: [{ id: 't1', ownerId: 'staff2' }],
    applications: data.applications.map(a => a.id === 'current' ? { ...a, status: 'closed' } : a) })
  assert.equal(next.candidates.get('c1').ownerId, 'staff2')
  assert.equal(next.tasks.get('t1').ownerId, 'staff2')
  assert.equal(next.activeApplications.get('c1'), undefined)
  assert.equal(next.applications.get('c1').length, 2)
})
