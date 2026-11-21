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

test('the Team member cell is the name alone, from the left: no avatar, no e-mail line, no "You" chip; the e-mail and "You" are the tooltip and the screen-reader text', async () => {
  const page = await read('screens/users/users.tsx')
  const start = page.indexOf("key: 'name', header: 'Team member'")
  const cell = page.slice(start, page.indexOf("key: 'role'", start))
  assert.match(cell, /align: 'text'/)
  assert.match(cell, /className="uv-member" title=\{u\.email \+ \(u\.id === store\.me\.id \? ' \(you\)' : ''\)\}/)
  assert.match(cell, /className="sr-only"/)
  assert.doesNotMatch(cell, /UserAvatar|uv-email|<em>|uv-person/)
  assert.match(page, /columns\.map\(column => \(\{ align: 'center' as const, \.\.\.column \}\)\)/, 'the other columns stay centred')
  const css = await read('styles/users-v2.css')
  assert.match(css, /\.uv-flags \{[^}]*width: 156px;/, 'the switches block is one width in every row')
  assert.match(css, /\.uv-flags label > span \{[^}]*text-align: left;/)
})

test('User Management carries no explanatory captions under its headings or cards', async () => {
  const page = await read('screens/users/users.tsx')
  for (const gone of [/Automatic assignment is off/, /Recruiter preferences below/, /Add your team, choose what each person/, /Automatic assignment is on\. New candidates/, /With access to the CRM/, /On leave today/, /Can be given new candidates automatically/, /uv-assignment-note/, /uv-foot/]) assert.doesNotMatch(page, gone)
  const css = await read('styles/users-v2.css')
  assert.doesNotMatch(css, /uv-assignment-note|uv-foot|\.uv-email|\.uv-person/)
})
