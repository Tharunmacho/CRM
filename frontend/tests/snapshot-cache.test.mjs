import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { beforeEach, test } from 'node:test'
import ts from 'typescript'

// The shipped default is OFF; the behavioural tests below switch the cache on to prove its safety rules still hold.
async function load(enabled = true) {
  let source = await readFile(new URL('../src/shared/state/snapshot-cache.ts', import.meta.url), 'utf8')
  assert.match(source, /export const SNAPSHOT_CACHE_ENABLED = false/, 'the cache must ship disabled')
  if (enabled) source = source.replace('SNAPSHOT_CACHE_ENABLED = false', 'SNAPSHOT_CACHE_ENABLED = true')
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
  })
  return import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}#${Math.random()}`)
}

// Object.keys(storage) must list the stored keys, as it does for the real sessionStorage.
function storage() {
  const items = new Map()
  const target = {
    items,
    getItem: (key) => (items.has(key) ? items.get(key) : null),
    setItem: (key, value) => { items.set(key, String(value)) },
    removeItem: (key) => { items.delete(key) },
  }
  return new Proxy(target, {
    ownKeys: () => [...items.keys()],
    getOwnPropertyDescriptor: (_t, key) => (items.has(key) ? { enumerable: true, configurable: true, value: items.get(key) } : undefined),
  })
}

let cache
beforeEach(async () => {
  globalThis.window = { sessionStorage: storage() }
  globalThis.document = { cookie: '' }
  cache = await load()
})

test('a saved workspace is restored only for the user the server named in the cookie', () => {
  cache.writePersisted('bootstrap', 'u1', { me: { id: 'u1' } })
  assert.equal(cache.readPersisted('bootstrap', 'u1'), null, 'no cookie, no restore')
  document.cookie = 'theme=dark; crm_uid=u2'
  assert.equal(cache.readPersisted('bootstrap', 'u1'), null, 'another person is signed in')
  document.cookie = 'theme=dark; crm_uid=u1'
  assert.deepEqual(cache.readPersisted('bootstrap', 'u1').value, { me: { id: 'u1' } })
  assert.equal(cache.readPersisted('bootstrap', null), null)
})

test('an entry written for one user is never returned for another', () => {
  document.cookie = 'crm_uid=u2'
  cache.writePersisted('bootstrap', 'u1', { me: { id: 'u1' } })
  assert.equal(cache.readPersisted('bootstrap', 'u2'), null)
})

test('forgetting removes every remembered snapshot', () => {
  document.cookie = 'crm_uid=u1'
  cache.writePersisted('bootstrap', 'u1', { a: 1 })
  cache.rememberReport('u1', '/dashboard', '', { b: 2 })
  window.sessionStorage.setItem('unrelated', 'keep')
  cache.forgetSnapshots()
  assert.equal(cache.readPersisted('bootstrap', 'u1'), null)
  assert.equal(cache.peekReport('u1', '/dashboard', ''), null)
  assert.equal(window.sessionStorage.getItem('unrelated'), 'keep')
})

test('oversized, unreadable or full storage means no cache rather than an error', () => {
  document.cookie = 'crm_uid=u1'
  cache.writePersisted('bootstrap', 'u1', { text: 'x'.repeat(4_000_000) })
  assert.equal(cache.readPersisted('bootstrap', 'u1'), null)
  window.sessionStorage.setItem('crm-swr:v1:bootstrap', '{not json')
  assert.equal(cache.readPersisted('bootstrap', 'u1'), null)
  window.sessionStorage.setItem = () => { throw new DOMException('full', 'QuotaExceededError') }
  assert.doesNotThrow(() => cache.writePersisted('bootstrap', 'u1', { a: 1 }))
})

test('reports are remembered per user and query; only the dashboard survives a reload', () => {
  document.cookie = 'crm_uid=u1'
  cache.rememberReport('u1', '/dashboard', '', { n: 1 })
  cache.rememberReport('u1', '/reports?from=2026-01-01', '', { n: 2 })
  assert.deepEqual(cache.peekReport('u1', '/dashboard', ''), { n: 1 })
  assert.equal(cache.peekReport('u2', '/dashboard', ''), null)
  assert.equal(cache.peekReport('u1', '/dashboard?staff_id=u3', ''), null)
  const survivors = [...window.sessionStorage.items.keys()]
  assert.deepEqual(survivors, ['crm-swr:v1:report:/dashboard'])
})

test('disabled by default: nothing is stored, restored or remembered', async () => {
  const off = await load(false)
  assert.equal(off.SNAPSHOT_CACHE_ENABLED, false)
  document.cookie = 'crm_uid=u1'
  off.writePersisted('bootstrap', 'u1', { a: 1 })
  off.rememberReport('u1', '/dashboard', '', { n: 1 })
  off.persistSoon('bootstrap', 'u1', () => ({ a: 1 }))
  assert.equal(window.sessionStorage.items.size, 0)
  assert.equal(off.readPersisted('bootstrap', 'u1'), null)
  assert.equal(off.peekReport('u1', '/dashboard', ''), null)
})

test('starting the app purges snapshots an earlier version stored, and only those', async () => {
  window.sessionStorage.setItem('crm-swr:v1:bootstrap', '{"old":true}')
  window.sessionStorage.setItem('crm-swr:v1:report:/dashboard', '{"old":true}')
  window.sessionStorage.setItem('crm-mail-draft:u1', 'unsent text')
  window.sessionStorage.setItem('crm:whatsapp:send:u1:c', '{"intent":1}')
  window.localStorage = storage()
  window.localStorage.setItem('crm-swr:v1:bootstrap', '{"old":true}')
  window.localStorage.setItem('theme', 'dark')
  await load(false)
  assert.deepEqual([...window.sessionStorage.items.keys()].sort(), ['crm-mail-draft:u1', 'crm:whatsapp:send:u1:c'])
  assert.deepEqual([...window.localStorage.items.keys()], ['theme'])
})
