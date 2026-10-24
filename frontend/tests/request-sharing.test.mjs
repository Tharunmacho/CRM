import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { afterEach, test } from 'node:test'
import ts from 'typescript'

async function loadSource(path) {
  const source = await readFile(new URL(path, import.meta.url), 'utf8')
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
  })
  return import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`)
}

const { api, takeEarlyRead } = await loadSource('../src/shared/api/client.ts')

const realFetch = globalThis.fetch
afterEach(() => {
  globalThis.fetch = realFetch
  delete globalThis.window
})

/** A fetch whose responses the test releases by hand; records every call. */
function manualFetch() {
  const calls = []
  globalThis.fetch = (url, options) => new Promise((resolve, reject) => {
    const call = { url, options, aborted: false, respond: (body, status = 200) => resolve(new Response(JSON.stringify(body), { status })) }
    options.signal.addEventListener('abort', () => { call.aborted = true; reject(new DOMException('Aborted', 'AbortError')) })
    calls.push(call)
  })
  return calls
}

test('overlapping identical reads share one request and each caller gets its own copy', async () => {
  const calls = manualFetch()
  const first = api.get('/notifications')
  const second = api.get('/notifications')
  assert.equal(calls.length, 1)
  calls[0].respond([{ id: 'n1' }])
  const [a, b] = await Promise.all([first, second])
  assert.deepEqual(a, [{ id: 'n1' }])
  assert.deepEqual(b, [{ id: 'n1' }])
  assert.notEqual(a, b)
})

test('different paths are different requests', async () => {
  const calls = manualFetch()
  const one = api.get('/notifications')
  const two = api.get('/inbox/revision')
  assert.equal(calls.length, 2)
  calls[0].respond([])
  calls[1].respond({})
  await Promise.all([one, two])
})

test('one caller leaving does not cancel a read another caller still wants', async () => {
  const calls = manualFetch()
  const leaving = new AbortController()
  const gone = api.get('/notifications', { signal: leaving.signal })
  const staying = api.get('/notifications')
  leaving.abort()
  await assert.rejects(gone, { name: 'AbortError' })
  assert.equal(calls[0].aborted, false)
  calls[0].respond([{ id: 'n2' }])
  assert.deepEqual(await staying, [{ id: 'n2' }])
})

test('the request is cancelled once every caller has left', async () => {
  const calls = manualFetch()
  const a = new AbortController()
  const b = new AbortController()
  const first = api.get('/notifications', { signal: a.signal })
  const second = api.get('/notifications', { signal: b.signal })
  a.abort()
  assert.equal(calls[0].aborted, false)
  b.abort()
  assert.equal(calls[0].aborted, true)
  await assert.rejects(first, { name: 'AbortError' })
  await assert.rejects(second, { name: 'AbortError' })
  // A read asked for afterwards starts again instead of joining the cancelled one.
  const again = api.get('/notifications')
  assert.equal(calls.length, 2)
  calls[1].respond([])
  await again
})

test('a write stops later reads from joining a read that began before it', async () => {
  const calls = manualFetch()
  const before = api.get('/notifications')
  const write = api.post('/notifications/read-all')
  const after = api.get('/notifications')
  assert.equal(calls.length, 3)
  calls[0].respond(['old'])
  calls[1].respond({ ok: true })
  calls[2].respond(['new'])
  assert.deepEqual(await before, ['old'])
  await write
  assert.deepEqual(await after, ['new'])
})

test('errors reach every caller of a shared read', async () => {
  const calls = manualFetch()
  const first = api.get('/inbox/attention')
  const second = api.get('/inbox/attention')
  calls[0].respond({ detail: 'No' }, 403)
  await assert.rejects(first, { status: 403, message: 'No' })
  await assert.rejects(second, { status: 403, message: 'No' })
})

test('a read the page started early is handed over once, for the same path only', async () => {
  const early = (path, response) => ({ path, startedAt: Date.now(), response: Promise.resolve(response) })
  globalThis.window = { __crmEarly: {
    session: early('/auth/session?workspace=true', Response.json({ me: null })),
    other: early('/dashboard', Response.json({ ok: 1 })),
    broken: early('/dashboard', new Response('nope', { status: 500 })),
  } }
  assert.deepEqual(await takeEarlyRead('session', '/auth/session?workspace=true'), { me: null })
  assert.equal(await takeEarlyRead('session', '/auth/session?workspace=true'), null)
  assert.equal(await takeEarlyRead('other', '/dashboard?staff_id=u3'), null)
  assert.equal(await takeEarlyRead('broken', '/dashboard'), null)
  assert.equal(await takeEarlyRead('missing', '/dashboard'), null)
})
