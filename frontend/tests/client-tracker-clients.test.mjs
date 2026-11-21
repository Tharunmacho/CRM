import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import path from 'node:path'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
import { test } from 'node:test'
import { build } from 'esbuild'

/*
 * Client tracker, management reading: the by-client lines (status tabs, search, "3 weeks ago" wording, candidates by stage, the Excel address) and the
 * screen's layout rules (the shared cards and table look, centred figures, a card per row under 900px, theme tokens only).
 */
const root = fileURLToPath(new URL('../', import.meta.url))
async function compile(entry) {
  const bundle = await build({ entryPoints: [path.join(root, entry)], bundle: true, write: false, platform: 'node', format: 'cjs', logLevel: 'silent' })
  const compiled = { exports: {} }
  new Function('require', 'module', 'exports', bundle.outputFiles[0].text)(createRequire(import.meta.url), compiled, compiled.exports)
  return compiled.exports
}
const m = await compile('src/features/reports/client-summary-model.ts')
const text = (file) => readFile(new URL(file, import.meta.url), 'utf8')

const person = (name, stageNo, travelled = false) => ({ applicationId: 'a-' + name, candidateId: 'c-' + name, name, stageNo, stage: '', travelled })
const order = (over = {}) => ({ id: 'o', ref: 'ORD-01', position: 'Driver', country: 'Kuwait', status: 'open', isOpen: true, openings: 3, placed: 1, inProcess: 2, open: 2, deadline: null, candidates: [], ...over })
const client = (name, over = {}) => ({ id: name, name, country: 'UAE', orders: [order()], orderCount: 1, openOrders: 1, openings: 2, inProcess: 2, placed: 1, fillRate: 33.3, lastActivity: '2026-11-19T10:00:00Z', active: true, stalled: false, status: 'active', ...over })
const LIST = [
  client('Alpha Test'),
  client('Bravo Test', { stalled: true, status: 'stalled', orders: [order({ ref: 'ORD-77', position: 'Welder' })] }),
  client('Charlie Test', { active: false, status: 'idle', openings: 0, inProcess: 0, country: 'Qatar' }),
]

test('status tabs: Active is everything with something open (stalled included), Stalled is the quiet part, All is everyone', () => {
  assert.deepEqual(m.filterClients(LIST, 'all', '').map((c) => c.name), ['Alpha Test', 'Bravo Test', 'Charlie Test'])
  assert.deepEqual(m.filterClients(LIST, 'active', '').map((c) => c.name), ['Alpha Test', 'Bravo Test'])
  assert.deepEqual(m.filterClients(LIST, 'stalled', '').map((c) => c.name), ['Bravo Test'])
  assert.deepEqual(m.tabCounts(LIST), { all: 3, active: 2, stalled: 1 })
})

test('search finds a client by name or country, or by the reference or position of one of its orders', () => {
  assert.deepEqual(m.filterClients(LIST, 'all', ' alpha ').map((c) => c.name), ['Alpha Test'])
  assert.deepEqual(m.filterClients(LIST, 'all', 'qatar').map((c) => c.name), ['Charlie Test'])
  assert.deepEqual(m.filterClients(LIST, 'all', 'ORD-77').map((c) => c.name), ['Bravo Test'])
  assert.deepEqual(m.filterClients(LIST, 'all', 'welder').map((c) => c.name), ['Bravo Test'])
  assert.deepEqual(m.filterClients(LIST, 'stalled', 'alpha'), [])
})

test('wording: fill rate, last activity in the agency calendar, the dash for nothing', () => {
  assert.equal(m.rateText(33.333), '33.3%')
  assert.equal(m.rateText(100), '100%')
  assert.equal(m.rateText(0), '0%')
  assert.equal(m.rateText(null), '–')
  const today = '2026-11-20'
  assert.equal(m.activityText('2026-11-20', today), 'Today')
  assert.equal(m.activityText('2026-11-19', today), 'Yesterday')
  assert.equal(m.activityText('2026-11-15', today), '5 days ago')
  assert.equal(m.activityText('2026-11-06', today), '2 weeks ago')
  assert.equal(m.activityText('2026-09-01', today), '2 months ago')
  assert.equal(m.activityText(null, today), '–')
  assert.equal(m.daysBetween('2026-11-21', today), 0)
})

test('the candidates of an order by stage: stage 1 first, who travelled last, names in the order given', () => {
  const groups = m.stageGroups([person('Ann', 13, true), person('Ben', 5), person('Cam', 1), person('Dee', 5)])
  assert.deepEqual(groups.map((g) => [g.label, g.names]), [['Prescreening', ['Cam']], ['Offer', ['Ben', 'Dee']], ['Travelled', ['Ann']]])
  assert.deepEqual(m.stageGroups([]), [])
})

test('the Excel file is the clients on the screen: the status tab and the search travel with it', () => {
  assert.equal(m.clientsPath('all', ''), '/reports/client-tracker/clients')
  assert.equal(m.clientsPath('all', '', 'xlsx'), '/reports/client-tracker/clients?format=xlsx')
  assert.equal(m.clientsPath('stalled', '  bravo   test ', 'xlsx'), '/reports/client-tracker/clients?format=xlsx&status=stalled&q=bravo+test')
  assert.equal(m.clientsFileName('2026-11-20'), 'client-tracker-clients-2026-11-20.xlsx')
})

test('every field the screen reads is one the server sends', async () => {
  const server = await text('../../backend/app/services/client_tracker_clients.py')
  for (const key of ['activeClients', 'stalledClients', 'openOrders', 'openings', 'inProcess', 'placed', 'fillRate', 'stalledDays', 'orderCount', 'lastActivity', 'isOpen', 'candidates', 'stageNo', 'travelled', 'status']) {
    assert.ok(server.includes(`"${key}"`), key)
  }
  assert.match(server, /STALLED_DAYS = 14/)
})

test('the screen: four summary cards, status tabs, search, Export Excel, an order detail per client, and the placement sheet kept as the other view', async () => {
  const page = await text('../src/features/reports/client-tracker.tsx')
  assert.match(page, /SummaryCards/)
  for (const label of ['Openings left', 'In process', 'Placed', 'Fill rate']) assert.ok(page.includes(`label: '${label}'`), label)
  assert.equal((page.match(/\n {4}\{ label: '/g) ?? []).length, 4) // a manager's few numbers, none that the table below already shows
  assert.match(page, /By client/); assert.match(page, /Placement sheet/)
  assert.match(page, /\/reports\/client-tracker\/clients/)
  const panel = await text('../src/features/reports/client-by-client.tsx')
  assert.match(panel, /Export Excel/)
  assert.match(panel, /SearchInput/)
  assert.match(panel, /role="tablist"/)
  assert.match(panel, /aria-expanded=\{expanded\}/)            // a client opens and closes: a real button with its state
  assert.match(panel, /aria-controls=\{detailId\}/)
  assert.match(panel, /scope="col"/); assert.match(panel, /scope="row"/)
  assert.match(panel, /data-label="Fill rate"/)                // the card mode names every value
  assert.match(panel, /role="meter"/)
  const sheet = await text('../src/features/reports/client-placement-sheet.tsx')
  assert.match(sheet, /Export Excel/)
  assert.match(sheet, /scope="col"/)
})

test('layout: the shared table look (centred figures, names left, bright lines), a card per row under 900px, no hard-coded colours, no sideways scroll at laptop widths', async () => {
  const shared = await text('../src/styles/report-mgmt-tabs.css')
  const css = shared + (await text('../src/styles/client-tracker.css'))
  assert.match(shared, /--rt-line: color-mix\(in srgb, var\(--text-muted\) 42%, var\(--bg-card\)\)/)
  assert.match(shared, /\.rm-table :is\(th, td\) \{[^}]*text-align: center/)
  assert.match(shared, /\.rm-table \{[^}]*table-layout: fixed/)
  assert.match(shared, /@media \(max-width: 899px\)[\s\S]*\.rm-table\.is-cards tbody td::before \{ content: attr\(data-label\)/)
  assert.match(shared, /@media \(max-width: 1100px\) \{ \.rm-cards \.workspace-summary \{ grid-template-columns: repeat\(3/)
  assert.match(css, /\.cm-table \{ table-layout: fixed; \}/)
  for (const line of css.split('\n')) if (/\{.*#[0-9a-fA-F]{3,6}\b/.test(line)) assert.fail(`a hard-coded colour: ${line.trim().slice(0, 90)}`)
  // the by-client table: a name column that takes what is left plus seven fixed ones; at 1078px (a 1366 window) it leaves the name 330px
  const panel = await text('../src/features/reports/client-by-client.tsx')
  const widths = JSON.parse(/const widths = (\[[\d, ]+\])/.exec(panel)[1])
  assert.equal(widths.length, 7)
  assert.ok(1078 - widths.reduce((a, b) => a + b, 0) >= 280, 'the name column keeps room at a 1366px window')
  assert.ok(!/min-width:\s*1[0-9]{3}px/.test(await text('../src/styles/client-tracker.css').then((c) => c.slice(c.indexOf('.cm-tools')))), 'no wide minimum on the by-client table')
})
