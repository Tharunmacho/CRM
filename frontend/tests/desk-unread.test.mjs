import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'

const source = await readFile(new URL('../src/shared/state/desk-unread.ts', import.meta.url), 'utf8')
const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } })
const { deskUnread } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`)

test('the desk response seeds the unread badge', () => {
  assert.equal(deskUnread({ inboxUnread: { total: 7, accounts: { a1: 7 } } }), 7)
  assert.equal(deskUnread({ inboxUnread: { total: 0, accounts: {} } }), 0)
})

test('a server without the field, or with an unusable one, falls back to the first pulse', () => {
  assert.equal(deskUnread({}), null)
  assert.equal(deskUnread(null), null)
  assert.equal(deskUnread({ inboxUnread: null }), null)
  assert.equal(deskUnread({ inboxUnread: { total: '3' } }), null)
  assert.equal(deskUnread({ inboxUnread: { total: -1 } }), null)
  assert.equal(deskUnread({ inboxUnread: { total: NaN } }), null)
})
