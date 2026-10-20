import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { afterEach, test } from 'node:test'
import ts from 'typescript'

// Run the actual small browser-independent modules using the existing compiler.
// This avoids introducing a DOM/test-runner dependency just for request races.
async function loadSource(path) {
  const source = await readFile(new URL(path, import.meta.url), 'utf8')
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
  })
  return import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`)
}

const { createLatestRequest } = await loadSource('../src/shared/api/latest-request.ts')
const { api, ApiError } = await loadSource('../src/shared/api/client.ts')

function deferred() {
  let resolve
  let reject
  const promise = new Promise((yes, no) => {
    resolve = yes
    reject = no
  })
  return { promise, resolve, reject }
}

const realFetch = globalThis.fetch
const realSetTimeout = globalThis.setTimeout
const realClearTimeout = globalThis.clearTimeout
afterEach(() => {
  globalThis.fetch = realFetch
  globalThis.setTimeout = realSetTimeout
  globalThis.clearTimeout = realClearTimeout
})

test('replayed startup shares one read and applies its response once', async () => {
  const request = createLatestRequest()
  const response = deferred()
  let reads = 0
  const applied = []
  const read = () => {
    reads += 1
    return response.promise
  }
  const fail = () => assert.fail('The read should succeed')
  const first = request.run(read, (value) => applied.push(value), fail)
  const replay = request.run(read, (value) => applied.push(value), fail)
  assert.equal(first, replay)
  response.resolve({ me: null })
  await Promise.all([first, replay])
  assert.equal(reads, 1)
  assert.deepEqual(applied, [{ me: null }])
})

test('changing identity aborts the read and ignores a late successful response', async () => {
  const request = createLatestRequest()
  const oldResponse = deferred()
  const applied = []
  let oldSignal
  const first = request.run(
    (signal) => {
      oldSignal = signal
      return oldResponse.promise
    },
    (value) => applied.push(value),
    () => assert.fail('An obsolete read must not report an error'),
  )
  await Promise.resolve()
  request.invalidate()
  assert.equal(oldSignal.aborted, true)
  await request.run(async () => 'new session', (value) => applied.push(value), assert.fail)
  oldResponse.resolve('previous session')
  await first
  assert.deepEqual(applied, ['new session'])
})

test('a failure from an obsolete read cannot replace the new session with an error', async () => {
  const request = createLatestRequest()
  const oldResponse = deferred()
  const failures = []
  const first = request.run(() => oldResponse.promise, assert.fail, (error) => failures.push(error))
  await Promise.resolve()
  request.invalidate()
  oldResponse.reject(new Error('Old connection failed'))
  await first
  assert.deepEqual(failures, [])
})

test('a current failure is reported and a retry makes a fresh request', async () => {
  const request = createLatestRequest()
  const error = new Error('Unavailable')
  const failures = []
  await assert.rejects(
    request.run(async () => { throw error }, assert.fail, (value) => failures.push(value)),
    error,
  )
  let result
  await request.run(async () => 'recovered', (value) => { result = value }, assert.fail)
  assert.deepEqual(failures, [error])
  assert.equal(result, 'recovered')
})

test('completed reads are not cached across later refreshes', async () => {
  const request = createLatestRequest()
  const applied = []
  await request.run(async () => 1, (value) => applied.push(value), assert.fail)
  await request.run(async () => 2, (value) => applied.push(value), assert.fail)
  assert.deepEqual(applied, [1, 2])
})

test('overlapping writes both wait for the latest replacement snapshot', async () => {
  const request = createLatestRequest()
  const oldResponse = deferred()
  const newResponse = deferred()
  const applied = []
  let firstFinished = false
  const first = request.replace(
    () => oldResponse.promise,
    (value) => applied.push(value),
    assert.fail,
  ).then(() => { firstFinished = true })
  const second = request.replace(
    () => newResponse.promise,
    (value) => applied.push(value),
    assert.fail,
  )
  oldResponse.resolve('before second write')
  await new Promise((resolve) => setImmediate(resolve))
  assert.equal(firstFinished, false)
  assert.deepEqual(applied, [])
  newResponse.resolve('after both writes')
  await Promise.all([first, second])
  assert.equal(firstFinished, true)
  assert.deepEqual(applied, ['after both writes'])
})

test('overlapping refresh callers both see a failed replacement snapshot', async () => {
  const request = createLatestRequest()
  const oldResponse = deferred()
  const newResponse = deferred()
  const failures = []
  const first = request.replace(() => oldResponse.promise, assert.fail, assert.fail)
  const second = request.replace(
    () => newResponse.promise,
    assert.fail,
    (error) => failures.push(error),
  )
  const settled = Promise.allSettled([first, second])
  const error = new Error('Latest refresh failed')
  newResponse.reject(error)
  oldResponse.resolve('obsolete')
  const results = await settled
  assert.deepEqual(results.map((result) => result.status), ['rejected', 'rejected'])
  assert.equal(results[0].reason, error)
  assert.equal(results[1].reason, error)
  assert.deepEqual(failures, [error])
})

test('session reads include cookies and preserve the logged-out response', async () => {
  globalThis.fetch = async (url, options) => {
    assert.equal(url, '/api/auth/session')
    assert.equal(options.credentials, 'include')
    assert.equal(options.method, 'GET')
    assert.ok(options.signal instanceof AbortSignal)
    return Response.json({ me: null })
  }
  assert.deepEqual(await api.get('/auth/session'), { me: null })
})

test('structured login errors remain available to field-specific validation', async () => {
  const detail = { field: 'password', message: 'Password is incorrect.' }
  globalThis.fetch = async () => Response.json({ detail }, { status: 401 })
  await assert.rejects(api.post('/auth/login', { email: 'person@example.test', password: 'test' }), (error) => {
    assert.ok(error instanceof ApiError)
    assert.equal(error.status, 401)
    assert.deepEqual(error.detail, detail)
    assert.equal(error.message, detail.message)
    return true
  })
})

test('30-second deadline also covers a response body that never arrives', async () => {
  let expire
  let cleared = false
  globalThis.setTimeout = (callback, delay) => {
    assert.equal(delay, 30_000)
    expire = callback
    return 123
  }
  globalThis.clearTimeout = (handle) => {
    assert.equal(handle, 123)
    cleared = true
  }
  globalThis.fetch = async (_url, { signal }) => ({
    status: 200,
    ok: true,
    text: () => new Promise((_resolve, reject) => {
      signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')))
    }),
  })
  const response = api.get('/bootstrap')
  await Promise.resolve()
  expire()
  await assert.rejects(response, (error) => {
    assert.ok(error instanceof ApiError)
    assert.match(error.message, /too long to respond/)
    return true
  })
  assert.equal(cleared, true)
})

test('explicit cancellation aborts the fetch instead of becoming an unavailable-server error', async () => {
  const controller = new AbortController()
  globalThis.fetch = (_url, { signal }) => new Promise((_resolve, reject) => {
    signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')))
  })
  const response = api.get('/bootstrap', { signal: controller.signal })
  controller.abort()
  await assert.rejects(response, { name: 'AbortError' })
})

test('HTML returned by an incorrect API route becomes a visible error', async () => {
  globalThis.fetch = async () => new Response('<html>Application shell</html>')
  await assert.rejects(api.get('/bootstrap'), (error) => {
    assert.ok(error instanceof ApiError)
    assert.equal(error.status, 502)
    assert.match(error.message, /unexpected response/)
    return true
  })
})
