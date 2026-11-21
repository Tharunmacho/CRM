import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'

const read = (relative) => readFile(new URL(`../src/${relative}`, import.meta.url), 'utf8')

async function load(relative) {
  const source = await readFile(new URL(relative, import.meta.url), 'utf8')
  const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } })
  return import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`)
}

const model = await load('../src/features/travelled/travelled-model.ts')
const { EMPTY_FILTERS, choosePeriod, filterCount, filtersFromParams, moreFilterCount, pageRows, pageWindow, periodValue, queryFor, startingPageSize, PAGE_SIZE_CHOICES } = model
const parse = (query) => filtersFromParams(new URLSearchParams(query))
const TODAY = '2026-11-20'

test('the Period select says what the date filters mean', () => {
  assert.equal(periodValue(EMPTY_FILTERS, TODAY), 'all')
  assert.equal(periodValue(parse('year=2026&month=11'), TODAY), 'this-month')
  assert.equal(periodValue(parse('year=2026&month=10'), TODAY), 'last-month')
  assert.equal(periodValue(parse('year=2026'), TODAY), 'this-year')
  assert.equal(periodValue(parse('from=2026-09-01&to=2026-11-30'), TODAY), 'last-3-months')
  assert.equal(periodValue(parse('undated=true'), TODAY), 'undated')
  assert.equal(periodValue(parse('year=2025&month=03'), TODAY), 'custom')
  assert.equal(periodValue(parse('year=2026&month=05&day=09'), TODAY), 'custom')
})

test('choosing a Period replaces every date part and keeps the other filters; Custom changes nothing by itself', () => {
  const start = parse('year=2025&month=03&client=A&q=raj')
  assert.equal(queryFor(choosePeriod(start, 'all', TODAY)), 'client=A&q=raj')
  assert.equal(queryFor(choosePeriod(start, 'this-month', TODAY)), 'year=2026&month=11&client=A&q=raj')
  assert.equal(queryFor(choosePeriod(start, 'last-3-months', TODAY)), 'from=2026-09-01&to=2026-11-30&client=A&q=raj')
  assert.equal(queryFor(choosePeriod(start, 'undated', TODAY)), 'client=A&q=raj&undated=true')
  assert.equal(choosePeriod(start, 'custom', TODAY), start)
  // picking the one already chosen does not switch it off (a select, not a toggle)
  const month = parse('year=2026&month=11')
  assert.equal(queryFor(choosePeriod(month, 'this-month', TODAY)), 'year=2026&month=11')
  const undated = parse('undated=true')
  assert.equal(choosePeriod(undated, 'undated', TODAY), undated)
})

test('the More filters badge counts only the filters hidden behind it', () => {
  assert.equal(moreFilterCount(EMPTY_FILTERS, TODAY), 0)
  // client, country, agency and search are in the toolbar, a ready Period is in the Period select
  assert.equal(moreFilterCount(parse('client=A&country=UAE&agency=X&q=raj&year=2026&month=11'), TODAY), 0)
  assert.equal(moreFilterCount(parse('undated=true'), TODAY), 0)
  assert.equal(moreFilterCount(parse('incomplete=true'), TODAY), 1)
  assert.equal(moreFilterCount(parse('order=abc'), TODAY), 1)
  assert.equal(moreFilterCount(parse('year=2025'), TODAY), 1)
  assert.equal(moreFilterCount(parse('year=2025&month=03'), TODAY), 2)
  assert.equal(moreFilterCount(parse('year=2025&month=03&day=09&order=abc&incomplete=true'), TODAY), 5)
  assert.equal(moreFilterCount(parse('from=2026-01-05&to=2026-02-09'), TODAY), 1)
})

test('the phone Filters badge counts every filter on except the search words', () => {
  assert.equal(filterCount(EMPTY_FILTERS), 0)
  assert.equal(filterCount(parse('q=raj')), 0)
  assert.equal(filterCount(parse('q=raj&client=A&year=2026&month=05&incomplete=true')), 3)
})

test('pages are slices of the list that is already loaded: 25 a page, "1-25 of N", clamped', () => {
  assert.deepEqual([10, 25, 50, 100], [...PAGE_SIZE_CHOICES])
  assert.deepEqual(pageWindow(60, 0, 25), { page: 0, pageCount: 3, pageSize: 25, total: 60, from: 1, to: 25 })
  assert.deepEqual(pageWindow(60, 2, 25), { page: 2, pageCount: 3, pageSize: 25, total: 60, from: 51, to: 60 })
  assert.equal(pageWindow(60, 9, 25).page, 2, 'a page past the end falls back to the last one')
  assert.equal(pageWindow(60, -4, 25).page, 0)
  assert.deepEqual(pageWindow(0, 0, 25), { page: 0, pageCount: 1, pageSize: 25, total: 0, from: 0, to: 0 })
  assert.deepEqual(pageWindow(25, 0, 25), { page: 0, pageCount: 1, pageSize: 25, total: 25, from: 1, to: 25 })
  assert.equal(pageWindow(26, 0, 25).pageCount, 2)
  const list = Array.from({ length: 60 }, (_, index) => index)
  assert.equal(pageRows(list, 0, 25).length, 25)
  assert.deepEqual(pageRows(list, 2, 25), list.slice(50))
  assert.deepEqual(pageRows(list, 7, 25), list.slice(50), 'the shrunken list never shows an empty page')
  assert.deepEqual(pageRows([], 0, 25), [])
  assert.deepEqual(pageRows(list, 0, 10), list.slice(0, 10))
})

test('a page starts at 25 (10 on a phone) unless the person chose a size', () => {
  assert.equal(startingPageSize(null, false), 25)
  assert.equal(startingPageSize(null, true), 10)
  assert.equal(startingPageSize(50, true), 50)
  assert.equal(startingPageSize(37, false), 25, 'a size that is not offered is ignored')
})

test('the screen: header totals, ONE toolbar row, chips only while a filter is on, a folded Breakdown, a paged table', async () => {
  const source = await read('screens/travelled/travelled.tsx')
  // the four totals are cards (the shared SummaryCards look), not small pills: Travelled, Clients, Countries, Agencies
  assert.match(source, /<SummaryCards items=\{\[/)
  for (const label of ['Travelled', 'Plane', 'Building2', 'Globe2', 'Users']) assert.match(source, new RegExp(label))
  assert.doesNotMatch(source, /<ul className="tv-stats"/)
  assert.equal([...source.matchAll(/<section className="tv-toolbar"/g)].length, 1, 'one toolbar')
  for (const label of ['Period', 'Client', 'Country', 'Agency']) assert.match(source, new RegExp(`aria-label="${label}"`))
  assert.match(source, /More filters/)
  assert.match(source, /aria-expanded=\{moreOpen\}/)
  for (const label of ['Year', 'Travel month', 'Date of travel', 'Job or order']) assert.match(source, new RegExp(`aria-label="${label}"`))
  assert.match(source, /Missing documents only/)
  assert.match(source, /\{chips\.length > 0 && <div className="tv-chips"/, 'the chips row exists only while a filter is on')
  assert.match(source, /Clear all/)
  for (const gone of ['Show all candidates', 'All travelled candidates are shown when no filters', 'tv-quick', '<details className="tv-breakdown-panel"']) assert.ok(!source.includes(gone), `${gone} is gone`)
  assert.match(source, /aria-expanded=\{breakdownOpen\}/)
  assert.match(source, /\{breakdownOpen && <div id="tv-breakdowns"/, 'the breakdown cards are not even rendered while folded')
  assert.match(source, /useState\(readBreakdownOpen\)/)
  assert.match(source, /const readBreakdownOpen = \(\) => \{ try \{[^}]*\} catch \{ return false \} \}/, 'localStorage is read inside try/catch and closed is the default')
  assert.match(source, /const saveBreakdownOpen = [^\n]*try \{[^\n]*localStorage\.setItem[^\n]*\} catch/)
  assert.match(source, /<Pager page=\{page\} pageSize=\{pageSize\} total=\{rows\.length\} noun="candidates"/)
  assert.match(source, /const shown = pageRows\(rows, page, pageSize\)/, 'pages are slices of the loaded rows (no new request)')
  assert.match(source, /useEffect\(\(\) => \{ setPage\(0\) \}, \[filterQuery\]\)/, 'a new filter goes back to page one')
  assert.equal([...source.matchAll(/useTravelled\(/g)].length, 1, 'one read')
})

test('the toolbar and the table sit above the fold: slim header, 40px controls, one row that wraps, the whole table with a header that sticks while the page scrolls', async () => {
  const css = await read('styles/travelled.css')
  assert.match(css, /\.tv-toolbar \{[^}]*display: flex; flex-wrap: wrap;[^}]*\}/)
  assert.match(css, /\.tv-search \{[^}]*height: 40px;/)
  assert.match(css, /:is\(\.tv-sel, \.tv-field\) \.custom-select-trigger \{[^}]*height: 40px; min-height: 40px;/)
  assert.match(css, /\.tv-more-toggle \{[^}]*height: 40px;/)
  assert.match(css, /\.tv-meta \{[^}]*min-height: 32px;/)
  assert.match(css, /\.tv-more-panel \{ position: absolute;/, 'More filters opens over the list, it never pushes the table down')
  assert.match(css, /\.tv-table-wrap \{ position: relative; overflow: clip;/, 'the table is shown whole (no inner scroll box) and its box contains the screen-reader texts (no page stretching)')
  assert.doesNotMatch(css, /\.tv-table-wrap \{[^}]*(max-height|overflow-y|overflow: auto|overflow: hidden)/, 'no short inner scroll box: the PAGE scrolls like Clients and Tasks')
  assert.match(css, /\.tv-table th \{ position: sticky; top: var\(--topbar-h, 64px\);/, 'the header sticks under the top bar while the page scrolls')
  assert.match(css, /\.tv-table td \{[^}]*height: 56px;/)
  assert.doesNotMatch(css, /\.tv-c-[a-z]+ \{ width: \d+%; \}/, 'columns size to their content, no fixed shares')
  // the order of the page: header, toolbar, chips row, breakdown (folded), table
  const source = await read('screens/travelled/travelled.tsx')
  const order = ['<PageHeader', 'className="tv-toolbar"', 'className="tv-meta"', 'tv-breakdowns', '<TravelTable'].map(marker => source.indexOf(marker))
  assert.ok(order.every((position, index) => position > 0 && (index === 0 || position > order[index - 1])), order.join(','))
})

test('phone: search and one Filters button, totals scroll sideways, filters open as a bottom sheet, rows are cards', async () => {
  const css = await read('styles/travelled.css')
  const source = await read('screens/travelled/travelled.tsx')
  assert.match(source, /const PHONE = '\(max-width: 768px\)'/)
  assert.match(source, /\{phone \? 'Filters' : 'More filters'\}/)
  assert.match(source, /\{!phone && <>/, 'the four selects leave the toolbar on a phone')
  assert.match(source, /\{phone && <>\s*<Field label="Period">/, 'and move into the sheet')
  const phone = css.slice(css.indexOf('@media (max-width: 768px)'))
  assert.match(phone, /\.tv-more-panel \{ position: fixed;[^}]*bottom: 0;[^}]*border-radius: 16px 16px 0 0;/)
  assert.match(phone, /\.tv-backdrop \{ position: fixed; inset: 0;/)
  assert.match(css, /@media \(max-width: 899px\) \{[\s\S]*\.tv-table-wrap \{ overflow: visible; \}/, 'no clipping box for cards')
  assert.match(css, /\.tv-table td::before \{ content: attr\(data-label\);/)
  assert.match(css, /\.tv \{ overflow-x: clip; \}/, 'nothing pushes the page sideways')
})

test('keyboard: Escape closes More filters and gives the focus back; outside clicks ignore the select menus; every control has a visible focus ring', async () => {
  const source = await read('screens/travelled/travelled.tsx')
  assert.match(source, /event\.key !== 'Escape' \|\| document\.querySelector\('\.custom-select-popup'\)/)
  assert.match(source, /moreButton\.current\?\.focus\(\)/)
  assert.match(source, /target\.closest\('\.custom-select-popup'\)/)
  assert.match(source, /role="dialog" aria-label=/)
  const css = await read('styles/travelled.css')
  assert.match(css, /\.tv-more-toggle, \.tv-breakdown-toggle, \.tv-sheet-close\):focus-visible \{ outline: 2px solid var\(--primary\);/)
})

test('light and dark follow the theme: no hard-coded colours in the new rules', async () => {
  const css = await read('styles/travelled.css')
  const rules = css.split('\n').filter(line => /^\s*\.tv(-|\b)/.test(line) && !/box-shadow|rgba\(0, 0, 0/.test(line))
  for (const line of rules) assert.doesNotMatch(line.replace(/var\(--warning-ink, #9a5b00\)/, ''), /#[0-9a-f]{3,8}\b/i, line.slice(0, 80))
})

test('the rows keep their information but lose the redundant captions', async () => {
  const source = await read('screens/travelled/travelled.tsx')
  const table = source.slice(source.indexOf('function TravelTable'))
  const headings = [...table.matchAll(/<th(?: className="[^"]*")?>([^<]+)<\/th>/g)].map(match => match[1])
  assert.deepEqual(headings, ['S NO', 'DOC', 'Date of travel', 'Candidate', 'Client', 'Country', 'Job', 'Agency', 'Uploaded documents', 'Recruiter'], 'the owner columns, in order')
  const labels = [...table.matchAll(/<td data-label="([^"]+)"/g)].map(match => match[1])
  assert.deepEqual(labels, headings, 'every cell carries its label for the phone cards, in column order')
  const colgroup = table.match(/<colgroup>(.*?)<\/colgroup>/)[1]
  assert.equal([...colgroup.matchAll(/<col /g)].length, 10, 'one col per column')
  assert.ok(!/Travel date|>Documents</.test(table), 'no old heading')
  assert.doesNotMatch(table, /TravellerIdChip|row\.careId|CI-T/, 'no Data Bank ID and no old CI-T number in the table')
  assert.doesNotMatch(table, /row\.orderRef|className="ident"/, 'no order number under the job')
  assert.match(table, /<Link to=\{`\/candidates\/\$\{row\.candidateId\}`\}>\{row\.name\}<\/Link>\s*<small className="tv-passport tnum">\{row\.passportNumber \|\| <span className="tv-muted">Passport not added<\/span>\}<\/small>\s*\{row\.travellerId && <span className="tv-traveller tnum" title=\{`Traveller number \$\{row\.travellerId\}`\}>\{row\.travellerId\}<\/span>\}/, 'the passport number sits under the name and the traveller number under the passport number')
  const css = await readFile(new URL('../src/styles/travelled.css', import.meta.url), 'utf8')
  assert.match(css, /\.tv-table td\.tv-who \.tv-traveller \{[^}]*display: block[^}]*width: max-content; min-width: 64px; margin: 4px auto 0;[^}]*text-align: center[^}]*font-variant-numeric: tabular-nums/, 'the traveller number is a centred tabular chip of one fixed minimum width under the passport number')
  assert.match(table, /formatTravelDay\(row\.commencementDate\)/, 'DOC reads as dd Mon yyyy like every table date (dd/mm/yyyy is for inputs)')
  assert.match(table, /serialNumber\(page, pageSize, index\)/, 'S NO runs across pages')
  assert.match(table, /row\.status !== 'deployed' &&/, 'a "Deployed" pill on every row of a list of travellers says nothing')
  assert.doesNotMatch(table, /<small>Complete<\/small>|missing<\/small>/, 'no repeated captions in the Documents cell')
  assert.match(table, /<details className="tv-doc-details">/, 'the missing documents are still one click away')
  assert.match(table, /Review documents/)
  assert.match(table, /Add agency/)
})

test('the table scroll box contains the absolutely placed screen-reader texts of its rows, so a long page cannot stretch the document', async () => {
  const css = await read('../src/styles/travelled.css')
  assert.match(css, /\.tv-table-wrap \{ position: relative;/)
})

test('the full table, the page scrolls: no inner scroll box, a page change scrolls the table into view, the chip is centred and as wide for CI 01 as for CI 100', async () => {
  const css = await read('../src/styles/travelled.css')
  const source = await read('../src/screens/travelled/travelled.tsx')
  assert.doesNotMatch(css, /calc\(100dvh - 380px\)/, 'the old short box is gone')
  assert.match(css, /\.tv-table-wrap \{ position: relative; overflow: clip; scroll-margin-top:/, 'clip is not a scroll container, so the sticky header follows the page')
  assert.match(source, /wrap\.current\?\.scrollIntoView\(\{ block: 'start' \}\)/)
  assert.doesNotMatch(source, /wrap\.current\?\.scrollTo/)
  assert.match(css, /@media \(max-width: 899px\) \{[\s\S]*\.tv-table td\.tv-who \.tv-traveller \{ margin-left: 0; \}/, 'on a phone card the chip sits left like the rest of the card')
  const dateColumn = source.match(/<td data-label="Date of travel"[^\n]*/)[0]
  assert.match(dateColumn, /row\.travelDate \? formatTravelDay\(row\.travelDate\)/, 'the date comes from the air-ticket stage (row.travelDate), right after DOC')
})
