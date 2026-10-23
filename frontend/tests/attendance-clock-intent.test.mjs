import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'

const source = await readFile(new URL('../src/features/attendance/clock-intent.ts', import.meta.url), 'utf8')
const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } })
const { readClockIntent, writeClockIntent } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`)
const intent = { kind: 'check-out', requestKey: '09566131-cfa0-4e2f-83ad-8f32a8a96961', sessionId: 'original-shift' }
const storage = () => { const values = new Map(); return { getItem: (key) => values.get(key) ?? null, setItem: (key, value) => values.set(key, value), removeItem: (key) => values.delete(key) } }

test('a reload recovers the same clock request and session without dispatching an action', () => {
  const store = storage()
  writeClockIntent(store, 'employee', 'current-login', intent)
  assert.deepEqual(readClockIntent(store, 'employee', 'current-login'), intent)
  writeClockIntent(store, 'employee', 'current-login', null)
  assert.equal(readClockIntent(store, 'employee', 'current-login'), null)
})

test('recovery cannot cross employees or login sessions, even for the same employee', () => {
  const store = storage()
  writeClockIntent(store, 'employee', 'old-login', intent)
  assert.equal(readClockIntent(store, 'another-employee', 'old-login'), null)
  assert.equal(readClockIntent(store, 'employee', 'new-login'), null)
  assert.equal(readClockIntent(store, 'employee', 'old-login'), null)
})

test('corrupted and incomplete stored requests do not become clock actions', () => {
  const store = storage()
  store.setItem('crm-attendance-pending:employee', '{invalid')
  assert.equal(readClockIntent(store, 'employee', 'login'), null)
  writeClockIntent(store, 'employee', 'login', { ...intent, sessionId: undefined })
  assert.equal(readClockIntent(store, 'employee', 'login'), null)
  writeClockIntent(store, 'employee', 'login', { ...intent, requestKey: 'broken' })
  assert.equal(readClockIntent(store, 'employee', 'login'), null)
})

test('blocked session storage does not break the in-memory retry path', () => {
  const blocked = { getItem() { throw new Error('blocked') }, setItem() { throw new Error('blocked') }, removeItem() { throw new Error('blocked') } }
  assert.doesNotThrow(() => writeClockIntent(blocked, 'employee', 'login', intent))
  assert.equal(readClockIntent(blocked, 'employee', 'login'), null)
})
