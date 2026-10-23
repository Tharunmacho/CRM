import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'

const source = await readFile(new URL('../src/features/whatsapp/send-recovery.ts', import.meta.url), 'utf8')
const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText
const { classifySendFailure } = await import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`)
const current = () => true

test('expired-window 409 with no durable request unlocks the draft for an approved template', async () => {
  let checked = 0
  const result = await classifySendFailure(409, async () => { checked++; throw { status: 404 } }, current)
  assert.equal(result, 'rejected')
  assert.equal(checked, 1)
})

test('duplicate-key 409 resolves the committed text or media intent without another submission', async () => {
  for (const type of ['text', 'document']) {
    assert.equal(await classifySendFailure(409, async () => ({ message: { type, state: 'queued' } }), current), 'recorded')
  }
})

test('lookup failure after 409 keeps the original key instead of unlocking a new send', async () => {
  for (const status of [0, 403, 500, 503]) {
    assert.equal(await classifySendFailure(409, async () => { throw { status } }, current), 'uncertain')
  }
})

test('a lost HTTP response stays uncertain and never infers rejection from an early missing read', async () => {
  let lookups = 0
  const result = await classifySendFailure(0, async () => { lookups++; throw { status: 404 } }, current)
  assert.equal(result, 'uncertain')
  assert.equal(lookups, 0)
})

test('known validation and permission failures release only the local send intent', async () => {
  for (const status of [400, 401, 403, 404, 413, 415, 422]) {
    assert.equal(await classifySendFailure(status, async () => assert.fail('No status lookup is needed'), current), 'rejected')
  }
})

test('a status response from a prior session cannot clear that session’s pending send draft', async () => {
  let active = true, complete
  const request = new Promise(resolve => { complete = resolve })
  const result = classifySendFailure(409, () => request, () => active)
  active = false
  complete({ message: { state: 'queued' } })
  assert.equal(await result, 'stale')
})

test('a missing-status response from a prior session cannot unlock the next account’s composer', async () => {
  let active = true, fail
  const request = new Promise((_resolve, reject) => { fail = reject })
  const result = classifySendFailure(409, () => request, () => active)
  active = false
  fail({ status: 404 })
  assert.equal(await result, 'stale')
})
