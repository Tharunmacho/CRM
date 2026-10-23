import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'

const source = await readFile(new URL('../src/features/whatsapp/chat-types.ts', import.meta.url), 'utf8')
const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText
const chat = await import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`)

test('only supervisors may attach a candidate when opening a conversation', () => {
  assert.equal(chat.canLinkCandidate('admin'), true)
  assert.equal(chat.canLinkCandidate('manager'), true)
  assert.equal(chat.canLinkCandidate('staff'), false)
})

test('a staff request never carries a candidate id the server would reject with 403', () => {
  const input = { phoneNumberId: '123', peerNumber: '+91 90000 00000', candidateId: 'c-1' }
  assert.deepEqual(chat.openConversationBody({ ...input, role: 'staff' }), { phoneNumberId: '123', peerNumber: '+91 90000 00000' })
  assert.deepEqual(chat.openConversationBody({ ...input, role: 'manager' }), { ...input })
  assert.deepEqual(chat.openConversationBody({ ...input, candidateId: '', role: 'admin' }), { phoneNumberId: '123', peerNumber: '+91 90000 00000' })
})

test('the candidate profile deep link round-trips through the WhatsApp page', () => {
  assert.equal(chat.conversationHref('abc-123'), '/whatsapp?conversation=abc-123')
  assert.equal(chat.conversationFromSearch('?conversation=abc-123'), 'abc-123')
  assert.equal(chat.conversationFromSearch(new URL(`https://crm.test${chat.conversationHref('a1b2')}`).search), 'a1b2')
  assert.equal(chat.conversationFromSearch(''), '')
  assert.equal(chat.conversationFromSearch('?conversation=../../etc'), '')
  assert.equal(chat.conversationFromSearch('?conversation=' + 'x'.repeat(65)), '')
})
