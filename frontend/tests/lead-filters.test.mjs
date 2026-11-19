import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'
import { withRoleLabels } from './fixtures/role-labels-url.mjs'

async function load(path) {
  const source = await readFile(new URL(path, import.meta.url), 'utf8')
  const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText
  return import(`data:text/javascript;base64,${Buffer.from(withRoleLabels(js)).toString('base64')}`)
}
const source = (path) => readFile(new URL(path, import.meta.url), 'utf8')

const m = await load('../src/features/leads/lead-model.ts')
const bulk = await load('../src/features/leads/lead-bulk-model.ts')
const base = m.EMPTY_FILTERS
const sort = { key: 'created', dir: 'desc' }
const query = (patch) => new URLSearchParams(m.buildLeadQuery({ ...base, ...patch }, sort))
const entries = (patch) => Object.fromEntries(query(patch))

test('the query carries exactly the filters that are set, under the names the server reads', () => {
  assert.deepEqual(entries({}), { sort: 'created', direction: 'desc' })
  assert.deepEqual(entries({ assigned: 'user-yuvaraj', from: '2026-11-19', to: '2026-11-19' }), {
    owner: 'user-yuvaraj', created_from: '2026-11-19', created_to: '2026-11-19', date_field: 'created', sort: 'created', direction: 'desc',
  })
  assert.deepEqual(entries({ status: 'contacted', campaign: 'Qatar, Kitchen (Doha)', position: 'Kitchen Helper', country: 'Qatar', platform: 'ig' }), {
    status: 'contacted', campaign: 'Qatar, Kitchen (Doha)', position: 'Kitchen Helper', country: 'Qatar', platform: 'ig', sort: 'created', direction: 'desc',
  })
  const all = entries({ q: ' Ravi ,  chef ', status: 'new', assigned: 'me', from: '2026-11-01', to: '2026-11-19', dateField: 'created', batchId: 'b1', followUp: 'due', recent: 'today' })
  assert.deepEqual(all, {
    q: 'Ravi,chef', status: 'new', owner: 'me', created_from: '2026-11-01', created_to: '2026-11-19', date_field: 'created', uploaded_today: 'true', batch_id: 'b1', follow_up: 'due',
    sort: 'created', direction: 'desc',
  })
})

test('every status the select offers, every owner choice and every date shape is sent as the server expects (codes and ids, never labels)', () => {
  for (const status of m.LEAD_STATUSES) {
    assert.equal(entries({ status: status.value }).status, status.value)
    assert.match(status.value, /^[a-z_]+$/, 'a code, not a display label')
  }
  for (const assigned of ['', 'me', 'none', 'c0ffee00-0000-4000-8000-000000000001']) assert.equal(query({ assigned }).get('owner'), assigned || null)
  assert.equal(query({ from: '2026-11-19' }).get('created_to'), null, 'from only')
  assert.equal(query({ to: '2026-11-19' }).get('created_from'), null, 'to only')
  assert.equal(query({ to: '2026-11-19' }).get('date_field'), 'created')
  assert.equal(query({ dateField: 'created', from: '2026-11-19' }).get('date_field'), 'created')
})

test('a date is always the calendar day as typed: no time, no timezone shift', () => {
  for (const zone of ['Asia/Kolkata', 'America/Los_Angeles', 'UTC', 'Pacific/Auckland']) {
    process.env.TZ = zone
    assert.equal(query({ from: '2026-11-19', to: '2026-11-19' }).get('created_from'), '2026-11-19', zone)
    assert.equal(query({ from: '2026-11-19', to: '2026-11-19' }).get('created_to'), '2026-11-19', zone)
  }
  assert.equal(m.agencyDay(new Date('2026-11-18T18:30:00Z')), '2026-11-19', 'IST midnight is the 19th')
  assert.equal(m.agencyDay(new Date('2026-11-18T18:29:59Z')), '2026-11-18')
})

test('a half-typed or impossible date is not sent (the list would turn into an error), a real one is', () => {
  for (const bad of ['', '2026-11', '20261-11-19', '0000-00-00', '2026-13-01', '2026-02-30', '19-11-2026', '2026-11-19T00:00:00Z', 'tomorrow']) {
    assert.equal(m.validDay(bad), false, bad)
    assert.equal(query({ from: bad }).has('created_from'), false, bad)
    assert.equal(query({ to: bad }).has('date_field'), false, `${bad}: no dates, no date_field`)
  }
  for (const good of ['2026-11-19', '2024-02-29', '2026-12-31', '2026-01-01']) assert.equal(m.validDay(good), true, good)
  assert.equal(m.validDay('2026-02-29'), false, '2026 is not a leap year')
  assert.equal(query({ from: '2026-11-19', to: '20261-1-1' }).has('created_to'), false)
})

test('the status select shows what is filtered: a card that sets two statuses gets its own option', () => {
  assert.deepEqual(m.statusChoices('').map(([value]) => value), m.LEAD_STATUSES.map((s) => s.value))
  assert.equal(m.statusChoices('new').length, m.LEAD_STATUSES.length)
  const pair = m.cardFilters('interested').status
  assert.equal(pair, 'interested,resume_received')
  const choices = m.statusChoices(pair)
  assert.equal(choices.length, m.LEAD_STATUSES.length + 1)
  assert.deepEqual(choices.at(-1), [pair, 'Interested or Interested (resume in)'])
})

test('nothing that narrows the list is hidden: upload, uploaded today, follow-ups due and the dates are removable chips', () => {
  assert.deepEqual(m.filterChips(base), [])
  assert.deepEqual(m.filterChips({ ...base, status: 'new', assigned: 'me', campaign: 'X' }), [], 'selects show their own value')
  const chips = m.filterChips({ ...base, batchId: 'b', recent: 'today', followUp: 'due', from: '2026-11-18', to: '2026-11-19', dateField: 'uploaded' })
  assert.deepEqual(chips.map((c) => c.key), ['batch', 'recent', 'followUp', 'dates'])
  assert.equal(chips[3].label, 'Uploaded 18 Nov 2026 to 19 Nov 2026')
  assert.equal(m.filterChips({ ...base, from: '2026-11-19', to: '2026-11-19' })[0].label, 'Created on 19 Nov 2026')
  assert.equal(m.filterChips({ ...base, from: '2026-11-19' })[0].label, 'Created from 19 Nov 2026')
  assert.equal(m.filterChips({ ...base, to: '2026-11-19' })[0].label, 'Created up to 19 Nov 2026')
  // Removing a chip clears exactly its own filter and nothing else.
  for (const chip of chips) {
    const on = { ...base, batchId: 'b', recent: 'today', followUp: 'due', from: '2026-11-18', to: '2026-11-19', status: 'contacted', assigned: 'yuv' }
    const after = { ...on, ...chip.clear }
    assert.equal(after.status, 'contacted')
    assert.equal(after.assigned, 'yuv')
    assert.ok(!m.filterChips(after).some((c) => c.key === chip.key), chip.key)
    assert.equal(m.filterChips(after).length, m.filterChips(on).length - 1, chip.key)
  }
})

test('a card on top of other filters keeps every one of them visible (Follow-ups due leaves a chip once a status is added)', () => {
  const filters = { ...base, ...m.cardFilters('follow_ups'), assigned: 'me', status: 'interested' }
  assert.equal(m.activeCard(filters), '', 'the card is no longer lit...')
  assert.deepEqual(m.filterChips(filters).map((c) => c.key), ['followUp'], '...so the filter that is still on shows as a chip')
  assert.equal(query(filters).get('follow_up'), 'due')
})

test('the Excel export, the select-all-matching filter and the list use the very same parameters', async () => {
  const filters = { ...base, status: 'interested,resume_received', assigned: 'user-yuvaraj', from: '2026-11-18', to: '2026-11-19', dateField: 'created', q: 'chef', recent: 'today', campaign: 'Qatar, Kitchen (Doha)' }
  const built = m.buildLeadQuery(filters, sort)
  const asFilter = bulk.filterFromQuery(built)
  assert.deepEqual(asFilter, {
    q: 'chef', status: 'interested,resume_received', campaign: 'Qatar, Kitchen (Doha)', owner: 'user-yuvaraj', created_from: '2026-11-18', created_to: '2026-11-19',
    date_field: 'created', uploaded_today: 'true',
  })
  assert.equal(new URLSearchParams(built).get('campaign'), asFilter.campaign, 'a comma inside a name survives the round trip')
  assert.match(await source('../src/screens/leads/leads.tsx'), /downloadLeadExport\(query\)/, 'the export sends the list query itself')
})

test('the list never shows an older request answer: only the newest request may set the rows, the error or the loading light', async () => {
  const hook = await source('../src/features/leads/use-leads.ts')
  assert.match(hook, /const ticket = \+\+latest\.current/)
  assert.match(hook, /if \(signal\?\.aborted \|\| ticket !== latest\.current\) return\s+setSnapshot/)
  assert.match(hook, /if \(!signal\?\.aborted && ticket === latest\.current\) setLoading\(false\)/)
  const page = await source('../src/screens/leads/leads.tsx')
  assert.match(page, /useEffect\(\(\) => \{ setPage\(0\) \}, \[filterKey, pageSize\]\)/, 'a changed filter returns to the first page')
})
