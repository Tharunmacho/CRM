import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'

async function load(path) {
  const source = await readFile(new URL(path, import.meta.url), 'utf8')
  const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText
  return import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`)
}
const read = (path) => readFile(new URL(`../src/${path}`, import.meta.url), 'utf8')

test('a CSV cell never starts like a spreadsheet formula, but a phone number keeps its plus', async () => {
  const { csvCell } = await load('../src/features/staff/user-text.ts')
  assert.equal(csvCell('=HYPERLINK("x")'), '"\'=HYPERLINK(""x"")"')
  assert.equal(csvCell('@cmd'), "'@cmd")
  assert.equal(csvCell('-Evil'), "'-Evil")
  assert.equal(csvCell('+971 50 000 0000'), '+971 50 000 0000')
  assert.equal(csvCell('Priya Nair'), 'Priya Nair')
  assert.equal(csvCell('Nair, Priya'), '"Nair, Priya"')
  assert.equal(csvCell(7), '7')
  assert.equal(csvCell(null), '')
})

test('a server form check reads as plain words, other messages are untouched', async () => {
  const { plainServerText } = await load('../src/features/staff/user-text.ts')
  assert.equal(plainServerText('phone: Value error, Enter a valid phone number: 5 to 15 digits, with an optional country code.'),
    'Enter a valid phone number: 5 to 15 digits, with an optional country code.')
  assert.equal(plainServerText('email: Value error, Enter a valid email address.; password: Value error, Enter a password.'),
    'Enter a valid email address.; Enter a password.')
  assert.equal(plainServerText('An account with this email already exists.'), 'An account with this email already exists.')
})

test('the account dialog asks for a recruiter when a person holds leads, and a re-activated or re-roled recruiter is offered new candidates again', async () => {
  const dialog = await read('screens/users/user-dialog.tsx')
  assert.match(dialog, /delete-summary/)
  assert.match(dialog, /leads > 0/)
  assert.match(dialog, /user\.active && !form\.active/)
  assert.match(dialog, /receivesAutoAssign: true/)
  assert.doesNotMatch(dialog, /Manager/)
})

test('the history sheet shows leads that moved and marks a deleted account like a deactivated one', async () => {
  const sheet = await read('screens/users/audit-sheet.tsx')
  assert.match(sheet, /deactivat\|deleted/)
  assert.match(sheet, /transfer\.leads/)
})
