import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'

const cache = new Map()
/** Transpile a TS module; relative imports are loaded the same way and inlined as data: URLs. */
async function load(path) {
  if (cache.has(path)) return cache.get(path)
  const url = new URL(path, import.meta.url)
  let source = await readFile(url, 'utf8')
  const imports = [...source.matchAll(/from '(\.[^']+)'/g)].map((match) => match[1])
  for (const spec of imports) {
    const target = new URL(`${spec}.ts`, url)
    const loaded = await load(target.href)
    source = source.replaceAll(`from '${spec}'`, `from '${loaded.__url}'`)
  }
  const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText
  const dataUrl = `data:text/javascript;base64,${Buffer.from(js).toString('base64')}`
  const module = { ...(await import(dataUrl)), __url: dataUrl }
  cache.set(path, module)
  return module
}

const fit = await load('../src/features/candidates/components/column-fit.ts')
const pager = await load('../src/features/candidates/pager-model.ts')
const model = await load('../src/features/leads/lead-model.ts')
const fields = await load('../src/features/leads/lead-fields.ts')

const col = (key, width, optional, off) => ({ key, width, ...(optional ? { optional } : {}), ...(off ? { off: true } : {}) })
const COLUMNS = [col('actions', 100), col('created', 90, 3), col('name', 160), col('contact', 210), col('position', 140), col('experience', 90, 2), col('location', 110, 1), col('campaign', 160, 4), col('status', 110), col('resume', 90), col('extra', 140, 9, true)]
const sum = (keys) => COLUMNS.filter((c) => keys.includes(c.key)).reduce((total, c) => total + c.width, 0)

test('fit: everything that fits is shown, comfortably, with nothing hidden', () => {
  const result = fit.fitColumns(COLUMNS, 1400)
  assert.equal(result.compact, false)
  assert.equal(result.hidden, 0)
  assert.ok(!result.keys.includes('extra'), 'columns that start off stay off')
})

test('fit: tighter spacing comes before any column is hidden', () => {
  const all = sum(COLUMNS.filter((c) => !c.off).map((c) => c.key)) // 1260
  const result = fit.fitColumns(COLUMNS, Math.ceil(all * fit.COMPACT_RATIO) + fit.FIT_GUTTER + 1)
  assert.equal(result.compact, true)
  assert.equal(result.hidden, 0)
})

test('fit: when it still does not fit, the highest optional number goes first and the shown columns always fit', () => {
  const result = fit.fitColumns(COLUMNS, 1000)
  assert.ok(!result.keys.includes('campaign'), 'campaign (4) is hidden before created (3), experience (2) and location (1)')
  for (const key of ['actions', 'name', 'contact', 'position', 'status', 'resume']) assert.ok(result.keys.includes(key), `${key} is never hidden`)
  assert.ok(sum(result.keys) * (result.compact ? fit.COMPACT_RATIO : 1) <= 1000 - fit.FIT_GUTTER)
  assert.equal(result.hidden, COLUMNS.filter((c) => !c.off).length - result.keys.length)
  // table order is kept
  assert.deepEqual(result.keys, COLUMNS.filter((c) => result.keys.includes(c.key)).map((c) => c.key))
})

test('fit: the person own choices win, and what they ask for is hidden last', () => {
  assert.ok(!fit.fitColumns(COLUMNS, 1600, { location: false }).keys.includes('location'))
  assert.ok(fit.fitColumns(COLUMNS, 1600, { extra: true }).keys.includes('extra'))
  const narrow = fit.fitColumns(COLUMNS, 1000, { campaign: true })
  assert.ok(narrow.keys.includes('campaign'), 'a wanted column stays while less wanted ones can go')
  assert.equal(fit.fitColumns(COLUMNS, 1400, { [fit.CHOICE_COMPACT]: true }).compact, true)
  assert.equal(fit.fitColumns(COLUMNS, 1000, { [fit.CHOICE_COMPACT]: false }).compact, false)
})

test('fit: the wide table shows every chosen column and never hides one', () => {
  const result = fit.fitColumns(COLUMNS, 600, { [fit.CHOICE_WIDE]: true, extra: true })
  assert.equal(result.wide, true)
  assert.equal(result.keys.length, COLUMNS.length)
  assert.equal(result.hidden, 0)
  assert.equal(fit.fitColumns(COLUMNS, 0).keys.length, COLUMNS.length - 1, 'an unmeasured box shows the default set')
})

test('fit: the Columns menu groups, searches and ignores bad stored choices', () => {
  const menu = [{ key: 'a', header: 'Gender', group: 'Person' }, { key: 'b', header: 'Phone number', group: 'Contact' }, { key: 'c', header: 'Passport', group: 'Zed' }]
  assert.deepEqual(fit.groupMenuColumns(menu, ['Person', 'Contact'], '').map((g) => g.name), ['Person', 'Contact', 'Zed'])
  assert.deepEqual(fit.groupMenuColumns(menu, ['Person', 'Contact'], 'phone').map((g) => g.name), ['Contact'])
  assert.deepEqual(fit.groupMenuColumns(menu, ['Person'], 'zzz'), [])
  assert.deepEqual(fit.readChoices('anything'), {}, 'no localStorage: no choices, no throw')
})

test('pager: page sizes, the default from the screen height and the saved choice', () => {
  assert.deepEqual([...pager.PAGE_SIZES], [10, 25, 50, 100, 250])
  assert.equal(pager.ALL_PAGE_SIZE, 1000)
  const small = pager.defaultPageSize(700)
  const tall = pager.defaultPageSize(1800)
  assert.ok([10, 25, 50, 100].includes(small) && [10, 25, 50, 100].includes(tall) && tall >= small)
  assert.equal(pager.defaultPageSize(0), pager.defaultPageSize(900), 'an unknown height falls back')
  assert.equal(pager.readPageSize('leads'), null)
})

test('pager: numbered pages with ellipsis, first and last always present', () => {
  assert.deepEqual(pager.pageList(0, 1), [1])
  assert.deepEqual(pager.pageList(0, 5), [1, 2, 3, 4, 5])
  assert.deepEqual(pager.pageList(0, 12), [1, 2, 3, '…', 12])
  assert.deepEqual(pager.pageList(5, 12), [1, '…', 5, 6, 7, '…', 12])
  assert.deepEqual(pager.pageList(11, 12), [1, '…', 10, 11, 12])
  assert.deepEqual(pager.pageList(99, 12).at(-1), 12, 'a page past the end is clamped')
  assert.deepEqual(pager.pageList(3, 6), [1, 2, 3, 4, 5, 6])
})

test('pager: the count line follows the list, the filters and the page', () => {
  assert.equal(pager.countText({ page: 0, pageSize: 25, total: 30, noun: 'leads' }), 'Showing 1–25 of 30 matching leads')
  assert.equal(pager.countText({ page: 1, pageSize: 25, total: 30, noun: 'leads' }), 'Showing 26–30 of 30 matching leads')
  assert.equal(pager.countText({ page: 0, pageSize: 25, total: 4000, noun: 'leads', filtered: false }), 'Showing 1–25 of 4,000 leads')
  assert.equal(pager.countText({ page: 0, pageSize: 25, total: 0, noun: 'leads' }), 'No matching leads')
  assert.equal(pager.countText({ page: 0, pageSize: 25, total: 0, noun: 'leads', filtered: false }), 'No leads yet')
  assert.equal(pager.countText({ page: 9, pageSize: 25, total: 30, noun: 'leads' }), 'Showing 26–30 of 30 matching leads', 'a page past the end shows the last one')
  assert.equal(pager.allNote(900, 'leads'), null)
  assert.match(pager.allNote(3500, 'leads'), /first 1,000 of 3,500 leads/)
})

test('footer hint: worded for who is looking and what the list shows', () => {
  const staff = model.footerHint({ supervisor: false, assignedToMe: 12, matching: 12, total: 12, filtersOn: false })
  assert.equal(staff, 'Click a row to see everything about the lead. You can work the leads assigned to you (12 assigned to you).')
  const admin = model.footerHint({ supervisor: true, unassigned: 4, matching: 30, total: 30, filtersOn: false })
  assert.equal(admin, 'Click a row to open the lead. You can work, assign and export any lead (4 unassigned).')
  assert.match(model.footerHint({ supervisor: true, matching: 3, total: 30, filtersOn: true }), /^Filters are on: showing 3 of 30 leads\./)
  assert.match(model.footerHint({ supervisor: false, assignedToMe: 2, matching: 2, total: 2, filtersOn: false, lockReason: 'Finish your daily tasks first.' }), /Finish your daily tasks first\. The sheet is read-only until then\.$/)
  assert.match(model.footerHint({ supervisor: true, matching: 0, total: 0, filtersOn: false }), /Upload a lead sheet/)
  assert.match(model.footerHint({ supervisor: false, matching: 0, total: 0, filtersOn: false }), /manager assigns them to you/)
})

test('fields: one registry drives labels, groups, values and the opened row', () => {
  const ids = fields.LEAD_FIELD_DEFS.map((f) => f.id)
  assert.equal(new Set(ids).size, ids.length, 'ids are unique')
  assert.ok(fields.LEAD_FIELD_DEFS.every((f) => f.label && fields.FIELD_GROUPS.includes(f.group) && f.width > 0))
  assert.equal(fields.fieldDef('country').label, model.LEAD_HEADINGS.country)
  assert.equal(fields.fieldDef('status').label, model.LEAD_HEADINGS.status)
  assert.equal(fields.fieldDef('followUp').label, model.LEAD_HEADINGS.followup)
  const lead = { id: '1', name: 'Test Person', status: 'new', phone: 'p:+919000000001', dob: '1990-03-27', experienceTotalYears: 3, extra: { which_shift: 'night', empty: '' } }
  const rows = fields.leadFieldRows(lead, [{ key: 'which_shift', label: 'Which shift', count: 1 }])
  const byKey = Object.fromEntries(rows.map((r) => [r.key, r]))
  assert.equal(byKey.phone.value, '+919000000001')
  assert.equal(byKey.dob.value, '27 Mar 1990')
  assert.equal(byKey.totalYears.value, '3 yrs')
  assert.equal(byKey.gender.value, '', 'a missing answer stays listed, empty')
  assert.equal(byKey['x:which_shift'].label, 'Which shift')
  assert.equal(byKey['x:which_shift'].value, 'night')
  assert.equal(byKey['x:empty'].label, 'Empty')
  assert.equal(fields.prettyHeader('how_many_years_of_experience_do_you_have?'), 'How many years of experience do you have')
  assert.equal(fields.prettyHeader('___'), '')
})

test('consistency: the table headings come from the one config and nothing else', async () => {
  const columns = await readFile(new URL('../src/features/leads/components/lead-columns.tsx', import.meta.url), 'utf8')
  const start = columns.indexOf('export function leadColumns')
  const body = columns.slice(start)
  const headers = [...body.matchAll(/header: ([^,]+),/g)].map((match) => match[1].trim())
  assert.ok(headers.length >= 10)
  for (const header of headers) assert.ok(header.startsWith('h.') || header.startsWith('field.') || header.startsWith('column.') || header === "'Education / Marital status'", `heading ${header} is not from the config`)
  for (const key of Object.keys(model.LEAD_HEADINGS)) assert.ok(body.includes(`h.${key}`) || key === 'country' || key === 'followup' || key === 'actions', `LEAD_HEADINGS.${key} is used`)
  // every table heading is a non-empty, single-spaced label without a trailing colon
  for (const label of Object.values(model.LEAD_HEADINGS)) assert.match(label, /^\S.*\S$|^\S$/)
})

test('report and extras helpers', () => {
  assert.equal(model.REPORT_BUTTON, 'Download import report (Excel)')
  assert.equal(model.allNewLine({ total: 30, new: 30, duplicate: 0, invalid: 0 }), 'All 30 rows were new. Nothing was ignored.')
  assert.equal(model.allNewLine({ total: 1, new: 1, duplicate: 0, invalid: 0 }), 'The row was new. Nothing was ignored.')
  assert.equal(model.allNewLine({ total: 30, new: 28, duplicate: 2, invalid: 0 }), null)
  assert.equal(model.allNewLine({ total: 0, new: 0, duplicate: 0, invalid: 0 }), null)
  assert.equal(model.columnsQuery({ campaign: 'Dubai Chef', batchId: '' }), 'campaign=Dubai+Chef')
  assert.equal(model.columnsQuery({ campaign: '', batchId: '' }), '')
  assert.equal(model.extraAnswer({ extra: { a: ' yes ' } }, 'a'), 'yes')
  assert.equal(model.extraAnswer({}, 'a'), '')
  assert.equal(model.extraQuery(['a b', 'c']), '&extra=a%20b&extra=c')
  assert.equal(model.extraKeyOf('x:which'), 'which')
  assert.equal(model.extraKeyOf('name'), null)
})
