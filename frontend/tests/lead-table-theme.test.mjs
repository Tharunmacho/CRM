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

test('header: the Tasks blue on both palettes, 44px, one line, uppercase 11.5px / 700 / .05em, sticky', () => {
  const head = rule(':root:root .ld .db-tbl.is-grid.is-fit thead th {')
  assert.match(head, /background: var\(--primary\)/)
  assert.match(head, /color: var\(--on-primary\)/)
  assert.match(head, /height: 44px/)
  assert.match(head, /font-size: 11\.5px/)
  assert.match(head, /font-weight: 700/)
  assert.match(head, /letter-spacing: \.05em/)
  assert.match(head, /text-transform: uppercase/)
  assert.match(head, /white-space: nowrap/)
  assert.match(head, /position: sticky/)
  assert.match(head, /padding: 0 3px 0 14px/, 'header and cells share the 14px left edge')
  assert.doesNotMatch(css, /#[0-9a-f]{3,8}\b(?![^{]*\{)/i, 'no hard-coded colour outside a rule')
  assert.doesNotMatch(head, /#[0-9a-f]{3,8}\b|rgb\(/i, 'tokens only, so the dark palette follows')
})

test('sort carets and the select-all box stay visible on the blue', () => {
  assert.match(rule('thead th .db-th-caret.is-idle'), /opacity: \.72/)
  assert.match(rule('thead th .db-th-caret {'), /color: var\(--on-primary\)/)
  assert.match(css, /thead th \.custom-checkbox \{ border-color: var\(--on-primary\)/)
})

test('body: one 64px row, 14px edge, a 1px line under every row (the last too), left text, centred Created and experience', () => {
  const body = rule(':root:root .ld .db-tbl.is-grid.is-fit tbody td {')
  assert.match(body, /height: 64px/)
  assert.match(body, /padding: 8px 3px 8px 14px/, 'the same 14px left edge as the header')
  assert.match(body, /vertical-align: middle/)
  assert.match(body, /text-align: left/)
  assert.match(body, /border-bottom: var\(--table-line-width\) solid var\(--table-line\)/)
  assert.match(rule('tbody tr:last-child > td'), /border-bottom: var\(--table-line-width\) solid var\(--table-line\)/)
  const centred = [...definitions.matchAll(/\{ key: '([a-zA-Z]+)'[^\n]*?width: \d+(, align: '(left)')?/g)].filter((m) => !m[3]).map((m) => m[1])
  assert.deepEqual(centred.slice(0, 5), ['created', 'uploaded', 'experienceIndia', 'experienceAbroad', 'experience'], 'only dates and experience are centred')
  const widths = Object.fromEntries([...definitions.matchAll(/key: '(experienceIndia|experienceAbroad|experience)'[^\n]*?width: (\d+)/g)].map((m) => [m[1], m[2]]))
  assert.deepEqual(new Set(Object.values(widths)).size, 1, 'the three experience columns are the same width')
})

test('row controls are 36px and the phone line never wraps', () => {
  assert.match(rule('.db-selcell > label'), /width: 36px; height: 36px/)
  assert.match(rule(':is(.db-menu-trigger, .db-act)'), /width: 36px; min-width: 36px; height: 36px/)
  assert.match(rule('.ld-upload {'), /height: 36px/)
  assert.match(rule('.ld-identity-phone {'), /flex-wrap: nowrap/)
  assert.match(rule('.ld-match {'), /height: 24px/)
})

test('the experience headings are shortened on one line with the full name as the tooltip', () => {
  assert.match(definitions, /LEAD_SHORT_HEADINGS = \{ experienceIndia: 'India exp\.', experienceAbroad: 'Abroad exp\.', experience: 'Total exp\.' \}/)
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
  // the hidden columns are a small pill whose tooltip explains
  assert.match(screen, /className="ld-hidden-pill" title=\{`\$\{fit\.hidden\}/)
  assert.match(screen, /\+\{fit\.hidden\} hidden/)
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
  assert.match(css, /@media \(max-width: 767px\)[\s\S]*\.ld-card \.ld-identity-phone \{ height: 44px/)
  assert.match(css, /\.ld \.db-panel-tools \.db-search \{ flex: 1 1 100%/)
})

test('the actions column has no visible heading and the cards drop empty rows', async () => {
  assert.match(css, /thead th:first-child:not\(\.is-select\) \{ font-size: 0; \}/)
  const cards = await read('../src/features/leads/components/lead-cards.tsx')
  assert.match(cards, /\{lead\.countryInterest\?\.trim\(\) && /)
  assert.doesNotMatch(cards, /: '–'\}<\/dd>/, 'no dash-only Follow-up / Country rows')
})
