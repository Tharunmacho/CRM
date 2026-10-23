import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'

async function load(path) {
  const source = await readFile(new URL(path, import.meta.url), 'utf8')
  const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText
  return import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`)
}

const pools = await load('../src/features/candidates/data-bank-pools.ts')
const actions = await load('../src/features/candidates/profile-actions.ts')

test('an unassigned candidate reads "Needs assignment", never a blank or dash', () => {
  assert.equal(pools.ownerLabel(undefined), 'Needs assignment')
  assert.equal(pools.ownerLabel(null), 'Needs assignment')
  assert.equal(pools.ownerLabel('   '), 'Needs assignment')
  assert.equal(pools.ownerLabel('Asha Nair'), 'Asha Nair')
  assert.equal(pools.NEEDS_ASSIGNMENT_LABEL, 'Needs assignment')
})

test('a summary card toggles its pool filter and a second press returns to everyone', () => {
  assert.equal(pools.togglePool('all', 'needs_review'), 'needs_review')
  assert.equal(pools.togglePool('needs_review', 'needs_review'), 'all')
  assert.equal(pools.togglePool('unassigned', 'needs_review'), 'needs_review')
})

test('pool counts follow the staff scope and encode it', () => {
  assert.equal(pools.poolCountsPath(), '/candidates/pool-counts')
  assert.equal(pools.poolCountsPath(''), '/candidates/pool-counts')
  assert.equal(pools.poolCountsPath('a b&c'), '/candidates/pool-counts?staff=a+b%26c')
})

test('Assign waiting candidates is offered to supervisors only while somebody is waiting', () => {
  assert.equal(pools.canAssignWaiting({ unassigned: 3 }, true), true)
  assert.equal(pools.canAssignWaiting({ unassigned: 0 }, true), false)
  assert.equal(pools.canAssignWaiting({ unassigned: 3 }, false), false)
  assert.equal(pools.canAssignWaiting(null, true), false)
  assert.equal(pools.assignWaitingLabel(3), 'Assign waiting candidates (3)')
  assert.equal(pools.assignWaitingLabel(3, true), 'Assigning…')
})

test('the assignment outcome says what happened and when nobody is eligible', () => {
  assert.deepEqual(pools.assignWaitingMessage({ assigned: 2, reviewCleared: 2, remaining: 0 }), { text: 'Assigned 2 waiting candidates.', tone: 'ok' })
  assert.deepEqual(pools.assignWaitingMessage({ assigned: 1, reviewCleared: 0, remaining: 2 }),
    { text: 'Assigned 1 waiting candidate. 2 candidates still waiting for an eligible recruiter.', tone: 'warn' })
  assert.equal(pools.assignWaitingMessage({ assigned: 0, reviewCleared: 0, remaining: 4 }).tone, 'warn')
})

test('the new pools have their own empty states', () => {
  assert.match(pools.emptyPoolCopy('unassigned').title, /waiting for a recruiter/)
  assert.match(pools.emptyPoolCopy('needs_review').title, /Nothing needs review/)
  assert.equal(pools.emptyPoolCopy('all'), null)
})

test('Map to an order explains why it is unavailable', () => {
  assert.match(actions.mapToOrderBlocker({ ownerId: null }, false), /^Assign an active recruiter first/)
  assert.match(actions.mapToOrderBlocker({ ownerId: 'u1' }, false), /inactive/)
  assert.equal(actions.mapToOrderBlocker({ ownerId: 'u1' }, true), null)
})

test('only a string conversation id produces the WhatsApp button', () => {
  assert.equal(actions.linkedConversationId({ conversation: { id: 'c-1' } }), 'c-1')
  assert.equal(actions.linkedConversationId({ conversation: null }), '')
  assert.equal(actions.linkedConversationId(null), '')
  assert.equal(actions.linkedConversationId({ conversation: { id: 7 } }), '')
})
