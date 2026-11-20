import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { afterEach, beforeEach, test } from 'node:test'
import ts from 'typescript'

async function loadSource(path) {
  const source = await readFile(new URL(path, import.meta.url), 'utf8')
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
  })
  return import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`)
}

const { api, invalidateReads, setFreshWindow, freshWindowSize } = await loadSource('../src/shared/api/client.ts')

const realFetch = globalThis.fetch
/** A fetch that answers at once from a script; records every call. */
function scripted(answer) {
  const calls = []
  globalThis.fetch = async (url, options) => {
    calls.push({ url: String(url), method: options.method, credentials: options.credentials })
    const next = typeof answer === 'function' ? answer(calls.length, String(url), options) : answer
    return new Response(JSON.stringify(next.body), { status: next.status ?? 200 })
  }
  return calls
}
const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms))
const gets = (calls) => calls.filter((call) => call.method === 'GET').length

beforeEach(() => { invalidateReads(); setFreshWindow(120) })
afterEach(() => { globalThis.fetch = realFetch; setFreshWindow(1_800) })

test('the same GET asked again right after it finished is answered from memory, each caller with its own copy', async () => {
  const calls = scripted({ body: { items: [1] } })
  const first = await api.get('/attendance?staffId=u1')
  const second = await api.get('/attendance?staffId=u1')
  assert.equal(calls.length, 1)
  assert.deepEqual(second, first)
  assert.notEqual(second, first)
  first.items.push(2)
  assert.deepEqual((await api.get('/attendance?staffId=u1')).items, [1])
})

test('sequential repeats from several callers (shell, screen, focus handler) cost one request', async () => {
  const calls = scripted({ body: { ok: true } })
  await Promise.all([api.get('/daily-work'), api.get('/daily-work')]) // in flight together: shared
  await api.get('/daily-work') // a moment later: the window
  await api.get('/daily-work')
  assert.equal(calls.length, 1)
})

test('the window ends, and the next read goes to the server', async () => {
  const calls = scripted({ body: { n: 1 } })
  await api.get('/follow-up')
  await pause(160)
  await api.get('/follow-up')
  assert.equal(calls.length, 2)
})

test('a different URL, or a different query, is a different read', async () => {
  const calls = scripted({ body: {} })
  await api.get('/daily-work')
  await api.get('/daily-work?staff=all')
  await api.get('/follow-up')
  assert.equal(calls.length, 3)
})

test('fresh: true skips the window but refreshes it', async () => {
  const calls = scripted((n) => ({ body: { n } }))
  await api.get('/travelled')
  assert.deepEqual(await api.get('/travelled', { fresh: true }), { n: 2 })
  assert.deepEqual(await api.get('/travelled'), { n: 2 })
  assert.equal(calls.length, 2)
})

test('any write empties the window, so a read after a save sees the save', async () => {
  const calls = scripted((n) => ({ body: { n } }))
  await api.get('/tasks')
  await api.post('/tasks/t1/complete', {})
  const after = await api.get('/tasks')
  assert.equal(after.n, 3)
  assert.equal(gets(calls), 2)
})

test('a write to another area empties it too (the window carries no tags)', async () => {
  const calls = scripted({ body: {} })
  await api.get('/jobs')
  await api.patch('/clients/c1', {})
  await api.get('/jobs')
  assert.equal(gets(calls), 2)
})

test('a presence heartbeat is not a change and does not empty the window', async () => {
  const calls = scripted({ body: {} })
  await api.get('/daily-work')
  await api.post('/presence/heartbeat', { visible: true })
  await api.get('/daily-work')
  assert.equal(gets(calls), 1)
})

test('a live change (invalidateReads with its topics) empties the window', async () => {
  const calls = scripted({ body: {} })
  await api.get('/daily-work')
  invalidateReads(['tasks'])
  await api.get('/daily-work')
  assert.equal(calls.length, 2)
})

test('signing in or out starts from nothing, so one user never sees another user\'s answer', async () => {
  const calls = scripted((n) => ({ body: { who: n === 1 ? 'alice' : 'bob' } }))
  assert.deepEqual(await api.get('/attendance?staffId=me'), { who: 'alice' })
  await api.post('/auth/logout')
  await api.post('/auth/login', { email: 'b@x.test', password: 'secret' })
  assert.deepEqual(await api.get('/attendance?staffId=me'), { who: 'bob' })
  assert.equal(gets(calls), 2)
})

test('a failed read is never kept: the next ask goes to the server again', async () => {
  const calls = scripted((n) => n === 1 ? { status: 500, body: { detail: 'boom' } } : { body: { fine: true } })
  await assert.rejects(api.get('/reports/daily-work'), /boom/)
  assert.deepEqual(await api.get('/reports/daily-work'), { fine: true })
  assert.equal(calls.length, 2)
  assert.equal(freshWindowSize(), 1)
})

test('a read that was in flight while something changed is not kept', async () => {
  let release
  const gate = new Promise((resolve) => { release = resolve })
  let count = 0
  globalThis.fetch = async () => {
    count += 1
    if (count === 1) await gate
    return new Response(JSON.stringify({ n: count }), { status: 200 })
  }
  const slow = api.get('/daily-work')
  invalidateReads(['tasks']) // a live change arrives while the read is travelling
  release()
  await slow
  assert.equal(freshWindowSize(), 0)
  assert.deepEqual(await api.get('/daily-work'), { n: 2 })
})

test('the workspace snapshot and the session read are never served from the window', async () => {
  const calls = scripted((n) => ({ body: { n } }))
  await api.get('/bootstrap?compact=true')
  await api.get('/bootstrap?compact=true')
  await api.get('/auth/session?workspace=true')
  await api.get('/auth/session?workspace=true')
  assert.equal(calls.length, 4)
})

test('requests keep sending the session cookie', async () => {
  const calls = scripted({ body: {} })
  await api.get('/leads/columns')
  assert.equal(calls[0].credentials, 'include')
})

test('a window of 0 turns the feature off', async () => {
  setFreshWindow(0)
  const calls = scripted({ body: {} })
  await api.get('/jobs')
  await api.get('/jobs')
  assert.equal(calls.length, 2)
})
