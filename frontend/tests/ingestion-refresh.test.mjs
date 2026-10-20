import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'

const source = await readFile(new URL('../src/shared/api/ingestion-refresh.ts', import.meta.url), 'utf8')
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
})
const { createIngestionRefresh } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`)
const deferred = () => {
  let resolve, reject
  const promise = new Promise((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}

function workspace() {
  let server = { revision: 'r0', candidates: [{ id: 'walk-in', documents: [] }] }
  let loaded = structuredClone(server)
  let current = true
  let reads = 0, refreshes = 0
  const options = {
    currentRevision: () => loaded.revision,
    readRevision: async () => { reads++; return { revision: server.revision } },
    refresh: async () => { refreshes++; loaded = structuredClone(server) },
    isCurrentSession: () => current,
  }
  return {
    options,
    get server() { return server }, set server(value) { server = value },
    get loaded() { return loaded }, set loaded(value) { loaded = value },
    get current() { return current }, set current(value) { current = value },
    get reads() { return reads }, get refreshes() { return refreshes },
  }
}

test('unchanged initial and repeated probes do not reload the workspace', async () => {
  const state = workspace()
  const observer = createIngestionRefresh(state.options)
  await observer.check()
  await observer.check()
  assert.equal(state.reads, 2)
  assert.equal(state.refreshes, 0)
})

test('a completion updates an already-open Data Bank without visiting Inbox', async () => {
  const state = workspace()
  const observer = createIngestionRefresh(state.options)
  await observer.check()
  state.server = { revision: 'r1', candidates: [...state.server.candidates, { id: 'email', documents: ['resume'] }] }
  await observer.check()
  assert.deepEqual(state.loaded.candidates.map(candidate => candidate.id), ['walk-in', 'email'])
  await observer.check()
  assert.equal(state.refreshes, 1)
})

test('enrichment refreshes existing candidate documents even when IDs and count stay the same', async () => {
  const state = workspace()
  const observer = createIngestionRefresh(state.options)
  state.server = { revision: 'r1', candidates: [{ id: 'walk-in', documents: ['resume-v2', 'passport'] }] }
  await observer.check()
  assert.deepEqual(state.loaded.candidates, state.server.candidates)
  assert.equal(state.loaded.candidates.length, 1)
  assert.equal(state.refreshes, 1)
})

test('completion between bootstrap and the first probe is not lost as a baseline', async () => {
  const state = workspace()
  state.server = { revision: 'r1', candidates: [{ id: 'arrived-before-first-poll', documents: [] }] }
  const observer = createIngestionRefresh(state.options)
  await observer.check()
  assert.equal(state.loaded.candidates[0].id, 'arrived-before-first-poll')
  assert.equal(state.refreshes, 1)
})

test('a failed refresh keeps the old revision and data, then retries the same completion', async () => {
  const state = workspace()
  state.server = { revision: 'r1', candidates: [{ id: 'email', documents: [] }] }
  let fail = true
  const observer = createIngestionRefresh({
    ...state.options,
    refresh: async () => {
      if (fail) throw new Error('Temporary network failure')
      await state.options.refresh()
    },
  })
  await assert.rejects(observer.check(), /Temporary network/)
  assert.equal(state.loaded.revision, 'r0')
  assert.equal(state.loaded.candidates[0].id, 'walk-in')
  fail = false
  await observer.check()
  assert.equal(state.loaded.revision, 'r1')
})

test('a probe error can recover without refreshing unchanged candidate data', async () => {
  const state = workspace()
  let fail = true
  const observer = createIngestionRefresh({
    ...state.options,
    readRevision: async () => {
      if (fail) throw new Error('Unavailable')
      return state.options.readRevision()
    },
  })
  await assert.rejects(observer.check(), /Unavailable/)
  fail = false
  await observer.check()
  assert.equal(state.refreshes, 0)
})

test('concurrent checks share the probe and remain guarded until the workspace refresh settles', async () => {
  const state = workspace()
  const probe = deferred()
  const refresh = deferred()
  let reads = 0, refreshes = 0
  const observer = createIngestionRefresh({
    ...state.options,
    readRevision: () => { reads++; return probe.promise },
    refresh: () => { refreshes++; return refresh.promise },
  })
  const first = observer.check()
  assert.equal(observer.check(), first)
  await Promise.resolve()
  assert.equal(reads, 1)
  probe.resolve({ revision: 'r1' })
  await new Promise(resolve => setImmediate(resolve))
  assert.equal(observer.check(), first)
  assert.equal(refreshes, 1)
  assert.equal(reads, 1)
  refresh.resolve()
  await first
})

test('session invalidation prevents a late revision from refreshing another user', async () => {
  const state = workspace()
  const probe = deferred()
  const observer = createIngestionRefresh({ ...state.options, readRevision: () => probe.promise })
  const pending = observer.check()
  await Promise.resolve()
  state.current = false
  probe.resolve({ revision: 'r1' })
  await pending
  await observer.check()
  assert.equal(state.refreshes, 0)
  assert.equal(state.reads, 0)
})

test('disposing aborts the probe and ignores a late successful response', async () => {
  const state = workspace()
  const probe = deferred()
  let signal
  const observer = createIngestionRefresh({
    ...state.options,
    readRevision: (value) => { signal = value; return probe.promise },
  })
  const pending = observer.check()
  await Promise.resolve()
  observer.dispose()
  assert.equal(signal.aborted, true)
  probe.resolve({ revision: 'r1' })
  await pending
  await observer.check()
  assert.equal(state.refreshes, 0)
})

test('missing revision fields on older deployments do not trigger repeated full downloads', async () => {
  for (const current of [undefined, 'r0']) {
    const state = workspace()
    state.loaded = { ...state.loaded, revision: current }
    const observer = createIngestionRefresh({
      ...state.options, readRevision: async () => current === undefined ? { revision: 'r1' } : {},
    })
    await observer.check()
    await observer.check()
    assert.equal(state.refreshes, 0)
  }
})
