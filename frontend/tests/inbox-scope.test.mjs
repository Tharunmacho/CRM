import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { afterEach, test } from 'node:test'
import ts from 'typescript'

const compile = source => ts.transpileModule(source, { compilerOptions: {
  target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext,
} }).outputText
const moduleFrom = source => import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`)
const { createLatestRequest } = await moduleFrom(compile(await readFile(new URL('../src/shared/api/latest-request.ts', import.meta.url), 'utf8')))
const hookSource = compile(await readFile(new URL('../src/features/inbox/use-inbox.ts', import.meta.url), 'utf8'))
  .replace(/^import .* from .*;\r?\n/gm, '')
const { useInbox } = await moduleFrom(`
const { useCallback, useEffect, useRef, useState } = globalThis.__inboxHooks;
const { api, createLatestRequest } = globalThis.__inboxDependencies;
${hookSource}`.replace('const { useCallback, useEffect, useRef, useState } = globalThis.__inboxHooks;', `
const useCallback = (...args) => globalThis.__inboxHooks.useCallback(...args);
const useEffect = (...args) => globalThis.__inboxHooks.useEffect(...args);
const useRef = (...args) => globalThis.__inboxHooks.useRef(...args);
const useState = (...args) => globalThis.__inboxHooks.useState(...args);`)
  .replace('const { api, createLatestRequest } = globalThis.__inboxDependencies;', `
const api = { get: (...args) => globalThis.__inboxDependencies.api.get(...args) };
const createLatestRequest = () => globalThis.__inboxDependencies.createLatestRequest();`))

function deferred() {
  let resolve, reject
  const promise = new Promise((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}
const settle = () => new Promise(resolve => setImmediate(resolve))
const previousWindow = globalThis.window
const previousDocument = globalThis.document
let dispose = () => {}
afterEach(() => {
  dispose()
  globalThis.window = previousWindow
  globalThis.document = previousDocument
  delete globalThis.__inboxHooks
  delete globalThis.__inboxDependencies
})

// A small lifecycle harness for this hook only: real state/ref/memo dependency
// slots, effect cleanup and queued renders. Request coalescing is production code.
function mountInbox() {
  const slots = []
  const requests = []
  let cursor = 0, effects = [], scheduled = false, alive = true, search = 'account_ids=first', result
  let signedIn = true
  const ownsSession = () => signedIn
  const different = (before, next) => !before || before.length !== next.length || next.some((item, i) => !Object.is(item, before[i]))
  const schedule = () => {
    if (scheduled || !alive) return
    scheduled = true
    queueMicrotask(() => { scheduled = false; if (alive) render() })
  }
  globalThis.__inboxHooks = {
    useState(initial) {
      const i = cursor++
      if (!slots[i]) slots[i] = { value: typeof initial === 'function' ? initial() : initial }
      return [slots[i].value, next => {
        const value = typeof next === 'function' ? next(slots[i].value) : next
        if (!Object.is(value, slots[i].value)) { slots[i].value = value; schedule() }
      }]
    },
    useRef(value) { const i = cursor++; if (!slots[i]) slots[i] = { current: value }; return slots[i] },
    useCallback(callback, deps) {
      const i = cursor++
      if (!slots[i] || different(slots[i].deps, deps)) slots[i] = { value: callback, deps }
      return slots[i].value
    },
    useEffect(setup, deps) {
      const i = cursor++
      if (!slots[i] || different(slots[i].deps, deps)) {
        const previous = slots[i]
        slots[i] = { deps, cleanup: previous?.cleanup }
        effects.push(() => { slots[i].cleanup?.(); slots[i].cleanup = setup() })
      }
    },
  }
  globalThis.__inboxDependencies = { createLatestRequest, api: { async get(path) {
    requests.push(path)
    return { accounts: [], messages: [{ id: path }], jobs: [] }
  } } }
  globalThis.window = { setInterval: () => 1, clearInterval: () => {} }
  globalThis.document = { visibilityState: 'visible', addEventListener() {}, removeEventListener() {} }
  function render() {
    cursor = 0; effects = []
    result = useInbox('admin', ownsSession, search)
    for (const effect of effects) effect()
  }
  dispose = () => { alive = false; for (const slot of slots) slot?.cleanup?.() }
  render()
  return { get current() { return result }, requests,
    scope(value) { search = value; render() },
    signOut() { signedIn = false; dispose() },
  }
}

test('a delayed mailbox write refreshes the currently selected scope, never the submission scope', async () => {
  const hook = mountInbox()
  await settle()
  assert.equal(hook.current.data.messages[0].id, '/inbox?account_ids=first')
  const write = deferred()
  const action = hook.current.act('star:first-message', () => write.promise, 'Star saved')
  await settle()
  hook.scope('account_ids=second&unread=true')
  await settle()
  assert.equal(hook.current.data, null)
  assert.equal(hook.requests.length, 1, 'The new read waits while the mutation is pending')
  write.resolve()
  assert.equal(await action, true)
  await settle()
  assert.deepEqual(hook.requests, ['/inbox?account_ids=first', '/inbox?account_ids=second&unread=true'])
  assert.equal(hook.current.data.messages[0].id, '/inbox?account_ids=second&unread=true')
  assert.equal(hook.current.busy, null)
  assert.equal(hook.current.error, '')
})

test('a late write from a signed-out inbox cannot refresh any mailbox', async () => {
  const hook = mountInbox()
  await settle()
  const write = deferred()
  const action = hook.current.act('star:first-message', () => write.promise, 'Star saved')
  await settle()
  hook.signOut()
  write.resolve()
  assert.equal(await action, false)
  await settle()
  assert.deepEqual(hook.requests, ['/inbox?account_ids=first'])
})
