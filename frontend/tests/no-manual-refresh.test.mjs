import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import ts from 'typescript'

const read = (path) => readFile(new URL(`../src/${path}`, import.meta.url), 'utf8')
const load = async (path) => {
  const js = ts.transpileModule(await read(path), { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText
  return import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`)
}
const retry = await load('shared/state/refresh-retry.ts')

/** A virtual clock: sleeping advances time, nothing really waits. */
function clock() {
  let t = 0
  const slept = []
  return { now: () => t, sleep: async (ms) => { slept.push(ms); t += ms }, slept, elapsed: () => t }
}

test('the retry delays are 0.3, 0.6, 1.2, 2.4 s, then every 5 s, and stop after 30 s', () => {
  assert.deepEqual([0, 1, 2, 3, 4, 5, 9].map((n) => retry.nextRefreshDelay(n, 0)), [300, 600, 1200, 2400, 5000, 5000, 5000])
  assert.equal(retry.nextRefreshDelay(4, 29_000), 1000, 'the last wait never runs past the give-up time')
  assert.equal(retry.nextRefreshDelay(0, 30_000), null)
  assert.equal(retry.nextRefreshDelay(2, 45_000), null)
})

test('refreshUntil re-reads until the new application is on screen, quickly at first, and stops there', async () => {
  const c = clock()
  let reads = 0
  let present = false
  const outcome = await retry.refreshUntil({ read: async () => { reads += 1; if (reads === 2) present = true }, done: () => present, sleep: c.sleep, now: c.now })
  assert.equal(outcome, 'found')
  assert.equal(reads, 2)
  assert.deepEqual(c.slept, [300, 60, 600, 60], 'wait 0.3 s, read, settle, wait 0.6 s, read, settle')
})

test('refreshUntil returns at once when the application is already there (nothing is read, nothing is shown)', async () => {
  const c = clock()
  let reads = 0
  assert.equal(await retry.refreshUntil({ read: async () => { reads += 1 }, done: () => true, sleep: c.sleep, now: c.now }), 'found')
  assert.equal(reads, 0)
  assert.deepEqual(c.slept, [])
})

test('refreshUntil survives failed reads, keeps trying, and only gives up after 30 s', async () => {
  const c = clock()
  let reads = 0
  const outcome = await retry.refreshUntil({ read: async () => { reads += 1; throw new Error('offline') }, done: () => false, sleep: c.sleep, now: c.now })
  assert.equal(outcome, 'gave_up')
  assert.ok(reads >= 6, `read ${reads} times`)
  assert.ok(c.elapsed() >= 30_000 && c.elapsed() < 32_000, `gave up after ${c.elapsed()} ms`)
})

test('refreshUntil stops at once when the screen has closed or moved on', async () => {
  const c = clock()
  let cancelled = false
  let reads = 0
  const outcome = await retry.refreshUntil({ read: async () => { reads += 1; cancelled = true }, done: () => false, cancelled: () => cancelled, sleep: c.sleep, now: c.now })
  assert.equal(outcome, 'cancelled')
  assert.equal(reads, 1)
})

test('refreshSilently: one read when it works, quiet backoff when it fails, false (never a throw) after 30 s', async () => {
  const ok = clock()
  let reads = 0
  assert.equal(await retry.refreshSilently(async () => { reads += 1 }, { sleep: ok.sleep, now: ok.now }), true)
  assert.equal(reads, 1)
  assert.deepEqual(ok.slept, [])

  const flaky = clock()
  let attempts = 0
  assert.equal(await retry.refreshSilently(async () => { attempts += 1; if (attempts < 4) throw new Error('x') }, { sleep: flaky.sleep, now: flaky.now }), true)
  assert.deepEqual(flaky.slept, [300, 600, 1200])

  const dead = clock()
  assert.equal(await retry.refreshSilently(async () => { throw new Error('down') }, { sleep: dead.sleep, now: dead.now }), false)
  assert.ok(dead.elapsed() >= 30_000)

  const gone = clock()
  assert.equal(await retry.refreshSilently(async () => { throw new Error('x') }, { cancelled: () => true, sleep: gone.sleep, now: gone.now }), false)
  assert.deepEqual(gone.slept, [])
})

// ---------------------------------------------------------------- the screens: no manual "Refresh" dead-end any more
test('the stage panel waits for a just-created placement by itself: "Loading the stages…", silent retries, "Try again" only after they fail', async () => {
  const text = await read('features/workflow/components/stage-modal-body.tsx')
  assert.match(text, /import \{ refreshSilently, refreshUntil \} from '@\/shared\/state\/refresh-retry'/)
  assert.match(text, /void refreshUntil\(\{ read: \(\) => store\.refresh\(\{ keepCurrent: true \}\), done: \(\) => stagesLoadedRef\.current/)
  assert.match(text, /'Loading the stages…'/)
  assert.match(text, /Try again<\/Button>/)
  assert.doesNotMatch(text, /We could not find this order for the candidate any more\. Refresh and try again/)
  assert.doesNotMatch(text, /Your change is saved\. Refresh to see the latest/)
  assert.doesNotMatch(text, /Refresh to see the history/)
  assert.doesNotMatch(text, />Refresh<\/Button>\}<\/div>\s*return embedded/)
})

test('after a stage is saved the history is re-read silently, and the form is never locked behind a Refresh button', async () => {
  const text = await read('features/workflow/components/stage-modal-body.tsx')
  const after = text.slice(text.indexOf('const refreshAfterWrite'), text.indexOf('const save = async'))
  assert.match(after, /await refreshSilently\(\(\) => store\.refresh\(\{ keepCurrent: true \}\)/)
  assert.match(after, /if \(ok\) \{ setRefreshRequired\(false\)/)
  assert.match(text, /Updating the history…/)
})

test('saving the client and job no longer ends in "Refresh the stages to load the latest details"', async () => {
  const text = await read('screens/candidates/application-form.tsx')
  assert.doesNotMatch(text, /Refresh the stages to load the latest details/)
  assert.match(text, /void refreshSilently\(\(\) => store\.refresh\(\{ keepCurrent: true \}\)\)/)
  const navigate = text.indexOf('const stale = (applicationId')
  const refresh = text.indexOf('void refreshSilently')
  assert.ok(refresh > 0 && refresh < navigate, 'the background re-read starts first and never blocks the navigation to the new placement')
  assert.ok(!/await refreshSilently/.test(text), 'never awaited: the person is not kept waiting')
})

test('matching a candidate to an order no longer asks for a manual Refresh', async () => {
  const text = await read('features/candidates/components/map-dialog.tsx')
  assert.doesNotMatch(text, /Refresh to see it\. You do not need to match again/)
  assert.match(text, /if \(!result\.refreshed\) void refreshSilently\(\(\) => store\.refresh\(\{ keepCurrent: true \}\)\)/)
})
