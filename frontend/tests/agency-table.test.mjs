import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { readFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import test from 'node:test'
import { build } from 'esbuild'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import ts from 'typescript'

const root = fileURLToPath(new URL('../', import.meta.url))
const read = (relative) => readFile(path.join(root, relative), 'utf8')

async function compile(entry) {
  const bundle = await build({
    entryPoints: [path.join(root, entry)], bundle: true, write: false, platform: 'node', format: 'cjs', jsx: 'automatic',
    alias: { '@': path.join(root, 'src') }, external: ['react', 'react-dom', 'react/jsx-runtime', 'lucide-react'], loader: { '.css': 'empty' }, logLevel: 'silent',
  })
  const compiled = { exports: {} }
  new Function('require', 'module', 'exports', bundle.outputFiles[0].text)(createRequire(import.meta.url), compiled, compiled.exports)
  return compiled.exports
}

const { ActiveToggle } = await compile('src/features/clients/components/active-toggle.tsx')
const render = (props) => renderToStaticMarkup(React.createElement(ActiveToggle, { agencyName: 'Gulf Link', ...props }))

test('the Active switch is a real button that reports its state and names its agency for screen readers', () => {
  const on = render({ active: true, onToggle: () => undefined })
  assert.match(on, /<button type="button" class="cs-switch is-on" aria-pressed="true"/)
  assert.match(on, /<span class="sr-only">Gulf Link: <\/span>Active/)
  const off = render({ active: false, onToggle: () => undefined })
  assert.match(off, /class="cs-switch" aria-pressed="false"/)
  assert.match(off, /Not active/)
})

test('a saving switch is disabled and busy; a refused save shows its reason as an alert under the pill', () => {
  const busy = render({ active: true, busy: true, onToggle: () => undefined })
  assert.match(busy, /disabled=""/)
  assert.match(busy, /aria-busy="true"/)
  const failed = render({ active: false, error: 'Not saved. Please try again.', onToggle: () => undefined })
  assert.match(failed, /<small class="cs-active-error" role="alert">Not saved\. Please try again\.<\/small>/)
})

test('without permission the switch is plain text in the same pill, not a button', () => {
  const plain = render({ active: true })
  assert.doesNotMatch(plain, /<button/)
  assert.match(plain, /cs-switch is-static is-on/)
})

test('agencyPayloadFor sends the saved agency unchanged except for the Active mark', async () => {
  const source = await read('src/features/clients/client-form.ts')
  const { outputText } = ts.transpileModule(source.replace("import type { StaffContact } from '../../shared/model/types'", ''), { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } })
  const form = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`)
  const agency = { name: 'Rahul', agencyName: 'Gulf Link', phone: '+91 1', email: 'a@b.co', address: 'Street', place: 'Chennai', state: 'TN', notes: 'n', staffContacts: [{ name: 'Ravi', designation: 'HR' }], specialisations: ['Medical'], preferredCountries: ['Gulf'], activeAgency: false }
  const on = form.agencyPayloadFor(agency, true)
  const off = form.agencyPayloadFor({ ...agency, activeAgency: true }, false)
  assert.equal(on.activeAgency, true)
  assert.equal(off.activeAgency, false)
  const { activeAgency: _a, ...rest } = on
  const { activeAgency: _b, ...restOff } = off
  assert.deepEqual(rest, restOff)
  assert.deepEqual(on.staffContacts, [{ name: 'Ravi', designation: 'HR' }])
  assert.deepEqual(on.specialisations, ['Medical'])
  assert.deepEqual(on.preferredCountries, ['Gulf'])
  assert.equal(on.place, 'Chennai')
  assert.equal(on.notes, 'n')
})

test('the agencies table has exactly Agency, Contact, Location, Specialisation, Country, Actions, Active; no Team, no avatar, no Active badge', async () => {
  const screen = await read('src/screens/clients/clients.tsx')
  const table = screen.slice(screen.indexOf('function AgencyTable('), screen.indexOf('function Skeleton('))
  const headers = [...table.matchAll(/header: '([^']+)'/g)].map((match) => match[1])
  assert.deepEqual(headers, ['Agency', 'Contact', 'Location', 'Specialisation', 'Country', 'Actions', 'Active'])
  assert.doesNotMatch(table, /Team|recruiter/)
  assert.match(table, /<Identity avatar=\{false\}/)
  assert.doesNotMatch(screen, /ActiveBadge|cs-active-badge/)
  // empty chip lists are a dash, never the words "Not added"
  const chips = screen.slice(screen.indexOf('function ChipList('), screen.indexOf('/** One card'))
  assert.match(chips, /—/)
  assert.doesNotMatch(chips, /Not added/)
})

test('the agency dialog no longer asks for Active agency, yet saving keeps the saved mark', async () => {
  const dialog = await read('src/features/clients/components/agency-dialog.tsx')
  const fields = await read('src/features/clients/components/party-fields.tsx')
  assert.doesNotMatch(fields, /ActiveAgencyField|Active agency|onActiveChange/)
  assert.doesNotMatch(dialog, /onActiveChange|setActive/)
  assert.match(dialog, /buildAgencyPayload\(values, rows, focus, active\)/)
  assert.match(dialog, /const active = agency\?\.activeAgency === true/)
})

test('a switch saves at once, optimistically, with rollback and an inline error, and the list re-sorts Active first', async () => {
  const screen = await read('src/screens/clients/clients.tsx')
  assert.match(screen, /agencyApi\.update\(agency\.id, agencyPayloadFor\(agency, next\)\)/)
  assert.match(screen, /setPending\(\(current\) => \(\{ \.\.\.current, \[agency\.id\]: next \}\)\)/)
  assert.match(screen, /catch \(err\) \{\s*setPending\(\(current\) => without\(current, agency\.id\)\)/)
  assert.match(screen, /activeFirst\(\(agencies\.items \?\? \[\]\)\.map/)
  assert.match(screen, /agencies\.refresh\(\)/)
})

test('the agencies header is the Tasks blue band, rows have a line under them (the last too), cells are centred and padded 14 px', async () => {
  const css = await read('src/styles/clients-simple.css')
  assert.match(css, /\.cs-agency-table \.ui-data-table\.is-grid thead th \{[^}]*height: 44px;[^}]*background: var\(--primary\);[^}]*color: var\(--on-primary\);[^}]*font-size: 11\.5px;[^}]*font-weight: 700;[^}]*text-transform: uppercase;[^}]*white-space: nowrap;/)
  assert.match(css, /tbody td \{ height: 56px; padding: 8px 14px; vertical-align: middle;[^}]*border-bottom: var\(--table-line-width\) solid var\(--table-line\);/)
  assert.match(css, /tbody tr:last-child > td \{ border-bottom: var\(--table-line-width\) solid var\(--table-line\); \}/)
  assert.match(css, /\.cs-switch \{[^}]*height: 32px;/)
  const tasks = await read('src/styles/daily-work.css')
  assert.match(tasks, /\.dw:not\(\.dwr\) \.dw-table thead th \{[^}]*background: var\(--primary\);[^}]*color: var\(--on-primary\);/)
})

test('the filter bar is one row of 40 px controls with the label above and Clear on the right', async () => {
  const css = await read('src/styles/clients-simple.css')
  assert.match(css, /\.cs-filterbar \{[^}]*align-items: flex-end;[^}]*gap: 12px;/)
  assert.match(css, /\.cs-filter select \{[^}]*height: 40px;/)
  assert.match(css, /\.cs-filter-clear \{[^}]*height: 40px;[^}]*margin-left: auto;/)
  assert.match(css, /\.cs-search input \{[^}]*height: 40px;/)
})
