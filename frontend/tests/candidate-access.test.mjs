import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'

async function load(path) {
  const source = await readFile(new URL(path, import.meta.url), 'utf8')
  const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText
  return import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`)
}

const { canWorkCandidate, workRefusal, choosesRecruiter } = await load('../src/features/candidates/candidate-access.ts')

const asha = { id: 'asha', role: 'staff' }
const ravi = { id: 'ravi', role: 'staff' }
const manager = { id: 'maya', role: 'manager' }
const admin = { id: 'root', role: 'admin' }
const owned = { ownerId: 'asha' }

test('only the assigned recruiter, a manager or an admin can work a candidate', () => {
  assert.equal(canWorkCandidate(asha, owned), true)
  assert.equal(canWorkCandidate(ravi, owned), false)
  assert.equal(canWorkCandidate(manager, owned), true)
  assert.equal(canWorkCandidate(admin, owned), true)
})

test('a candidate nobody owns can only be worked by a manager or an admin', () => {
  const waiting = { ownerId: null }
  assert.equal(canWorkCandidate(asha, waiting), false)
  assert.equal(canWorkCandidate(manager, waiting), true)
  assert.equal(canWorkCandidate(admin, waiting), true)
})

test('nobody is allowed before the session or the candidate is known', () => {
  assert.equal(canWorkCandidate(undefined, owned), false)
  assert.equal(canWorkCandidate(asha, undefined), false)
  assert.equal(canWorkCandidate(null, null), false)
})

test('an unknown role that is not the owner gets nothing', () => {
  assert.equal(canWorkCandidate({ id: 'x', role: 'guest' }, owned), false)
})

test('the refusal names the owner and matches the server wording', async () => {
  assert.equal(workRefusal(asha, owned, 'Asha'), null)
  assert.equal(workRefusal(admin, owned, 'Asha'), null)
  assert.equal(workRefusal(ravi, owned, 'Asha'), 'This candidate is assigned to Asha. Only they, a manager or an admin can work on it.')
  assert.equal(workRefusal(ravi, owned), 'This candidate is assigned to another recruiter. Only they, a manager or an admin can work on it.')
  assert.equal(workRefusal(ravi, { ownerId: null }), 'This candidate has no recruiter yet. Only a manager or an admin can work on it.')
  // The server is the source of truth; the sentences must not drift apart.
  const server = await readFile(new URL('../../backend/app/core/deps.py', import.meta.url), 'utf8')
  assert.ok(server.includes('This candidate is assigned to {name or \'another recruiter\'}. Only they, a manager or an admin can work on it.'))
  assert.ok(server.includes('This candidate has no recruiter yet. Only a manager or an admin can work on it.'))
})

test('only admin and manager choose the recruiter when they add a candidate', () => {
  assert.equal(choosesRecruiter('admin'), true)
  assert.equal(choosesRecruiter('manager'), true)
  assert.equal(choosesRecruiter('staff'), false)
  assert.equal(choosesRecruiter(undefined), false)
})

test('the sidebar and the page say Candidate, the old route keeps working', async () => {
  const shell = await readFile(new URL('../src/app/layout/app-shell.tsx', import.meta.url), 'utf8')
  assert.match(shell, /\{ to: '\/add-candidate', label: 'Candidate', icon: UserPlus/)
  assert.doesNotMatch(shell, /label: 'Add Candidate'/)
  const page = await readFile(new URL('../src/screens/candidates/add-candidate.tsx', import.meta.url), 'utf8')
  assert.match(page, /title="Candidate"/)
  const routes = await readFile(new URL('../src/app/routes.tsx', import.meta.url), 'utf8')
  assert.match(routes, /path="add-candidate"/)
})

test('staff see that the candidate will be assigned to them; admin and manager keep the recruiter choice', async () => {
  const page = await readFile(new URL('../src/screens/candidates/add-candidate.tsx', import.meta.url), 'utf8')
  assert.match(page, /This candidate will be assigned to you/)
  assert.match(page, /picksRecruiter \? 'Save and assign' : 'Save candidate'/)
  assert.match(page, /Automatic: a free recruiter/)
  // The chosen recruiter goes with the create request itself, and only for the roles that may choose.
  assert.match(page, /picksRecruiter && form\.assignTo \? \{ assign_to: form\.assignTo \} : \{\}/)
  assert.doesNotMatch(page, /store\.reassign\(\[created\.id\]/)
})

test('the duplicate dialog hides Combine when the candidate belongs to another recruiter', async () => {
  const dialog = await readFile(new URL('../src/features/candidates/components/entry-dialogs.tsx', import.meta.url), 'utf8')
  assert.match(dialog, /canMerge && <Button variant="primary"/)
  assert.match(dialog, /Only they, a manager or an admin can add to that candidate/)
})
