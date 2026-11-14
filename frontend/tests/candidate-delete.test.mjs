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

test('a candidate on an order can be deleted only after the admin confirms removing them from it', () => {
  assert.equal(deleteCandidateBlocker([]), null)
  assert.equal(deleteCandidateBlocker([{ status: 'closed', orderRef: 'ORD-1' }]), null)
  assert.deepEqual(deleteCandidateBlocker([{ status: 'closed' }, { status: 'active', orderRef: 'ORD-2' }]),
    { message: 'This candidate is still on order ORD-2.', canWithdraw: true, orderRefs: ['ORD-2'] })
  assert.deepEqual(deleteCandidateBlocker([{ status: 'active', orderRef: 'ORD-2' }, { status: 'active', orderRef: 'ORD-5' }, { status: 'active', orderRef: 'ORD-2' }]),
    { message: 'This candidate is still on order ORD-2, ORD-5.', canWithdraw: true, orderRefs: ['ORD-2', 'ORD-5'] })
  assert.deepEqual(deleteCandidateBlocker([{ status: 'active' }]),
    { message: 'This candidate is still on an order.', canWithdraw: true, orderRefs: [] })
})

test('a deployed candidate is blocked with a plain message and no override, even if also on an active order', () => {
  const deployed = deleteCandidateBlocker([{ status: 'deployed', orderRef: 'ORD-3' }])
  assert.match(deployed.message, /deployed through ORD-3, and a deployed placement is kept on record/)
  assert.equal(deployed.canWithdraw, false)
  assert.match(deleteCandidateBlocker([{ status: 'deployed' }]).message, /^This candidate was deployed, and/)
  const both = deleteCandidateBlocker([{ status: 'active', orderRef: 'ORD-4' }, { status: 'deployed', orderRef: 'ORD-3' }])
  assert.equal(both.canWithdraw, false)
  assert.match(both.message, /deployed through ORD-3/)
})
