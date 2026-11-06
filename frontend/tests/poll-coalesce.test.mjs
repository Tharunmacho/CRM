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

const { createCoalescer } = await load('../src/shared/lib/coalesce.ts')
const { createPoller } = await load('../src/shared/lib/poll.ts')

/** A manual clock: timers fire only when the test advances time. */
function clock() {
  let time = 0
  let nextHandle = 1
  const timers = new Map()
  return {
    now: () => time,
    setTimeout: (callback, ms) => { const handle = nextHandle++; timers.set(handle, { at: time + ms, callback }); return handle },
    clearTimeout: (handle) => { timers.delete(handle) },
    async advance(ms) {
      const target = time + ms
      for (;;) {
        const due = [...timers.entries()].filter(([, timer]) => timer.at <= target).sort((a, b) => a[1].at - b[1].at)[0]
        if (!due) break
        timers.delete(due[0])
        time = due[1].at
        due[1].callback()
        await Promise.resolve() // let promise callbacks settle between timers
        await Promise.resolve()
      }
      time = target
    },
    pending: () => timers.size,
  }
}

// ---- coalescer -------------------------------------------------------------------------------------------------

test('a burst of triggers runs once, after the last one', async () => {
  const c = clock()
  let runs = 0
  const trigger = createCoalescer(() => { runs += 1 }, { waitMs: 300, ...c })
  trigger(); await c.advance(100); trigger(); await c.advance(100); trigger()
  assert.equal(runs, 0)
  await c.advance(299)
  assert.equal(runs, 0)
  await c.advance(1)
  assert.equal(runs, 1)
  await c.advance(5000)
  assert.equal(runs, 1)
})

test('separate bursts run separately', async () => {
  const c = clock()
  let runs = 0
  const trigger = createCoalescer(() => { runs += 1 }, { waitMs: 300, ...c })
  trigger(); trigger(); await c.advance(300)
  trigger(); await c.advance(300)
  assert.equal(runs, 2)
})

test('maxWaitMs stops a steady stream from postponing the call for ever', async () => {
  const c = clock()
  let runs = 0
  const trigger = createCoalescer(() => { runs += 1 }, { waitMs: 300, maxWaitMs: 1000, ...c })
  for (let i = 0; i < 20; i++) { trigger(); await c.advance(200) } // 4 s of events every 200 ms
  assert.ok(runs >= 3, `ran ${runs} times`)
  assert.ok(runs <= 5, `ran ${runs} times`)
})

test('leading runs at once for the first trigger and once more at the end if more arrived', async () => {
  const c = clock()
  let runs = 0
  const trigger = createCoalescer(() => { runs += 1 }, { waitMs: 300, leading: true, ...c })
  trigger()
  assert.equal(runs, 1)
  trigger(); trigger()
  assert.equal(runs, 1)
  await c.advance(300)
  assert.equal(runs, 2)
  trigger() // a new burst
  assert.equal(runs, 3)
  await c.advance(300)
  assert.equal(runs, 3) // nothing else arrived
})

test('flush runs a pending call now; cancel drops it', async () => {
  const c = clock()
  let runs = 0
  const trigger = createCoalescer(() => { runs += 1 }, { waitMs: 300, ...c })
  trigger()
  assert.equal(trigger.pending(), true)
  trigger.flush()
  assert.equal(runs, 1)
  assert.equal(trigger.pending(), false)
  trigger()
  trigger.cancel()
  await c.advance(1000)
  assert.equal(runs, 1)
  trigger.flush() // nothing pending: no call
  assert.equal(runs, 1)
})

// ---- poller ----------------------------------------------------------------------------------------------------

test('a poller ticks at its interval and follows interval changes', async () => {
  const c = clock()
  let interval = 1000
  let runs = 0
  const poller = createPoller({ run: () => { runs += 1 }, intervalMs: () => interval, ...c })
  poller.start()
  await c.advance(3500)
  assert.equal(runs, 3)
  interval = 10_000 // the live stream came up
  poller.rearm()
  await c.advance(9000)
  assert.equal(runs, 3)
  await c.advance(1000)
  assert.equal(runs, 4)
  poller.stop()
  await c.advance(60_000)
  assert.equal(runs, 4)
  assert.equal(c.pending(), 0)
})

test('nothing runs while the tab is hidden, and one catch-up run happens when it is shown', async () => {
  const c = clock()
  let visible = true
  let runs = 0
  const poller = createPoller({ run: () => { runs += 1 }, intervalMs: () => 1000, isVisible: () => visible, ...c })
  poller.start()
  await c.advance(1000)
  assert.equal(runs, 1)
  visible = false
  await c.advance(10_000)
  assert.equal(runs, 1)
  visible = true
  poller.shown()
  assert.equal(runs, 2) // exactly one, not ten
  poller.shown()
  assert.equal(runs, 2) // nothing was missed since
  await c.advance(1000)
  assert.equal(runs, 3)
})

test('failures slow the poller down (doubling, capped) and a success restores the pace', async () => {
  const c = clock()
  let fail = true
  const times = []
  const poller = createPoller({
    run: () => { times.push(c.now()); return fail ? Promise.reject(new Error('down')) : Promise.resolve() },
    intervalMs: () => 1000, maxBackoff: 4, ...c,
  })
  poller.start()
  await c.advance(30_000)
  const gaps = times.slice(1).map((t, i) => t - times[i])
  assert.deepEqual(gaps.slice(0, 4), [2000, 4000, 4000, 4000]) // 1000 base: x2 after the first failure, capped at x4
  assert.equal(poller.backoff(), 4)
  fail = false
  await c.advance(5000) // the next (slow) attempt succeeds
  assert.equal(poller.backoff(), 1)
  const before = times.length
  await c.advance(3000)
  assert.equal(times.length - before, 3) // back to every second
})

test('a run that throws counts as a failure and does not stop the poller', async () => {
  const c = clock()
  let runs = 0
  const poller = createPoller({ run: () => { runs += 1; throw new Error('sync failure') }, intervalMs: () => 1000, ...c })
  poller.start()
  await c.advance(10_000)
  assert.ok(runs >= 3)
  assert.ok(poller.backoff() > 1)
})
