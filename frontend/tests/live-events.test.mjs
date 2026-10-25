import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { beforeEach, test } from 'node:test'
import ts from 'typescript'

async function load() {
  const source = await readFile(new URL('../src/shared/state/live-events.ts', import.meta.url), 'utf8')
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
  })
  return import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}#${Math.random()}`)
}

class FakeSource {
  static all = []
  constructor(url) {
    this.url = url
    this.readyState = 0
    this.closed = false
    this.handlers = new Map()
    this.onerror = null
    FakeSource.all.push(this)
  }
  addEventListener(type, handler) { this.handlers.set(type, handler) }
  close() { this.closed = true; this.readyState = 2 }
  emit(type, data) { this.handlers.get(type)?.({ data: data === undefined ? undefined : JSON.stringify(data) }) }
  fail() { this.onerror?.({}) }
}

// A manual clock: timers fire only when the test advances time.
function clock() {
  let now = 0
  let next = 1
  const timers = new Map()
  return {
    setTimeout: (callback, ms) => { const id = next++; timers.set(id, { at: now + ms, callback }); return id },
    clearTimeout: (id) => { timers.delete(id) },
    advance(ms) {
      const end = now + ms
      for (;;) {
        const due = [...timers.entries()].filter(([, t]) => t.at <= end).sort((a, b) => a[1].at - b[1].at)[0]
        if (!due) break
        timers.delete(due[0]); now = due[1].at; due[1].callback()
      }
      now = end
    },
    pending: () => timers.size,
  }
}

let live, time, visible, before
beforeEach(async () => {
  FakeSource.all = []
  time = clock()
  visible = true
  before = []
  const { createLiveEvents } = await load()
  live = createLiveEvents({
    url: '/api/events',
    createSource: (url) => new FakeSource(url),
    setTimeout: time.setTimeout,
    clearTimeout: time.clearTimeout,
    random: () => 1, // no jitter: delay = full backoff step
    isVisible: () => visible,
    beforeDispatch: (change) => before.push(change),
  })
})

const hello = (source) => source.emit('hello', { serverTime: 1, heartbeatMs: 20_000 })
const current = () => FakeSource.all[FakeSource.all.length - 1]

test('opens one stream on start, reports its status and closes it on stop', () => {
  assert.equal(live.status(), 'off')
  const seen = []
  live.onStatus((status) => seen.push(status))
  live.start(); live.start()
  assert.equal(FakeSource.all.length, 1)
  assert.equal(FakeSource.all[0].url, '/api/events')
  assert.equal(live.status(), 'connecting')
  hello(current())
  assert.equal(live.isUp(), true)
  live.stop()
  assert.equal(FakeSource.all[0].closed, true)
  assert.deepEqual(seen, ['connecting', 'up', 'off'])
  time.advance(120_000)
  assert.equal(FakeSource.all.length, 1, 'a stopped stream never reconnects')
})

test('only subscribers of a changed topic are called, with just their topics', () => {
  const tasks = [], clients = [], everything = []
  live.subscribe(['tasks', 'orders'], (c) => tasks.push(c), { debounceMs: 100 })
  live.subscribe(['clients'], (c) => clients.push(c), { debounceMs: 100 })
  live.subscribe('*', (c) => everything.push(c), { debounceMs: 100 })
  live.start(); hello(current())
  current().emit('change', { topics: ['tasks', 'clients', 'reports'], ids: { tasks: ['t1'], clients: ['c1'] }, at: 5 })
  time.advance(100)
  assert.deepEqual(tasks.map((c) => c.topics), [['tasks']])
  assert.deepEqual(tasks[0].ids, { tasks: ['t1'] })
  assert.deepEqual(clients.map((c) => c.topics), [['clients']])
  assert.deepEqual(everything[0].topics, ['tasks', 'clients', 'reports'])
  current().emit('change', { topics: ['mail'], at: 6 })
  time.advance(100)
  assert.equal(tasks.length, 1, 'an unrelated topic does not wake the screen')
  assert.equal(before.length, 2, 'beforeDispatch sees every change')
})

test('a burst is coalesced into one call within the window', () => {
  const calls = []
  live.subscribe(['candidates'], (c) => calls.push(c), { debounceMs: 350 })
  live.start(); hello(current())
  for (let i = 0; i < 20; i += 1) {
    current().emit('change', { topics: ['candidates'], ids: { candidates: [`c${i % 3}`] }, at: 100 + i })
    time.advance(10)
  }
  assert.equal(calls.length, 0, 'still inside the window')
  time.advance(350)
  assert.equal(calls.length, 1)
  assert.deepEqual(calls[0].ids.candidates.sort(), ['c0', 'c1', 'c2'])
  assert.equal(calls[0].at, 119)
  current().emit('change', { topics: ['candidates'], at: 500 })
  time.advance(350)
  assert.equal(calls.length, 2, 'the next burst starts a new window')
})

test('an unsubscribed screen is not called, even for a held change', () => {
  const calls = []
  const off = live.subscribe(['tasks'], (c) => calls.push(c), { debounceMs: 100 })
  live.start(); hello(current())
  current().emit('change', { topics: ['tasks'] })
  off()
  time.advance(500)
  assert.equal(calls.length, 0)
})

test('a dropped stream reconnects with growing backoff and then asks everyone to resync', () => {
  const calls = []
  live.subscribe(['tasks'], (c) => calls.push(c), { debounceMs: 50 })
  live.start(); hello(current())
  const states = []
  live.onStatus((s) => states.push(s))
  current().fail()
  assert.equal(live.status(), 'down')
  assert.equal(FakeSource.all[0].closed, true)
  time.advance(999); assert.equal(FakeSource.all.length, 1)
  time.advance(1); assert.equal(FakeSource.all.length, 2, 'first retry after 1 s')
  current().fail()
  time.advance(1_999); assert.equal(FakeSource.all.length, 2)
  time.advance(1); assert.equal(FakeSource.all.length, 3, 'second retry after 2 s')
  current().fail()
  time.advance(4_000); assert.equal(FakeSource.all.length, 4, 'third retry after 4 s')
  hello(current())
  assert.equal(live.status(), 'up')
  time.advance(50)
  assert.equal(calls.length, 1)
  assert.equal(calls[0].resync, true, 'changes announced during the outage were missed')
  assert.deepEqual(calls[0].topics, ['*'])
  assert.deepEqual(states, ['down', 'up'])
  current().fail()
  time.advance(1_000)
  assert.equal(FakeSource.all.length, 5, 'a successful hello resets the backoff to 1 s')
})

test('backoff is capped', () => {
  live.start()
  for (let i = 0; i < 12; i += 1) { current().fail(); time.advance(60_000) }
  const count = FakeSource.all.length
  current().fail()
  time.advance(59_999); assert.equal(FakeSource.all.length, count)
  time.advance(1); assert.equal(FakeSource.all.length, count + 1)
})

test('a stream that goes silent is replaced by the watchdog', () => {
  live.start(); hello(current())
  time.advance(44_999)
  assert.equal(live.isUp(), true)
  current().emit('ping', {}) // heartbeats keep it alive
  time.advance(44_999)
  assert.equal(live.isUp(), true)
  time.advance(1)
  assert.equal(live.status(), 'down')
  time.advance(1_000)
  assert.equal(FakeSource.all.length, 2)
})

test('nothing reaches a hidden tab until it is visible again', () => {
  const calls = []
  live.subscribe(['tasks'], (c) => calls.push(c), { debounceMs: 100 })
  live.start(); hello(current())
  visible = false
  current().emit('change', { topics: ['tasks'], at: 1 })
  time.advance(1_000)
  assert.equal(calls.length, 0)
  visible = true
  live.flushHeld()
  assert.equal(calls.length, 1)
  live.flushHeld()
  assert.equal(calls.length, 1, 'delivered once')
})

test('a goodbye resyncs the screens (so the 401 is noticed) and backs off', () => {
  const calls = []
  live.subscribe('*', (c) => calls.push(c), { debounceMs: 10 })
  live.start(); hello(current())
  current().emit('bye', { reason: 'signed-out' })
  time.advance(10)
  assert.equal(calls[0].resync, true)
  assert.equal(live.status(), 'down')
})

test('malformed events are ignored and a throwing screen does not stop others', () => {
  const good = []
  live.subscribe(['tasks'], () => { throw new Error('screen bug') }, { debounceMs: 10 })
  live.subscribe(['tasks'], (c) => good.push(c), { debounceMs: 10 })
  live.start(); hello(current())
  current().handlers.get('change')({ data: 'not json' })
  current().handlers.get('change')({ data: undefined })
  current().emit('change', { topics: ['tasks'] })
  time.advance(10)
  assert.equal(good.length, 1)
  assert.equal(live.isUp(), true)
})

test('events from a replaced stream are ignored', () => {
  const calls = []
  live.subscribe(['tasks'], (c) => calls.push(c), { debounceMs: 10 })
  live.start(); hello(current())
  const stale = current()
  stale.fail(); time.advance(1_000)
  hello(current())
  stale.emit('change', { topics: ['tasks'] })
  time.advance(10)
  assert.equal(calls.filter((c) => !c.resync).length, 0)
})
