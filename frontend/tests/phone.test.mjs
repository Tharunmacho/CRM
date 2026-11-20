import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'

async function load(path) {
  const source = await readFile(new URL(path, import.meta.url), 'utf8')
  const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText
  return import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`)
}

const phone = await load('../src/shared/lib/phone.ts')

test('waDigits adds the home calling code to a national number', () => {
  assert.equal(phone.waDigits('9876543210', '91'), '919876543210')
  assert.equal(phone.waDigits('98765 43210', '91'), '919876543210')
  assert.equal(phone.waDigits('098765 43210', '91'), '919876543210')
  assert.equal(phone.waDigits('(098765)-43210', '91'), '919876543210')
  assert.equal(phone.waDigits('9876543210', '971'), '9719876543210')
})

test('waDigits keeps a number that already carries its country code', () => {
  assert.equal(phone.waDigits('+91 98765 43210', '91'), '919876543210')
  assert.equal(phone.waDigits('919876543210', '91'), '919876543210')
  assert.equal(phone.waDigits('0091-98765-43210', '91'), '919876543210')
  assert.equal(phone.waDigits('00971 50 123 4567', '91'), '971501234567')
  assert.equal(phone.waDigits('+971 50 123 4567', '91'), '971501234567')
  assert.equal(phone.waDigits('p:+919876543210', '91'), '919876543210')
})

test('waDigits uses 91 only when the settings give no code', () => {
  assert.equal(phone.waDigits('9876543210', ''), '919876543210')
  assert.equal(phone.waDigits('9876543210', undefined), '919876543210')
  assert.equal(phone.waDigits('9876543210', null), '919876543210')
  assert.equal(phone.waDigits('9876543210', '44'), '449876543210')
})

test('waDigits refuses what cannot be a phone number', () => {
  for (const bad of [null, undefined, '', '   ', '12345', '123456', '1'.repeat(16), '+1234', '0000000000', 'call me', '98765 43210 ext 4', '98765 43210 / 98765 00000']) {
    assert.equal(phone.waDigits(bad, '91'), null, String(bad))
  }
})

test('formatPhone groups a readable number and leaves the rest as typed', () => {
  assert.equal(phone.formatPhone('+919876543210'), '+91 98765 43210')
  assert.equal(phone.formatPhone('098765 43210'), '+91 98765 43210')
  assert.equal(phone.formatPhone('9876543210', '91'), '+91 98765 43210')
  assert.equal(phone.formatPhone('+971501234567', '91'), '+971 501 234 567')
  assert.equal(phone.formatPhone('p:+91 98765-43210'), '+91 98765 43210')
  assert.equal(phone.formatPhone('12345'), '12345')
  assert.equal(phone.formatPhone('  call after 5 '), 'call after 5')
  assert.equal(phone.formatPhone(null), '')
  assert.equal(phone.formatPhone(''), '')
})

test('samePhone treats every spelling of one number as the same', () => {
  assert.equal(phone.samePhone('+91 98765 43210', '098765 43210', '91'), true)
  assert.equal(phone.samePhone('9876543210', '919876543210', '91'), true)
  assert.equal(phone.samePhone('9876543210', '9876543211', '91'), false)
  assert.equal(phone.samePhone('+91 98765 43210', '', '91'), false)
  assert.equal(phone.samePhone('', '', '91'), false)
  assert.equal(phone.samePhone('12345', '12345', '91'), true)
})
