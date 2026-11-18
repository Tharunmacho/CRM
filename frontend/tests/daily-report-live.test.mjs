import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'

const source = await readFile(new URL('../src/features/daily-report/daily-report-live.ts', import.meta.url), 'utf8')
const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText
const live = await import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`)

/** A clock we move by hand, so the 1.5 s window can be tested without waiting. */
function clock() {
  let now = 0, next = 1
  const timers = new Map()
  return {
    setTimer: (callback, ms) => { const id = next++; timers.set(id, { at: now + ms, callback }); return id },
    clearTimer: id => { timers.delete(id) },
    async advance(ms) {
      const end = now + ms
      for (;;) {
        const due = [...timers.entries()].filter(([, t]) => t.at <= end).sort((a, b) => a[1].at - b[1].at)[0]
        if (!due) break
        timers.delete(due[0]); now = due[1].at
        void due[1].callback() // a read may stay pending, so it is not awaited
        await Promise.resolve(); await Promise.resolve()
      }
      now = end
    },
    timers,
  }
}

test('the report follows leads, candidates, applications and stages, tasks and staff', () => {
  for (const topic of ['leads', 'candidates', 'applications', 'tasks', 'users', 'reports', 'daily_report']) assert.ok(live.LIVE_TOPICS.includes(topic), topic)
  assert.ok(!live.LIVE_TOPICS.includes('mail')) // an incoming email is not a report figure
})

test('a burst of events becomes one read, 1.5 s after the first', async () => {
  const time = clock()
  let reads = 0
  const coalescer = live.createCoalescer(() => { reads += 1 }, { setTimer: time.setTimer, clearTimer: time.clearTimer })
  coalescer.trigger(); await time.advance(400)
  coalescer.trigger(); await time.advance(400)
  coalescer.trigger(); await time.advance(600) // 1.4 s since the first
  assert.equal(reads, 0)
  assert.equal(coalescer.pending(), true)
  await time.advance(100) // 1.5 s
  assert.equal(reads, 1)
  assert.equal(coalescer.pending(), false)
  await time.advance(10_000)
  assert.equal(reads, 1) // nothing more without a new event
})

test('events during a read ask for exactly one more read afterwards (that read began before the change)', async () => {
  const time = clock()
  let reads = 0
  let release
  const coalescer = live.createCoalescer(() => { reads += 1; return new Promise(resolve => { release = resolve }) }, { setTimer: time.setTimer, clearTimer: time.clearTimer })
  coalescer.trigger(); await time.advance(1500)
  assert.equal(reads, 1) // running, not finished
  coalescer.trigger(); coalescer.trigger(); coalescer.trigger() // three more events while it runs
  assert.equal(time.timers.size, 0) // no second read started on top of the first
  release(); await Promise.resolve(); await Promise.resolve()
  assert.equal(coalescer.pending(), true)
  await time.advance(1500)
  assert.equal(reads, 2)
  release(); await Promise.resolve(); await Promise.resolve()
  await time.advance(10_000)
  assert.equal(reads, 2)
})

test('a failing read does not stop later events from being read, and a cancelled coalescer never fires', async () => {
  const time = clock()
  let reads = 0
  const coalescer = live.createCoalescer(() => { reads += 1; throw new Error('network') }, { setTimer: time.setTimer, clearTimer: time.clearTimer })
  coalescer.trigger(); await time.advance(1500)
  coalescer.trigger(); await time.advance(1500)
  assert.equal(reads, 2)
  coalescer.trigger()
  coalescer.cancel()
  await time.advance(5000)
  assert.equal(reads, 2)
  coalescer.trigger(); await time.advance(5000)
  assert.equal(reads, 2)
})

test('polling: 30 s while the stream is down, a 60 s safety net while it is up; focus reads again after 5 s', () => {
  assert.equal(live.COALESCE_MS, 1500)
  assert.equal(live.POLL_DOWN_MS, 30_000)
  assert.equal(live.POLL_SAFETY_MS, 60_000)
  assert.equal(live.FOCUS_GAP_MS, 5_000)
})

test('"Updated 12 s ago" text', () => {
  assert.equal(live.agoText(0), 'just now')
  assert.equal(live.agoText(4_900), 'just now')
  assert.equal(live.agoText(12_400), '12 s ago')
  assert.equal(live.agoText(59_999), '59 s ago')
  assert.equal(live.agoText(60_000), '1 min ago')
  assert.equal(live.agoText(185_000), '3 min ago')
  assert.equal(live.agoText(2 * 3600_000 + 5000), '2 h ago')
  assert.equal(live.agoText(-500), 'just now')
})

test('only the cells whose number moved are reported; a new cell or the first answer is not news', () => {
  assert.deepEqual(live.changedCells(null, { a: 1 }), [])
  assert.deepEqual(live.changedCells({ a: 1, b: 2, c: null }, { a: 1, b: 3, c: 4 }), ['b', 'c'])
  assert.deepEqual(live.changedCells({ a: 1 }, { a: 1, b: 9 }), [])
  assert.deepEqual(live.changedCells({ a: 1, b: 2 }, { a: 1 }), [])
})

test('the hook wires the stream, the polling, the burst window and the focus read as designed', async () => {
  const hook = await readFile(new URL('../src/features/daily-report/use-daily-report.ts', import.meta.url), 'utf8')
  assert.match(hook, /liveInterval\(.*POLL_DOWN_MS, LIVE_TOPICS, \{ safetyMs: POLL_SAFETY_MS/)
  assert.match(hook, /createCoalescer\(\(\) => read\(true\)/)
  assert.match(hook, /document\.visibilityState === 'hidden'/)
  assert.match(hook, /addEventListener\('focus'/)
  assert.match(hook, /version/) // an open drill-down page re-reads when the report was read again
  const drawer = await readFile(new URL('../src/features/daily-report/drilldown-drawer.tsx', import.meta.url), 'utf8')
  assert.match(drawer, /version\]\)/)
  const parts = await readFile(new URL('../src/features/daily-report/report-parts.tsx', import.meta.url), 'utf8')
  assert.match(parts, /aria-live="off"/)
  assert.match(parts, /Reconnecting/)
  const css = await readFile(new URL('../src/styles/daily-report.css', import.meta.url), 'utf8')
  assert.match(css, /dwr-flash \.6s/)
  assert.match(css, /prefers-reduced-motion: reduce[\s\S]*animation: none/)
})
