import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'

const read = (path) => readFile(new URL(path, import.meta.url), 'utf8')
const css = await read('../src/styles/leads-table.css')
const screen = await read('../src/screens/leads/leads.tsx')
const cells = await read('../src/features/leads/components/lead-columns.tsx')
const definitions = await read('../src/features/leads/components/lead-column-definitions.tsx')
const table = await read('../src/features/candidates/components/db-table.tsx')
const model = await read('../src/features/leads/lead-model.ts')

/** The declarations of the first rule whose selector contains `selector`. */
function rule(selector) {
  const at = css.indexOf(selector)
  assert.ok(at >= 0, `rule ${selector} exists`)
  const open = css.indexOf('{', at)
  return css.slice(open + 1, css.indexOf('}', open))
}

const shared = await read('../src/styles/candidate-tables.css')

test('header: the Tasks blue on both palettes, 44px, one line, uppercase 11.5px / 600 / .04em, sticky, 8px edge', () => {
  const sharedHead = shared.slice(shared.indexOf(':root:root:root .db-tbl.is-grid thead th {'), shared.indexOf('}', shared.indexOf(':root:root:root .db-tbl.is-grid thead th {')))
  assert.match(sharedHead, /background: var\(--primary\)/)
  assert.match(sharedHead, /color: var\(--on-primary\)/)
  assert.match(sharedHead, /height: 44px/)
  assert.match(sharedHead, /font-size: 11\.5px/)
  assert.match(sharedHead, /font-weight: 600/)
  assert.match(sharedHead, /letter-spacing: \.04em/)
  assert.match(sharedHead, /text-transform: uppercase/)
  assert.match(sharedHead, /white-space: nowrap/)
  assert.match(sharedHead, /padding: 0 8px/, 'header and cells share the 8px left edge')
  const head = rule(':root:root .ld .db-tbl.is-grid.is-fit thead th {')
  assert.match(head, /position: sticky/)
  assert.match(head, /padding: 0 8px/)
  assert.match(head, /white-space: nowrap/)
  assert.doesNotMatch(css, /#[0-9a-f]{3,8}\b(?![^{]*\{)/i, 'no hard-coded colour outside a rule')
  assert.doesNotMatch(head + sharedHead, /#[0-9a-f]{3,8}\b|rgb\(/i, 'tokens only, so the dark palette follows')
})

test('sort carets and the select-all box stay visible on the blue', () => {
  assert.match(rule('thead th .db-th-caret.is-idle'), /opacity: \.72/)
  assert.match(rule('thead th .db-th-caret {'), /color: var\(--on-primary\)/)
  assert.match(css, /thead th \.custom-checkbox \{ border-color: var\(--on-primary\)/)
})

test('body: rows of at least 60px, 8px edge, a 1px line under every row (the last too), a light line between columns, everything centred', () => {
  const sharedBody = shared.slice(shared.indexOf(':root:root:root .db-tbl.is-grid tbody tr:not(.db-xrow) > td {'), shared.indexOf('}', shared.indexOf(':root:root:root .db-tbl.is-grid tbody tr:not(.db-xrow) > td {')))
  assert.match(sharedBody, /height: 60px/)
  assert.match(sharedBody, /padding: 8px;/, 'the same 8px left edge as the header')
  assert.match(sharedBody, /vertical-align: middle/)
  assert.match(sharedBody, /text-align: center/)
  assert.match(sharedBody, /border-bottom: var\(--table-line-width\) solid var\(--table-line\)/)
  assert.match(shared, /td:not\(:last-child\) \{ border-right: var\(--table-line-width\) solid var\(--table-column-line\); \}/)
  assert.match(shared, /tr:not\(\.db-xrow\):last-child > td \{ border-bottom: var\(--table-line-width\) solid var\(--table-line\)/)
  assert.match(rule(':root:root .ld .db-tbl.is-grid.is-fit tbody td.is-center {'), /text-align: center/, 'Created and the experience answers are centred under centred headings')
  assert.match(css, /\.ld-position, \.ld-owner-name, \.ld-identity-name, \.ld-camp-name, \.ld-answer-text, \.db-tone\) \{[^}]*white-space: normal; line-height: 18px/, 'names, jobs, places and statuses wrap at spaces')
  const widths = Object.fromEntries([...definitions.matchAll(/key: '(experienceIndia|experienceAbroad|experience)'[^\n]*?width: (\d+)/g)].map((m) => [m[1], +m[2]]))
  assert.ok(widths.experienceIndia <= widths.experienceAbroad, 'each experience column is as wide as its heading needs')
})

test('row controls are 36px and the phone line never wraps', () => {
  assert.match(rule('.db-selcell > label'), /width: 36px; height: 36px/)
  assert.match(rule(':is(.db-menu-trigger, .db-act)'), /width: 36px; min-width: 36px; height: 36px/)
  assert.match(rule('.ld-upload {'), /height: 36px/)
  assert.match(rule('.ld-identity-phone {'), /flex-wrap: nowrap/)
  assert.match(rule('.ld-match {'), /height: 24px/)
})

test('the experience headings are shortened on one line with the full name as the tooltip', () => {
  assert.match(definitions, /LEAD_SHORT_HEADINGS = \{ name: 'Name', experienceIndia: 'India exp\.', experienceAbroad: 'Abroad exp\.', experience: 'Total exp\.' \}/)
  assert.match(definitions, /headerTitle: h\.experienceIndia/)
  assert.match(definitions, /headerTitle: h\.experienceAbroad/)
  assert.match(definitions, /headerTitle: h\.experience,/)
  assert.match(table, /headerTitle\?: string/)
  assert.match(table, /<th key=\{column\.key\}[^\n]*title=\{column\.headerTitle\}/)
  assert.match(screen, /header: column\.headerTitle \?\? column\.header/, 'the Columns menu keeps the full names')
  assert.match(screen, /headerTitle: column\.headerTitle \?\? column\.header/, 'every heading is its own tooltip, so a squeezed one can be read whole')
})

test('captions that are not data are gone', () => {
  // both help lines above the table, and the sentence under it
  assert.doesNotMatch(screen, /searchHint|dbp-tip|ld-fitnote|footerHint|ld-hint/)
  assert.doesNotMatch(model, /Search any detail as you type/)
  assert.doesNotMatch(screen, /are hidden to fit the screen\. Open a row \(the arrow/)
  assert.match(screen, /placeholder="Search name, phone, job… \(use commas to combine\)"/, 'the placeholder is the only search hint')
  // the "+N hidden" pill is gone: opening a row (or the expand-all button) shows every field, so the count adds nothing
  assert.doesNotMatch(screen, /ld-hidden-pill|\+\{fit\.hidden\} hidden/)
  // the Follow-ups due card is gone from the Lead sheet's number cards
  assert.doesNotMatch(screen, /Follow-ups due/)
  // the experience question caption and the (i) icon are gone; the question stays in the tooltip and the dialog
  assert.doesNotMatch(cells, /experienceQuestionSummary|<Info /)
  assert.match(cells, /const title = `\$\{question \|\| label\}: \$\{value\}`/)
  // no "Age —" / "Phone —" / "· Imported" fillers
  assert.doesNotMatch(cells, /Age —|Phone —|· Imported/)
  // the unmoved real data
  assert.match(cells, /className="ld-identity-age"/)
  assert.match(cells, /\{lead\.countryInterest|const country = lead\.countryInterest/)
})

test('a cell never holds more than two data lines in the Name and Assigned-to columns', () => {
  const nameBody = cells.slice(cells.indexOf('export function NameCell'), cells.indexOf('/** Copy to clipboard'))
  assert.equal((nameBody.match(/className="ld-identity-top"|className="ld-identity-phone"/g) ?? []).length, 2, 'the name+age line and the phone line, nothing else')
  assert.doesNotMatch(nameBody, /<small/, 'age is no longer a line of its own')
  const owner = cells.slice(cells.indexOf('export function StatusOwnerCell'), cells.indexOf('/** Job title over'))
  assert.match(owner, /ld-statline/, 'the status and the follow-up share one line')
})

test('phones: cards keep 44px targets and the filters stack', () => {
  assert.match(css, /@media \(max-width: 767px\)[\s\S]*\.ld-card \.ld-identity-phone \{ flex-wrap: nowrap; height: auto; min-height: 44px/)
  assert.match(css, /\.ld \.db-panel-tools \.db-search \{ flex: 1 1 100%/)
})

test('the actions column has no visible heading and the cards drop empty rows', async () => {
  assert.match(css, /\[aria-label='Leads'\] thead th:first-child:not\(\.is-select\) \{ font-size: 0; \}/)
  const cards = await read('../src/features/leads/components/lead-cards.tsx')
  assert.match(cards, /\{lead\.countryInterest\?\.trim\(\) && /)
  assert.doesNotMatch(cards, /: '–'\}<\/dd>/, 'no dash-only Follow-up / Country rows')
})
