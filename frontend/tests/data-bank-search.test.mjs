import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'

async function load(path) {
  const source = await readFile(new URL(path, import.meta.url), 'utf8')
  const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText
  return import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`)
}

const search = await load('../src/features/candidates/data-bank-search.ts')

test('comma separated terms are trimmed, collapsed and de-duplicated like the server does', () => {
  assert.deepEqual(search.searchTerms(' Ravi ,, agent   kumar , RAVI,'), ['Ravi', 'agent kumar'])
  assert.deepEqual(search.searchTerms(''), [])
  assert.deepEqual(search.searchTerms(' , '), [])
  assert.equal(search.normalizeSearch('ravi, '), 'ravi')
  assert.equal(search.normalizeSearch('ravi,  agent  kumar'), 'ravi,agent kumar')
  // a trailing comma or space is not a new search
  assert.equal(search.normalizeSearch('ravi,'), search.normalizeSearch('ravi'))
})

test('an agent is the typed referrer, never the pointer automatic intake stores', () => {
  assert.equal(search.agentName({ sourceDetail: ' Suresh Agent ' }), 'Suresh Agent')
  assert.equal(search.agentName({ sourceDetail: 'email_message:abc-123' }), '')
  assert.equal(search.agentName({ sourceDetail: 'whatsapp_message:1' }), '')
  assert.equal(search.agentName({ sourceDetail: 'person_review:7' }), '')
  assert.equal(search.agentName({}), '')
  assert.equal(search.agentName({ sourceDetail: null }), '')
})

test('positions read as title, client and country; candidates without an order form "No job yet"', () => {
  const group = { orderId: 'o-1', ref: 'ORD-1', title: 'Welder', company: 'Al Noor', client: 'Gulf Hiring', country: 'UAE', city: null, orderStatus: 'open', resumes: 2 }
  assert.equal(search.groupTitle(group), 'Welder')
  assert.equal(search.groupMeta(group), 'Al Noor · Gulf Hiring · UAE')
  assert.equal(search.groupMeta({ ...group, client: 'al noor' }), 'Al Noor · UAE')
  assert.equal(search.groupKey(group), 'o-1')
  const none = { ...group, orderId: null, title: null, company: null, client: null, country: null }
  assert.equal(search.groupTitle(none), 'No job yet')
  assert.equal(search.groupKey(none), 'none')
  assert.equal(search.groupMeta(none), 'Not matched to an order')
  assert.equal(search.resumeCount(1), '1 resume')
  assert.equal(search.resumeCount(0), '0 resumes')
})

test('requests carry the same filters plus the position and the page', () => {
  assert.equal(search.positionGroupsPath('q=ravi'), '/candidates/position-groups?q=ravi')
  assert.equal(search.groupResumesPath('q=ravi', 'o-1', 2, 25), '/candidates?q=ravi&position_id=o-1&page=2&page_size=25')
  assert.equal(search.groupResumesPath('q=ravi', 'none', 0, 25), '/candidates?q=ravi&position_id=none&page=0&page_size=25')
})

test('highlighting marks every searched term in any case, including regex characters', () => {
  assert.deepEqual(search.highlightParts('Ravi Kumar', ['ravi']), [{ text: 'Ravi', hit: true }, { text: ' Kumar', hit: false }])
  assert.deepEqual(search.highlightParts('Suresh Agent', ['AGENT', 'sur']), [{ text: 'Sur', hit: true }, { text: 'esh ', hit: false }, { text: 'Agent', hit: true }])
  assert.deepEqual(search.highlightParts('A+B (x)', ['+', '(x)']).filter(part => part.hit).map(part => part.text), ['+', '(x)'])
  assert.deepEqual(search.highlightParts('Nobody', []), [{ text: 'Nobody', hit: false }])
  assert.deepEqual(search.highlightParts('', ['a']), [{ text: '', hit: false }])
})
