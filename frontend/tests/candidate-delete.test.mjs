import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'

async function load(path) {
  const source = await readFile(new URL(path, import.meta.url), 'utf8')
  const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText
  return import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`)
}

const { canDeleteCandidate, deleteCandidateBlocker } = await load('../src/features/candidates/profile-actions.ts')

test('only an admin is offered Delete candidate', () => {
  assert.equal(canDeleteCandidate('admin'), true)
  for (const role of ['manager', 'staff', '', undefined]) assert.equal(canDeleteCandidate(role), false)
})

test('a candidate on an order or deployed is blocked with a plain message', () => {
  assert.equal(deleteCandidateBlocker([]), null)
  assert.equal(deleteCandidateBlocker([{ status: 'closed', orderRef: 'ORD-1' }]), null)
  assert.equal(deleteCandidateBlocker([{ status: 'closed' }, { status: 'active', orderRef: 'ORD-2' }]),
    'This candidate is still on an order. Remove them from the order first.')
  assert.match(deleteCandidateBlocker([{ status: 'deployed', orderRef: 'ORD-3' }]), /deployed through ORD-3, and a deployed placement is kept on record/)
  assert.match(deleteCandidateBlocker([{ status: 'deployed' }]), /^This candidate was deployed, and/)
})
