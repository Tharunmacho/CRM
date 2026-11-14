import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import ts from 'typescript'

async function load(path) {
  const source = await readFile(new URL(path, import.meta.url), 'utf8')
  const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText
  return import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`)
}
const model = await load('../src/features/candidates/request-allocation.ts')

test('only staff can request, and never a candidate that is already theirs', () => {
  const staff = { id: 'a', role: 'staff' }
  assert.equal(model.canRequestAllocation(staff, { id: 'c', ownerId: null }), true)
  assert.equal(model.canRequestAllocation(staff, { id: 'c', ownerId: 'b' }), true)
  assert.equal(model.canRequestAllocation(staff, { id: 'c', ownerId: 'a' }), false)
  assert.equal(model.canRequestAllocation({ id: 'x', role: 'admin' }, { id: 'c', ownerId: null }), false)
  assert.equal(model.canRequestAllocation({ id: 'm', role: 'manager' }, { id: 'c', ownerId: 'b' }), false)
  assert.equal(model.canRequestAllocation(null, { id: 'c', ownerId: null }), false)
  assert.equal(model.canRequestAllocation(staff, undefined), false)
})

test('the request goes to the candidate endpoint the server exposes', async () => {
  assert.equal(model.requestAllocationPath('abc'), '/candidates/abc/request-allocation')
  const server = await readFile(new URL('../../backend/app/api/routers/candidates.py', import.meta.url), 'utf8')
  assert.match(server, /@router\.post\("\/\{candidate_id\}\/request-allocation"\)/)
})

test('messages say what happened, including a repeat and an agency with no active admin', () => {
  assert.match(model.requestMessage({ requested: true, alreadyRequested: false, notified: 2 }, 'Ravi').text, /Request sent/)
  assert.match(model.requestMessage({ requested: true, alreadyRequested: true, notified: 0 }, 'Ravi').text, /already asked/)
  assert.equal(model.requestMessage({ requested: true, alreadyRequested: false, notified: 0 }, 'Ravi').tone, 'warn')
})

test('a requested candidate stays requested for every row and card, and listeners hear about it once', () => {
  model.resetRequested()
  let heard = 0
  const stop = model.subscribeRequested(() => { heard += 1 })
  assert.equal(model.hasRequested('a', 'c1'), false)
  model.markRequested('a', 'c1'); model.markRequested('a', 'c1')
  assert.equal(model.hasRequested('a', 'c1'), true)
  assert.equal(model.hasRequested('a', 'c2'), false)
  assert.equal(model.hasRequested('b', 'c1'), false, 'another signed-in person never sees it as requested')
  assert.equal(heard, 1)
  stop(); model.markRequested('a', 'c2')
  assert.equal(heard, 1)
  assert.equal(model.REQUEST_LABEL, 'Request')
  assert.equal(model.REQUESTED_LABEL, 'Requested')
  model.resetRequested()
})
