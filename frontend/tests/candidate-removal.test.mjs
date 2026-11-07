import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'

async function load(path) {
  const source = await readFile(new URL(path, import.meta.url), 'utf8')
  const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText
  return import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`)
}

const {
  removeCandidate, restoreCandidate, markCandidateDeleted, unmarkCandidateDeleted, isCandidateDeleted, withoutDeleted, dropDeleted,
} = await load('../src/shared/state/candidate-removal.ts')

const workspace = () => ({
  settings: { name: 'Agency' },
  candidates: [{ id: 'c1' }, { id: 'c2' }, { id: 'c3' }],
  applications: [{ id: 'a1', candidateId: 'c1' }, { id: 'a2', candidateId: 'c2' }],
  tasks: [{ id: 't1', candidateId: 'c1' }, { id: 't2', candidateId: 'c1' }, { id: 't3', candidateId: 'c2' }],
  remarks: [{ id: 'r1', taskId: 't1' }, { id: 'r2', taskId: 't3' }],
  todos: [{ id: 'd1', linkTo: '/candidates/c1', linkLabel: 'Person' }, { id: 'd2', linkTo: '/candidates/c2', linkLabel: 'Other' }, { id: 'd3' }],
  notifications: [{ id: 'n1', linkTo: '/candidates/c1' }, { id: 'n2', linkTo: '/tasks?task=t2' }, { id: 'n3', linkTo: '/candidates/c2' }, { id: 'n4' }],
})

test('the candidate and everything the server deletes with them leave the workspace at once', () => {
  const before = workspace()
  const { data, removed } = removeCandidate(before, 'c1')
  assert.deepEqual(data.candidates.map((row) => row.id), ['c2', 'c3'])
  assert.deepEqual(data.applications.map((row) => row.id), ['a2'])
  assert.deepEqual(data.tasks.map((row) => row.id), ['t3'])
  assert.deepEqual(data.remarks.map((row) => row.id), ['r2'])
  assert.deepEqual(data.notifications.map((row) => row.id), ['n3', 'n4'])
  assert.equal(removed.candidates.length, 1)
  assert.equal(removed.tasks.length, 2)
  // The input is not changed, and what the delete does not touch keeps its identity.
  assert.equal(before.candidates.length, 3)
  assert.equal(data.settings, before.settings)
})

test('to-dos that linked to the candidate stay, with the link cleared (as on the server)', () => {
  const { data } = removeCandidate(workspace(), 'c1')
  assert.deepEqual(data.todos.map((row) => row.id), ['d1', 'd2', 'd3'])
  assert.equal(data.todos[0].linkTo, undefined)
  assert.equal(data.todos[0].linkLabel, undefined)
  assert.equal(data.todos[1].linkTo, '/candidates/c2')
})

test('a candidate nothing refers to changes only the candidate list; other arrays are the same objects', () => {
  const before = workspace()
  const { data } = removeCandidate(before, 'c3')
  assert.deepEqual(data.candidates.map((row) => row.id), ['c1', 'c2'])
  for (const key of ['applications', 'tasks', 'remarks', 'todos', 'notifications']) assert.equal(data[key], before[key], key)
})

test('an unknown candidate leaves the workspace as it was', () => {
  const before = workspace()
  const { data, removed } = removeCandidate(before, 'nobody')
  assert.equal(data.candidates, before.candidates)
  assert.equal(removed.candidates.length, 0)
})

test('a refused delete puts everything back, without duplicates', () => {
  const before = workspace()
  const { data, removed } = removeCandidate(before, 'c1')
  const restored = restoreCandidate(data, removed)
  const ids = (rows) => rows.map((row) => row.id).sort()
  assert.deepEqual(ids(restored.candidates), ids(before.candidates))
  assert.deepEqual(ids(restored.applications), ids(before.applications))
  assert.deepEqual(ids(restored.tasks), ids(before.tasks))
  assert.deepEqual(ids(restored.remarks), ids(before.remarks))
  assert.deepEqual(ids(restored.notifications), ids(before.notifications))
  assert.equal(restored.todos.find((row) => row.id === 'd1').linkTo, '/candidates/c1')
  // A refresh that already brought the candidate back must not leave two of them.
  const twice = restoreCandidate(restored, removed)
  assert.equal(twice.candidates.length, 3)
  assert.equal(twice.tasks.length, 3)
})

test('lists fetched before the delete finished never show the deleted candidate again', () => {
  const rows = [{ id: 'c1' }, { id: 'c2' }]
  assert.equal(withoutDeleted(rows), rows)
  markCandidateDeleted('c1')
  assert.equal(isCandidateDeleted('c1'), true)
  assert.deepEqual(withoutDeleted(rows).map((row) => row.id), ['c2'])
  // A stale workspace snapshot is cleaned the same way, applications and tasks included.
  const stale = dropDeleted(workspace())
  assert.deepEqual(stale.candidates.map((row) => row.id), ['c2', 'c3'])
  assert.deepEqual(stale.tasks.map((row) => row.id), ['t3'])
  unmarkCandidateDeleted('c1')
  assert.equal(isCandidateDeleted('c1'), false)
  assert.equal(withoutDeleted(rows), rows)
})
