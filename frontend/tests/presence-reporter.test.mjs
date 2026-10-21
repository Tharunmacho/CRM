import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'

const source = await readFile(new URL('../src/features/attendance/presence-reporter.ts', import.meta.url), 'utf8')
const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } })
const { createPresenceReporter } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`)
const flush = () => new Promise((resolve) => setImmediate(resolve))
function setup() {
  let now = 1000
  const sent = [], leaves = [], results = [], errors = []
  const reporter = createPresenceReporter({
    clientId: 'page-one', now: () => now,
    send: (pulse, signal) => new Promise((resolve, reject) => sent.push({ pulse, signal, resolve, reject })),
    leave: (pulse) => leaves.push(pulse), onResult: (value) => results.push(value), onError: (error) => errors.push(error),
  })
  return { reporter, sent, leaves, results, errors, advance: (ms) => { now += ms } }
}

test('presence ages use the monotonic clock despite device wall-clock changes', async () => {
  const env = setup(), original = Date.now
  try {
    Date.now = () => original() + 3_600_000
    env.reporter.publish(true)
    assert.equal(env.sent[0].pulse.interactionAgeSeconds, null)
    env.sent[0].resolve('idle'); await flush()
    assert.equal(env.reporter.recordInteraction(), true)
    env.reporter.publish(true)
    assert.equal(env.sent[1].pulse.interactionAgeSeconds, 0)
    env.sent[1].resolve('online'); await flush()
    Date.now = () => original() - 3_600_000
    env.advance(300_000); env.reporter.publish(true)
    assert.equal(env.sent[2].pulse.interactionAgeSeconds, 300)
    assert.ok(!('lastInteractionAt' in env.sent[2].pulse))
  } finally { Date.now = original; env.reporter.dispose() }
})

test('slow sends coalesce hide/show and recompute activity age at dispatch', async () => {
  const env = setup()
  env.reporter.recordInteraction(); env.reporter.publish(true)
  env.advance(299_000); env.reporter.publish(false); env.reporter.publish(true)
  assert.equal(env.sent.length, 1)
  env.advance(2000); env.sent[0].resolve('obsolete-online'); await flush()
  assert.equal(env.sent.length, 2)
  assert.deepEqual(env.sent[1].pulse, { clientId: 'page-one', sequence: 3, visible: true, interactionAgeSeconds: 301 })
  assert.deepEqual(env.results, [])
  env.sent[1].resolve('current-idle'); await flush()
  assert.deepEqual(env.results, ['current-idle'])
  env.reporter.dispose()
})

test('emergency leave is newer than pending heartbeat and later show is newer than leave', async () => {
  const env = setup()
  env.reporter.recordInteraction(); env.reporter.publish(true)
  env.reporter.publish(false)
  env.reporter.leave()
  assert.equal(env.leaves[0].sequence, 3)
  env.reporter.publish(true)
  env.sent[0].resolve('stale'); await flush()
  assert.equal(env.sent[1].pulse.sequence, 4)
  assert.deepEqual(env.results, [])
  env.sent[1].resolve('shown'); await flush()
  assert.deepEqual(env.results, ['shown'])
  env.reporter.dispose()
  assert.equal(env.leaves[1].sequence, 5)
})

test('obsolete failures and disposed responses cannot overwrite the latest page state', async () => {
  const env = setup()
  env.reporter.publish(true); env.reporter.publish(false)
  env.sent[0].reject(new Error('obsolete failure')); await flush()
  assert.deepEqual(env.errors, [])
  env.reporter.dispose()
  assert.equal(env.sent[1].signal.aborted, true)
  env.sent[1].resolve('late result'); await flush()
  assert.deepEqual(env.results, [])
  env.reporter.publish(true)
  assert.equal(env.sent.length, 2)
})

test('current failures are reported and long inactivity remains within the server bound', async () => {
  const env = setup()
  env.reporter.recordInteraction(); env.advance(100_000_000); env.reporter.publish(true)
  assert.equal(env.sent[0].pulse.interactionAgeSeconds, 86400)
  env.sent[0].reject(new Error('connection lost')); await flush()
  assert.equal(env.errors[0].message, 'connection lost')
  env.reporter.publish(true)
  assert.equal(env.sent[1].pulse.sequence, 2)
  env.reporter.dispose()
})
