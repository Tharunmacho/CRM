import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'

async function load(relative) {
  const source = await readFile(new URL(relative, import.meta.url), 'utf8')
  const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } })
  return import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`)
}

const { matchesSearch, searchTerms, highlightPieces, highlightPhone, isPhoneTerm } = await load('../src/features/clients/client-search.ts')

const gulf = {
  name: 'Khalid Rahman', agencyName: 'Gulf Build Contracting', email: 'khalid@gulfbuild.example', address: '12 Marina Road, Dubai', state: 'Kerala',
  phone: '+91 98765-43210', staffNames: ['Anita Rao', 'Zoë Mathew'], staffContacts: [{ name: 'Priya Nair', phone: '(044) 2345 6789' }],
}

test('an empty or blank query matches everything and has no terms', () => {
  assert.deepEqual(searchTerms(''), [])
  assert.deepEqual(searchTerms('  , ,  '), [])
  assert.equal(matchesSearch(gulf, ''), true)
  assert.equal(matchesSearch(gulf, '   '), true)
  assert.equal(matchesSearch(gulf, undefined), true)
})

test('matching ignores case and accents', () => {
  assert.equal(matchesSearch(gulf, 'GULF'), true)
  assert.equal(matchesSearch(gulf, 'kerala'), true)
  assert.equal(matchesSearch(gulf, 'zoe'), true)
  assert.equal(matchesSearch(gulf, 'ZOË'), true)
  assert.equal(matchesSearch({ name: 'Zoe' }, 'zoë'), true)
})

test('every term must match, separated by spaces or commas, with extra whitespace ignored', () => {
  assert.equal(matchesSearch(gulf, 'gulf kerala'), true)
  assert.equal(matchesSearch(gulf, '  gulf ,  kerala  '), true)
  assert.equal(matchesSearch(gulf, 'gulf,kerala'), true)
  assert.equal(matchesSearch(gulf, 'gulf punjab'), false)
  assert.equal(matchesSearch(gulf, 'khalid anita'), true)
})

test('each term is a substring of contact, agency, email, address, state, allocated staff or staff-details names', () => {
  for (const hit of ['rahm', 'contract', 'gulfbuild.example', 'marina', 'dubai', 'erala', 'anita', 'mathew', 'priya']) assert.equal(matchesSearch(gulf, hit), true, hit)
  assert.equal(matchesSearch(gulf, 'nobody'), false)
})

test('phone numbers match by digits only, whatever the spacing and punctuation', () => {
  assert.equal(matchesSearch(gulf, '98765'), true)
  assert.equal(matchesSearch(gulf, '9876543210'), true)
  assert.equal(matchesSearch(gulf, '+91 98765'), true)
  assert.equal(matchesSearch(gulf, '98-765-432'), true)
  assert.equal(matchesSearch(gulf, '044 2345'), true)
  assert.equal(matchesSearch(gulf, '55555'), false)
  assert.equal(isPhoneTerm('98-765'), true)
  assert.equal(isPhoneTerm('gulf2'), false)
  // A word with a digit is text only, so it never matches by phone digits.
  assert.equal(matchesSearch({ name: 'A', phone: '+91 12345' }, 'a12345'), false)
})

test('a record with no phone or staff does not break', () => {
  assert.equal(matchesSearch({ name: 'Only Name' }, 'only'), true)
  assert.equal(matchesSearch({ name: 'Only Name' }, '123'), false)
  assert.equal(matchesSearch({ name: null, agencyName: undefined }, 'x'), false)
})

test('highlight splits text into plain and matched pieces', () => {
  assert.deepEqual(highlightPieces('Gulf Build', 'build'), [{ text: 'Gulf ', hit: false }, { text: 'Build', hit: true }])
  assert.deepEqual(highlightPieces('Gulf Build', ''), [{ text: 'Gulf Build', hit: false }])
  assert.deepEqual(highlightPieces('abcabc', 'b c'), [{ text: 'a', hit: false }, { text: 'bc', hit: true }, { text: 'a', hit: false }, { text: 'bc', hit: true }])
  // Overlapping terms merge into one mark.
  assert.deepEqual(highlightPieces('abcd', 'abc bcd'), [{ text: 'abcd', hit: true }])
  // The original letters (case, accent) are kept in the output.
  assert.deepEqual(highlightPieces('Zoë Mathew', 'zoe'), [{ text: 'Zoë', hit: true }, { text: ' Mathew', hit: false }])
  assert.equal(highlightPieces('Gulf Build', 'xyz').some((piece) => piece.hit), false)
})

test('a phone highlight covers the matching digits and the punctuation between them', () => {
  assert.deepEqual(highlightPhone('+91 98765-43210', '98765'), [{ text: '+91 ', hit: false }, { text: '98765', hit: true }, { text: '-43210', hit: false }])
  assert.deepEqual(highlightPhone('+91 98765-43210', '55'), [{ text: '+91 98765-43210', hit: false }])
  assert.deepEqual(highlightPhone('+91 98765-43210', '76-54'), [{ text: '+91 98', hit: false }, { text: '765-4', hit: true }, { text: '3210', hit: false }])
  assert.deepEqual(highlightPhone('+91 98765-43210', 'gulf'), [{ text: '+91 98765-43210', hit: false }])
  assert.deepEqual(highlightPhone('', '123'), [{ text: '', hit: false }])
})
