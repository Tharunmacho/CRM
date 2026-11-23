import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const src = (path) => readFile(new URL(`../src/${path}`, import.meta.url), 'utf8')

test('the Numbers to ignore card is admin-only, sits in the WhatsApp Number settings tab and talks to the admin API', async () => {
  const screen = await src('screens/inbox/whatsapp.tsx')
  assert.match(screen, /import \{ IgnoredNumbers \} from '@\/features\/whatsapp\/ignored-numbers'/)
  assert.match(screen, /\{view === 'settings' && admin && <IgnoredNumbers \/>\}/)
  const card = await src('features/whatsapp/ignored-numbers.tsx')
  assert.match(card, /api\.get<[^>]*>\('\/whatsapp\/ignored-numbers'/)
  assert.match(card, /api\.post\('\/whatsapp\/ignored-numbers', \{ number: number\.trim\(\), label: label\.trim\(\) \}\)/)
  assert.match(card, /api\.del\(`\/whatsapp\/ignored-numbers\/\$\{encodeURIComponent\(removing\.id\)\}`\)/)
})

test('the card has loading, error, empty and list states, formats numbers with the agency calling code and asks before removing', async () => {
  const card = await src('features/whatsapp/ignored-numbers.tsx')
  assert.match(card, /Loading the list/)
  assert.match(card, /role="alert" className="wa-ignore-error"/)
  assert.match(card, /No numbers are ignored/)
  assert.match(card, /formatPhone\(item\.number, home\)/)
  assert.match(card, /homeDigits\(store\.settings\?\.homeCallingCode\)/)
  assert.match(card, /<Dialog open=\{!!removing\}/, 'removing a number is confirmed in a dialog')
  assert.match(card, /aria-label=\{`Stop ignoring \$\{shown\}`\}/, 'the remove button names the number for screen readers')
  assert.match(card, /if \(adding\) return/, 'a double press cannot add twice')
})

test('the card follows the site rules: tokens only, 44px controls and 16px fields on touch screens', async () => {
  const css = await src('features/whatsapp/ignored-numbers.css')
  assert.match(css, /@media \(max-width: 1023px\), \(pointer: coarse\) \{[^}]*min-height: 44px/)
  assert.match(css, /font-size: 16px/)
  assert.doesNotMatch(css, /#[0-9a-fA-F]{3,8}\b/, 'colours come from the theme tokens')
})
