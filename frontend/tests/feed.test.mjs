import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'

async function load(path) {
  const source = await readFile(new URL(path, import.meta.url), 'utf8')
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
  })
  return import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}#${Math.random()}`)
}

const { createFeed } = await load('../src/shared/lib/feed.ts')

/** A load the test releases by hand; records how many reads were started. */
function manualLoad() {
  const reads = []
  const load = (signal, fresh) => new Promise((resolve, reject) => {
    const read = { fresh, aborted: false, resolve, reject }
    signal.addEventListener('abort', () => { read.aborted = true })
    reads.push(read)
  })
  return { reads, load }
}
const settle = () => new Promise((resolve) => setTimeout(resolve, 0))

test('three watchers of one list cause one read and all see the answer', async () => {
  const { reads, load } = manualLoad()
  const feed = createFeed({ load })
  const seen = [0, 0, 0]
  const stops = [0, 1, 2].map((i) => feed.subscribe(() => { seen[i] += 1 }))
  assert.equal(reads.length, 1)
  assert.equal(feed.getState().loading, true)
  reads[0].resolve({ n: 1 })
  await settle()
  assert.deepEqual(feed.getState(), { data: { n: 1 }, error: '', loading: false })
  assert.deepEqual(seen, [1, 1, 1])
  stops.forEach((stop) => stop())
})

test('live attachment happens once for all watchers and is released with the last one', async () => {
  const { reads, load } = manualLoad()
  let attached = 0
  let detached = 0
  const feed = createFeed({ load, attach: () => { attached += 1; return () => { detached += 1 } } })
  const a = feed.subscribe(() => {})
  const b = feed.subscribe(() => {})
  assert.equal(attached, 1)
  a()
  assert.equal(detached, 0)
  b()
  assert.equal(detached, 1)
  assert.equal(feed.watchers(), 0)
  reads[0].resolve({})
})

test('data is kept after the last watcher leaves: coming back shows it at once and re-reads without a loading state', async () => {
  const { reads, load } = manualLoad()
  const feed = createFeed({ load })
  let stop = feed.subscribe(() => {})
  reads[0].resolve({ n: 1 })
  await settle()
  stop()
  stop = feed.subscribe(() => {})
  assert.deepEqual(feed.getState().data, { n: 1 })
  assert.equal(feed.getState().loading, false)
  assert.equal(reads.length, 2)
  reads[1].resolve({ n: 2 })
  await settle()
  assert.deepEqual(feed.getState().data, { n: 2 })
  stop()
})

test('a read nobody waits for any more is cancelled and its late answer is ignored', async () => {
  const { reads, load } = manualLoad()
  const feed = createFeed({ load })
  feed.subscribe(() => {})()
  assert.equal(reads[0].aborted, true)
  reads[0].resolve({ stale: true })
  await settle()
  assert.equal(feed.getState().data, null)
})

test('a burst of changes while a read runs is answered by exactly one follow-up read', async () => {
  const { reads, load } = manualLoad()
  let reload
  const feed = createFeed({ load, attach: (fn) => { reload = fn; return () => {} } })
  const stop = feed.subscribe(() => {})
  void reload({ afterChange: true })
  void reload({ afterChange: true })
  void reload({ afterChange: true })
  assert.equal(reads.length, 1)
  reads[0].resolve({ n: 1 })
  await settle()
  assert.equal(reads.length, 2)
  reads[1].resolve({ n: 2 })
  await settle()
  assert.equal(reads.length, 2)
  assert.deepEqual(feed.getState().data, { n: 2 })
  stop()
})

test('a plain reload while a read runs joins it (no extra request)', async () => {
  const { reads, load } = manualLoad()
  let reload
  const feed = createFeed({ load, attach: (fn) => { reload = fn; return () => {} } })
  const stop = feed.subscribe(() => {})
  void reload()
  void reload()
  assert.equal(reads.length, 1)
  reads[0].resolve({})
  await settle()
  assert.equal(reads.length, 1)
  stop()
})

test('a failure keeps the data on screen and shows the message; the next read clears it', async () => {
  const { reads, load } = manualLoad()
  const feed = createFeed({ load, failure: 'Could not load.' })
  const stop = feed.subscribe(() => {})
  reads[0].resolve({ n: 1 })
  await settle()
  void feed.reload()
  reads[1].reject(new Error('Cannot reach the server.'))
  await settle()
  assert.deepEqual(feed.getState(), { data: { n: 1 }, error: 'Cannot reach the server.', loading: false })
  void feed.reload()
  reads[2].resolve({ n: 3 })
  await settle()
  assert.deepEqual(feed.getState(), { data: { n: 3 }, error: '', loading: false })
  stop()
})

test('a first failure ends the loading state', async () => {
  const { reads, load } = manualLoad()
  const feed = createFeed({ load, failure: 'Could not load.' })
  const stop = feed.subscribe(() => {})
  reads[0].reject('weird')
  await settle()
  assert.deepEqual(feed.getState(), { data: null, error: 'Could not load.', loading: false })
  stop()
})

test('an explicit refresh asks for a fresh read, and also while another is running', async () => {
  const { reads, load } = manualLoad()
  const feed = createFeed({ load })
  const stop = feed.subscribe(() => {})
  void feed.reload({ fresh: true })
  assert.equal(reads.length, 1)
  reads[0].resolve({ n: 1 })
  await settle()
  assert.equal(reads.length, 2)
  assert.equal(reads[1].fresh, true)
  reads[1].resolve({ n: 2 })
  await settle()
  stop()
})

test('unchanged state notifies nobody', async () => {
  const feed = createFeed({ load: async () => ({ n: 1 }) })
  let notified = 0
  const stop = feed.subscribe(() => { notified += 1 })
  await settle()
  assert.equal(notified, 1)
  stop()
})
