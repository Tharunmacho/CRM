import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'

async function load(relative) {
  const source = await readFile(new URL(relative, import.meta.url), 'utf8')
  const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } })
  return import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`)
}

const m = await load('../src/features/reports/client-tracker-model.ts')
const row = (over = {}) => ({
  sNo: 1, applicationId: 'a1', candidateId: 'c1', orderId: 'o1', orderRef: 'ORD-1', client: 'Tawseel', country: 'Kuwait', name: 'Benny', position: 'Driver',
  referral: 'Sigma', advance: '10k collected/50k', stage: 'Waiting for stamp', remarks: '', stageNo: 11, travelled: false, travelDate: null, ...over,
})

test('the columns are the clients sheet, in order', () => {
  assert.deepEqual(m.TRACKER_COLUMNS.map(c => c.label), ['S.No', 'Client', 'Country', 'Name', 'Position', 'Ref', 'Advance', 'Stage', 'Remarks'])
  assert.deepEqual(m.TRACKER_COLUMNS.map(c => c.key), ['sNo', 'client', 'country', 'name', 'position', 'referral', 'advance', 'stage', 'remarks'])
  assert.deepEqual(m.TRACKER_COLUMNS.filter(c => c.numeric).map(c => c.key), ['sNo'])
})

test('every column is a field the server sends for a row, and the server writes the same header', async () => {
  const sample = row()
  for (const column of m.TRACKER_COLUMNS) assert.ok(column.key in sample, column.key)
  const service = await readFile(new URL('../../backend/app/services/client_tracker.py', import.meta.url), 'utf8')
  const header = service.match(/^COLUMNS = \(([^)]*)\)/m)[1].split(',').map(part => part.trim().replace(/^"|"$/g, ''))
  assert.deepEqual(header, m.TRACKER_COLUMNS.map(c => c.label))
})

test('a missing value is an empty cell, a number is its digits', () => {
  assert.equal(m.cellText(row({ advance: '' }), 'advance'), '')
  assert.equal(m.cellText(row({ remarks: null }), 'remarks'), '')
  assert.equal(m.cellText(row({ sNo: 12 }), 'sNo'), '12')
  assert.equal(m.cellText(row(), 'advance'), '10k collected/50k')
})

test('the query sends only what narrows the sheet', () => {
  assert.equal(m.trackerQuery(m.EMPTY_TRACKER_FILTERS), '')
  assert.equal(m.trackerQuery({ client: ' Tawseel  Test ', country: 'Kuwait', agency: 'Sigma', stage: '10', includeTravelled: false }),
    'client=Tawseel+Test&country=Kuwait&agency=Sigma&stage=10&include_travelled=false')
  assert.equal(m.trackerQuery({ ...m.EMPTY_TRACKER_FILTERS, stage: 'x' }), '')
  assert.equal(m.trackerQuery({ ...m.EMPTY_TRACKER_FILTERS, stage: '07' }), 'stage=7')
  assert.equal(m.trackerQuery({ ...m.EMPTY_TRACKER_FILTERS, country: 'UAE' }, 'xlsx'), 'country=UAE&format=xlsx')
  assert.equal(m.trackerPath(m.EMPTY_TRACKER_FILTERS), '/reports/client-tracker')
  assert.equal(m.trackerPath({ ...m.EMPTY_TRACKER_FILTERS, client: 'A' }, 'csv'), '/reports/client-tracker?client=A&format=csv')
})

test('how many filters are in use, and the picker keeps a chosen value', () => {
  assert.equal(m.activeTrackerFilters(m.EMPTY_TRACKER_FILTERS), 0)
  assert.equal(m.activeTrackerFilters({ client: 'A', country: '', agency: 'B', stage: '', includeTravelled: false }), 3)
  assert.deepEqual(m.withChosen(['A', 'B'], 'C'), ['C', 'A', 'B'])
  assert.deepEqual(m.withChosen(['A', 'B'], 'B'), ['A', 'B'])
  assert.deepEqual(m.withChosen(['A'], ''), ['A'])
})

test('the file name carries the day, and the summary counts in progress and travelled', () => {
  assert.equal(m.trackerFileName('2026-11-19', 'xlsx'), 'client-tracker-2026-11-19.xlsx')
  assert.equal(m.trackerSummary({ total: 0, rows: [] }), 'No placements')
  assert.equal(m.trackerSummary({ total: 1, rows: [row()] }), '1 placement: 1 in progress, 0 travelled')
  assert.equal(m.trackerSummary({ total: 3, rows: [row(), row({ travelled: true }), row({ travelled: true })] }), '3 placements: 1 in progress, 2 travelled')
})

test('the Reports screen has a Client tracker entry with an Export Excel button and the client, country and referral filters', async () => {
  const screen = await readFile(new URL('../src/screens/reports/reports.tsx', import.meta.url), 'utf8')
  assert.match(screen, /ClientTrackerReport/)
  const view = await readFile(new URL('../src/features/reports/client-placement-sheet.tsx', import.meta.url), 'utf8')
  assert.match(view, /Export Excel/)
  for (const label of ['"Client"', '"Country"', '"Referral agency"']) assert.ok(view.includes(`aria-label=${label}`), label)
  const api = await readFile(new URL('../src/features/reports/client-tracker-api.ts', import.meta.url), 'utf8')
  assert.match(api, /downloadClientTracker/)
})

test('the sheet is the Tasks look: names wrap at spaces, only Remarks stops at three lines with a title; the address follows the stage filter', async () => {
  const css = await readFile(new URL('../src/styles/client-tracker.css', import.meta.url), 'utf8')
  assert.match(css, /\.ct-cell \{ display: block; overflow-wrap: break-word; \}/) // no mid-word breaks, no ellipsis for names, clients, jobs
  assert.ok(!/overflow-wrap: anywhere/.test(css))
  assert.match(css, /td\.is-remarks \.ct-cell \{[^}]*-webkit-line-clamp: 3;/)
  // Table rules: 44 px blue header kept in view, 56 px rows centred, 10px side padding on header and cells, S.No 8px and centred; a table from 1080px, cards below: never a sideways scroll.
  assert.match(css, /@container ct \(min-width: 960px\)/) // auto layout: the sheet is a table from the width its content needs (960px), cards below
  assert.match(css, /@container ct \(max-width: 959px\)/)
  assert.match(css, /thead th \{[^}]*position: sticky;[^}]*height: 44px;[^}]*padding: 4px 8px;[^}]*background: var\(--primary\);[^}]*font-size: 11\.5px;[^}]*text-transform: uppercase;[^}]*white-space: normal;/)
  assert.match(css, /tbody td \{[^}]*height: 56px;[^}]*padding: 8px;[^}]*vertical-align: middle;/)
  assert.match(css, /overflow: auto; border: var\(--table-frame-width\) solid var\(--table-line-strong\); border-radius: var\(--table-frame-radius, 6px\)/)
  assert.ok(!/min-width: 1060px/.test(css))
  const view = await readFile(new URL('../src/features/reports/client-placement-sheet.tsx', import.meta.url), 'utf8')
  assert.match(view, /next\.delete\('stage'\)/)   // Reset / another stage must not leave a stale ?stage= in the address
})
