import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'

async function loadSource(path) {
  const source = await readFile(new URL(path, import.meta.url), 'utf8')
  const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } })
  return import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`)
}

const { createDraftSaver, stableKey, DRAFT_INTERVAL_MS, DRAFT_SETTLE_MS } = await loadSource('../src/features/candidates/draft-saver.ts')

/** A virtual clock with timers, and a server whose answers the test releases by hand. */
function rig({ fail = false } = {}) {
  let time = 0
  let sequence = 0
  const timers = new Map()
  const sent = []
  const waiting = []
  const clock = {
    now: () => time,
    setTimer: (callback, ms) => { const id = ++sequence; timers.set(id, { at: time + ms, callback }); return id },
    clearTimer: (id) => { timers.delete(id) },
    advance(ms) {
      const end = time + ms
      for (;;) {
        const due = [...timers.entries()].filter(([, timer]) => timer.at <= end).sort((a, b) => a[1].at - b[1].at)[0]
        if (!due) break
        timers.delete(due[0]); time = Math.max(time, due[1].at); due[1].callback()
      }
      time = end
    },
  }
  const results = []
  const state = { fail }
  const saver = createDraftSaver({
    ...clock,
    send: (draft) => new Promise((resolve, reject) => {
      sent.push({ draft, at: time })
      waiting.push(() => (state.fail ? reject(new Error('down')) : resolve()))
    }),
    onResult: (error) => results.push(error ? 'error' : 'saved'),
  })
  const answer = async () => { waiting.shift()(); await new Promise((resolve) => setImmediate(resolve)) }
  return { saver, sent, clock, answer, results, state, timers, pendingTimers: () => timers.size }
}

const draft = (name, extra = {}) => ({ form: { name, phone: '9000000000' }, editedFields: ['name'], jobIds: ['j1'], savedCandidateId: null, ...extra })

test('a burst of typing becomes one request, with the latest values', async () => {
  const { saver, sent, clock, answer } = rig()
  saver.queue(draft('A')); clock.advance(100)
  saver.queue(draft('Al')); clock.advance(400)
  saver.queue(draft('Ali'))
  assert.equal(sent.length, 0)
  clock.advance(DRAFT_SETTLE_MS)
  assert.equal(sent.length, 1)
  assert.equal(sent[0].draft.form.name, 'Ali')
  await answer()
  assert.equal(saver.hasPending(), false)
})

test('the first change after a quiet spell waits only the settle time; the next ones wait for the interval', async () => {
  const { saver, sent, clock, answer } = rig()
  clock.advance(60_000)
  saver.queue(draft('A')); clock.advance(DRAFT_SETTLE_MS)
  assert.equal(sent.length, 1, 'nothing was sent for a minute, so only the settle time applies')
  await answer()
  saver.queue(draft('Ab')); clock.advance(1000)
  assert.equal(sent.length, 1, 'within 2.5 s of the last request')
  clock.advance(1500)
  assert.equal(sent.length, 2)
  assert.equal(sent[1].at - sent[0].at >= DRAFT_INTERVAL_MS, true)
})

test('never two requests at once: changes made while one is out go in ONE follow-up with the latest values', async () => {
  const { saver, sent, clock, answer } = rig()
  saver.queue(draft('A')); clock.advance(DRAFT_SETTLE_MS)
  assert.equal(sent.length, 1)
  for (const name of ['Ab', 'Abc', 'Abcd']) { saver.queue(draft(name)); clock.advance(300) }
  clock.advance(10_000)
  assert.equal(sent.length, 1, 'the first request has not been answered: nothing else may be sent')
  await answer()
  clock.advance(DRAFT_INTERVAL_MS)
  assert.equal(sent.length, 2)
  assert.equal(sent[1].draft.form.name, 'Abcd', 'only the latest values, not every intermediate draft')
  await answer()
  clock.advance(60_000)
  assert.equal(sent.length, 2)
})

test('a draft equal to the saved one is not sent, whatever the key order', async () => {
  const { saver, sent, clock, answer } = rig()
  saver.queue(draft('Same')); clock.advance(DRAFT_SETTLE_MS)
  await answer()
  saver.queue({ jobIds: ['j1'], savedCandidateId: null, editedFields: ['name'], form: { phone: '9000000000', name: 'Same' } })
  clock.advance(60_000)
  assert.equal(sent.length, 1)
  assert.equal(stableKey({ b: 1, a: [{ d: 1, c: 2 }] }), stableKey({ a: [{ c: 2, d: 1 }], b: 1 }))
  // typing back to what the server holds cancels the pending request too
  saver.queue(draft('Other')); saver.queue(draft('Same'))
  clock.advance(60_000)
  assert.equal(sent.length, 1)
})

test('flush sends now, waits for a request already out, and resolves when the latest values are saved', async () => {
  const { saver, sent, clock, answer } = rig()
  saver.queue(draft('A')); clock.advance(DRAFT_SETTLE_MS)
  saver.queue(draft('Ab'))
  let flushed = false
  const flush = saver.flush().then(() => { flushed = true })
  await new Promise((resolve) => setImmediate(resolve))
  assert.equal(sent.length, 1, 'waits for the request that is out')
  await answer()
  await new Promise((resolve) => setImmediate(resolve))
  assert.equal(sent.length, 2, 'then sends the latest values at once, not after 2.5 s')
  assert.equal(sent[1].draft.form.name, 'Ab')
  assert.equal(flushed, false)
  await answer()
  await flush
  assert.equal(flushed, true)
  await saver.flush()
  assert.equal(sent.length, 2, 'flushing an already saved draft sends nothing')
})

test('flush with a draft passes the values it was given', async () => {
  const { saver, sent, answer } = rig()
  const flush = saver.flush(draft('Direct'))
  await new Promise((resolve) => setImmediate(resolve))
  await answer()
  await flush
  assert.equal(sent.length, 1)
  assert.equal(sent[0].draft.form.name, 'Direct')
})

test('a failed save stays pending: flush rejects, and the next change tries again', async () => {
  const { saver, sent, clock, answer, state, results } = rig({ fail: true })
  saver.queue(draft('A')); clock.advance(DRAFT_SETTLE_MS)
  await answer()
  assert.deepEqual(results, ['error'])
  assert.equal(saver.hasPending(), true)
  clock.advance(60_000)
  assert.equal(sent.length, 1, 'no automatic hammering after a failure')
  state.fail = false
  saver.queue(draft('A')); clock.advance(2500)
  assert.equal(sent.length, 2)
  await answer()
  assert.deepEqual(results, ['error', 'saved'])
  assert.equal(saver.hasPending(), false)
  state.fail = true
  const flush = saver.flush(draft('B')).catch((error) => error.message)
  await new Promise((resolve) => setImmediate(resolve))
  await answer()
  assert.equal(await flush, 'down')
})

test('reset starts a new intake: the first draft is always sent', async () => {
  const { saver, sent, clock, answer } = rig()
  saver.queue(draft('A')); clock.advance(DRAFT_SETTLE_MS); await answer()
  saver.reset()
  saver.queue(draft('A')); clock.advance(DRAFT_SETTLE_MS)
  assert.equal(sent.length, 2)
})
