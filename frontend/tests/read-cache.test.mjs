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

const { api, invalidateReads, readCacheSize } = await loadSource('../src/shared/api/client.ts')

const realFetch = globalThis.fetch
/** A fetch that answers at once from a script; records every call. */
function scripted(answer) {
  const calls = []
  globalThis.fetch = async (url, options) => {
    calls.push({ url: String(url), method: options.method })
    const next = typeof answer === 'function' ? answer(calls.length, String(url), options) : answer
    return new Response(JSON.stringify(next.body), { status: next.status ?? 200 })
  }
  return calls
}
const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms))
const gets = (calls) => calls.filter((call) => call.method === 'GET').length

beforeEach(() => invalidateReads())
afterEach(() => { globalThis.fetch = realFetch })

const POLICY = { ttlMs: 60_000, staleMs: 120_000, tags: ['form_options'], errorTtlMs: 30_000 }

test('a cached read is answered without a request while it is fresh, and each caller gets its own copy', async () => {
  const calls = scripted({ body: { categories: [1, 2] } })
  const first = await api.get('/form-options', { cache: POLICY })
  const second = await api.get('/form-options', { cache: POLICY })
  assert.equal(calls.length, 1)
  assert.deepEqual(first, { categories: [1, 2] })
  assert.deepEqual(second, first)
  assert.notEqual(first, second)
  assert.deepEqual(readCacheSize(), { cached: 1, failed: 0, inFlight: 0 })
})

test('concurrent reads of one URL share a single request', async () => {
  const calls = scripted({ body: { ok: true } })
  await Promise.all([api.get('/inbox/health', { cache: { ttlMs: 5000 } }), api.get('/inbox/health', { cache: { ttlMs: 5000 } }), api.get('/inbox/health')])
  assert.equal(calls.length, 1)
})

test('a read without a cache policy is never stored', async () => {
  const calls = scripted({ body: { n: 1 } })
  await api.get('/notifications')
  await api.get('/notifications')
  assert.equal(calls.length, 2)
  assert.equal(readCacheSize().cached, 0)
})

test('a stale copy is returned at once and re-read in the background, telling the caller when it changed', async () => {
  const calls = scripted((n) => ({ body: { version: n } }))
  const seen = []
  const policy = { ttlMs: 80, staleMs: 5000, onRevalidated: (value) => seen.push(value) }
  assert.deepEqual(await api.get('/form-options', { cache: policy }), { version: 1 })
  await pause(120)
  assert.deepEqual(await api.get('/form-options', { cache: policy }), { version: 1 }) // stale, served instantly
  await pause(30)
  assert.equal(calls.length, 2) // and revalidated exactly once
  assert.deepEqual(seen, [{ version: 2 }])
  assert.deepEqual(await api.get('/form-options', { cache: policy }), { version: 2 }) // fresh again
  assert.equal(calls.length, 2)
})

test('past the stale window the read goes to the server and waits for it', async () => {
  const calls = scripted((n) => ({ body: { version: n } }))
  const policy = { ttlMs: 10, staleMs: 10 }
  await api.get('/form-options', { cache: policy })
  await pause(40)
  assert.deepEqual(await api.get('/form-options', { cache: policy }), { version: 2 })
  assert.equal(calls.length, 2)
})

test('fresh: true skips the copy but refreshes it', async () => {
  const calls = scripted((n) => ({ body: { version: n } }))
  await api.get('/form-options', { cache: POLICY })
  assert.deepEqual(await api.get('/form-options', { cache: POLICY, fresh: true }), { version: 2 })
  assert.deepEqual(await api.get('/form-options', { cache: POLICY }), { version: 2 })
  assert.equal(calls.length, 2)
})

test('a failed read is remembered for errorTtlMs so a missing route is not hammered', async () => {
  const calls = scripted({ status: 404, body: { detail: 'Not Found' } })
  await assert.rejects(api.get('/form-options', { cache: POLICY }), { status: 404 })
  await assert.rejects(api.get('/form-options', { cache: POLICY }), { status: 404, message: 'Not Found' })
  await assert.rejects(api.get('/form-options', { cache: POLICY }), { status: 404 })
  assert.equal(calls.length, 1)
  // Without errorTtlMs a failure is not remembered.
  await assert.rejects(api.get('/other', { cache: { ttlMs: 1000 } }), { status: 404 })
  await assert.rejects(api.get('/other', { cache: { ttlMs: 1000 } }), { status: 404 })
  assert.equal(calls.length, 3)
})

test('the remembered failure expires', async () => {
  const calls = scripted((n) => (n === 1 ? { status: 500, body: { detail: 'boom' } } : { body: { fine: true } }))
  const policy = { ttlMs: 1000, errorTtlMs: 20 }
  await assert.rejects(api.get('/form-options', { cache: policy }), { status: 500 })
  await pause(40)
  assert.deepEqual(await api.get('/form-options', { cache: policy }), { fine: true })
  assert.equal(calls.length, 2)
})

test('a write to the same area drops the copy; a write elsewhere does not', async () => {
  const calls = scripted((n, url, options) => ({ body: options.method === 'GET' ? { n } : { saved: true } }))
  await api.get('/form-options', { cache: POLICY })
  await api.post('/notifications/read-all') // unrelated
  await api.get('/form-options', { cache: POLICY })
  assert.equal(gets(calls), 1)
  await api.put('/form-options/abc', { label: 'x' }) // same first path word
  await api.get('/form-options', { cache: POLICY })
  assert.equal(gets(calls), 2)
})

test('a write to a declared tag drops copies that depend on it', async () => {
  const calls = scripted({ body: { n: 1 } })
  const policy = { ttlMs: 60_000, tags: ['documents'] }
  await api.get('/inbox/health', { cache: policy })
  await api.post('/documents/refresh')
  await api.get('/inbox/health', { cache: policy })
  assert.equal(gets(calls), 2)
})

test('invalidateReads(topics), as the live hub calls it, removes only matching copies', async () => {
  const calls = scripted({ body: { ok: 1 } })
  const health = { ttlMs: 60_000, tags: ['mail'] }
  await api.get('/form-options', { cache: POLICY })
  await api.get('/inbox/health', { cache: health })
  invalidateReads(['mail'])
  assert.equal(readCacheSize().cached, 1)
  await api.get('/form-options', { cache: POLICY }) // still cached
  await api.get('/inbox/health', { cache: health }) // read again
  assert.equal(calls.length, 3)
  invalidateReads(['*'])
  assert.deepEqual(readCacheSize(), { cached: 0, failed: 0, inFlight: 0 })
})

test('signing in or out clears everything', async () => {
  const calls = scripted({ body: { ok: 1 } })
  await api.get('/form-options', { cache: POLICY })
  await api.post('/auth/logout')
  assert.equal(readCacheSize().cached, 0)
  await api.get('/form-options', { cache: POLICY })
  assert.equal(gets(calls), 2)
})

test('a 401 on any read clears the cache', async () => {
  let unauthorised = false
  scripted(() => (unauthorised ? { status: 401, body: { detail: 'Sign in' } } : { body: { ok: 1 } }))
  await api.get('/form-options', { cache: POLICY })
  unauthorised = true
  await assert.rejects(api.get('/dashboard'), { status: 401 })
  assert.equal(readCacheSize().cached, 0)
})

test('a read that was in flight when its data was invalidated is not stored', async () => {
  let release
  globalThis.fetch = () => new Promise((resolve) => { release = () => resolve(new Response(JSON.stringify({ old: true }))) })
  const pending = api.get('/form-options', { cache: POLICY })
  invalidateReads(['form_options']) // the server announced a change while the read was running
  release()
  assert.deepEqual(await pending, { old: true })
  assert.equal(readCacheSize().cached, 0)
})
