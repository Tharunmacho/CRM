import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'

async function load(path) {
  const source = await readFile(new URL(path, import.meta.url), 'utf8')
  const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText
  return import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`)
}

const m = await load('../src/features/leads/lead-model.ts')

const NOW = new Date(2026, 10, 6, 12, 0, 0) // 6 Nov 2026, local noon

test('status map: known statuses have a label and a tone, unknown ones degrade to plain text', () => {
  assert.deepEqual(m.leadStatus('interested'), { value: 'interested', label: 'Interested', tone: 'ok' })
  assert.equal(m.leadStatus('wrong_number').tone, 'bad')
  assert.equal(m.leadStatus('not_interested').tone, 'quiet')
  assert.equal(m.leadStatus('something_new').label, 'Something new')
  assert.equal(m.leadStatus('').label, 'New')
  assert.equal(m.leadStatus(null).value, '')
  assert.ok(m.LEAD_STATUSES.every((s) => s.label && s.tone))
})

test('converted can only be reached through Convert, not set by hand', () => {
  assert.ok(!m.SETTABLE_STATUSES.some((s) => s.value === 'converted'))
  assert.equal(m.SETTABLE_STATUSES.length, m.LEAD_STATUSES.length - 1)
})

test('call outcome moves the status exactly as the server table says', () => {
  assert.equal(m.statusAfterCall('answered', 'new'), 'contacted')
  assert.equal(m.statusAfterCall('answered', 'interested'), null)
  assert.equal(m.statusAfterCall('interested', 'contacted'), 'interested')
  assert.equal(m.statusAfterCall('interested', 'resume_requested'), null)
  assert.equal(m.statusAfterCall('interested', 'resume_received'), null)
  assert.equal(m.statusAfterCall('no_answer', 'new'), null)
  assert.equal(m.statusAfterCall('wrong_number', 'new'), 'wrong_number')
  assert.equal(m.statusAfterCall('not_interested', 'interested'), 'not_interested')
  assert.deepEqual(m.CALL_OUTCOMES.map((o) => o.value), ['answered', 'interested', 'no_answer', 'wrong_number', 'not_interested'])
})

test('search: commas combine terms, case and spacing do not matter, the hint follows the typing', () => {
  assert.deepEqual(m.searchTerms('  Ravi ,  chef  ,RAVI,, '), ['Ravi', 'chef'])
  assert.equal(m.normalizeSearch('ravi, chef,'), 'ravi,chef')
  assert.equal(m.normalizeSearch('ravi,'), m.normalizeSearch('ravi'))
  assert.match(m.searchHint(''), /commas/)
  assert.match(m.searchHint("ravi"), /"ravi"/)
  assert.match(m.searchHint('ravi, chef'), /all of: "ravi", "chef"/)
})

const base = m.EMPTY_FILTERS
const sort = { key: 'created', dir: 'desc' }

test('query builder sends only what is set, with the server parameter names', () => {
  assert.equal(m.buildLeadQuery(base, sort), 'sort=created&direction=desc')
  const q = new URLSearchParams(m.buildLeadQuery({ ...base, q: 'ravi, chef', status: 'new', campaign: 'Dubai', position: 'Chef', country: 'Dubai', platform: 'ig', assigned: 'me', from: '2026-11-01', to: '2026-11-06', batchId: 'b1' }, { key: 'follow_up', dir: 'asc' }))
  assert.equal(q.get('q'), 'ravi,chef')
  assert.equal(q.get('owner'), 'me')
  assert.equal(q.get('created_from'), '2026-11-01')
  assert.equal(q.get('created_to'), '2026-11-06')
  assert.equal(q.get('batch_id'), 'b1')
  assert.equal(q.get('sort'), 'follow_up')
  assert.equal(q.get('direction'), 'asc')
  assert.equal(m.buildLeadQuery({ ...base, assigned: 'unassigned' }, sort).includes('owner=unassigned'), true)
  // typing a trailing comma does not change the request
  assert.equal(m.buildLeadQuery({ ...base, q: 'ravi,' }, sort), m.buildLeadQuery({ ...base, q: 'ravi' }, sort))
})

test('KPI cards set filters and light up only while the filters are exactly theirs', () => {
  assert.equal(m.activeCard(base, NOW), 'total')
  assert.deepEqual(m.cardFilters('not_contacted', NOW), { status: 'new', from: '', to: '' })
  assert.equal(m.activeCard({ ...base, ...m.cardFilters('not_contacted', NOW) }, NOW), 'not_contacted')
  assert.equal(m.activeCard({ ...base, ...m.cardFilters('new_today', NOW) }, NOW), 'new_today')
  assert.equal(m.cardFilters('new_today', NOW).from, '2026-11-06')
  assert.equal(m.activeCard({ ...base, ...m.cardFilters('converted', NOW) }, NOW), 'converted')
  assert.equal(m.activeCard({ ...base, status: 'contacted' }, NOW), '')
  assert.equal(m.activeCard({ ...base, status: 'new', from: '2026-01-01' }, NOW), '')
  assert.equal(m.summaryScope({ assigned: 'me' }), 'mine')
  assert.equal(m.summaryScope({ assigned: '' }), 'all')
  assert.equal(m.activeLeadFilterCount({ ...base, q: 'abc', status: 'new', campaign: 'x' }), 2)
})

test('dates: created over year, follow-up wording and state, local inputs', () => {
  assert.deepEqual(m.createdParts('2026-11-04T19:53:51+05:30') && Object.keys(m.createdParts('2026-11-04T19:53:51+05:30')), ['day', 'year'])
  assert.equal(m.createdParts(''), null)
  assert.equal(m.createdParts('nonsense'), null)
  const day = (offset, hour = 9) => new Date(2026, 10, 6 + offset, hour, 0, 0).toISOString()
  assert.equal(m.followUpState(day(-2), 'interested', NOW), 'overdue')
  assert.equal(m.followUpState(day(0, 18), 'interested', NOW), 'today')
  assert.equal(m.followUpState(day(1), 'interested', NOW), 'soon')
  assert.equal(m.followUpState(day(9), 'interested', NOW), 'later')
  assert.equal(m.followUpState(day(-2), 'converted', NOW), 'closed')
  assert.equal(m.followUpState(day(-2), 'not_interested', NOW), 'closed')
  assert.equal(m.followUpState(null, 'new', NOW), 'none')
  assert.equal(m.followUpLabel(day(0), NOW), 'Today')
  assert.equal(m.followUpLabel(day(1), NOW), 'Tomorrow')
  assert.equal(m.followUpLabel(day(-1), NOW), 'Yesterday')
  assert.equal(m.followUpLabel(day(-3), NOW), '3 days late')
  assert.equal(m.followUpLabel(day(10), NOW), '16 Nov')
  const local = m.toLocalInput(day(3, 14))
  assert.match(local, /^2026-11-09T14:00$/)
  assert.equal(new Date(m.fromLocalInput(local)).getHours(), 14)
  assert.equal(m.fromLocalInput(''), null)
  assert.equal(m.dobLabel('1990-09-25'), '25 Sep 1990')
  assert.equal(m.dobLabel('garbage'), 'garbage')
  assert.equal(m.dobLabel(null), '')
})

test('cells: years, platform, phone and WhatsApp link', () => {
  assert.equal(m.yearsLabel(10), '10 yrs')
  assert.equal(m.yearsLabel('1'), '1 yr')
  assert.equal(m.yearsLabel(0.5), '0.5 yrs')
  assert.equal(m.yearsLabel(null), null)
  assert.equal(m.yearsLabel(''), null)
  assert.deepEqual(m.experienceLines({ experienceRestaurantYears: 10, experienceTotalYears: 1 }), { restaurant: '10 yrs', total: '1 yr' })
  assert.equal(m.platformKind('ig'), 'instagram')
  assert.equal(m.platformKind('fb'), 'facebook')
  assert.equal(m.platformKind('Facebook'), 'facebook')
  assert.equal(m.platformKind('Google Ads'), 'google')
  assert.equal(m.platformKind('tiktok'), 'other')
  assert.equal(m.cleanPhone('p:+919000000001'), '+919000000001')
  assert.equal(m.whatsappLink('p:+91 90000 00001'), 'https://wa.me/919000000001')
  assert.equal(m.whatsappLink('123'), null)
  assert.equal(m.whatsappLink(null), null)
})

const admin = { id: 'a', role: 'admin' }
const manager = { id: 'm', role: 'manager' }
const staff = { id: 's1', role: 'staff' }

test('permissions: staff only work their own leads, admin and manager work any, converted leads cannot convert again', () => {
  assert.equal(m.canWorkLead(admin, { ownerId: null }), true)
  assert.equal(m.canWorkLead(manager, { ownerId: 's2' }), true)
  assert.equal(m.canWorkLead(staff, { ownerId: 's1' }), true)
  assert.equal(m.canWorkLead(staff, { ownerId: 's2' }), false)
  assert.equal(m.canWorkLead(staff, { ownerId: null }), false)
  assert.equal(m.canWorkLead(null, { ownerId: 's1' }), false)
  assert.equal(m.leadRefusal(staff, { ownerId: 's1' }), null)
  assert.match(m.leadRefusal(staff, { ownerId: null }), /no recruiter yet/)
  assert.match(m.leadRefusal(staff, { ownerId: 's2' }, 'Asha'), /assigned to Asha/)
  assert.equal(m.convertRefusal(staff, { ownerId: 's1', candidateId: null }), null)
  assert.match(m.convertRefusal(staff, { ownerId: 's1', candidateId: 'c1' }), /already a candidate/)
  assert.equal(m.isSupervisor(admin), true)
  assert.equal(m.isSupervisor(staff), false)
})

test('file checks: legacy xls, wrong type, empty, too large, and the formats that work', () => {
  const ok = (name, size = 1000) => m.leadFileError({ name, size })
  assert.equal(ok('Leads.CSV'), null)
  assert.equal(ok('leads.tsv'), null)
  assert.equal(ok('leads.txt'), null)
  assert.equal(ok('leads.xlsx'), null)
  assert.match(ok('old.xls'), /\.xls/)
  assert.match(ok('doc.pdf'), /CSV, TSV, TXT or Excel/)
  assert.match(ok('leads.csv', 0), /empty/)
  assert.match(ok('leads.csv', m.LEAD_MAX_BYTES + 1), /limit is 25\.0 MB/)
  assert.equal(m.formatBytes(2048), '2 KB')
  assert.equal(m.formatBytes(500), '500 B')
})

const columns = [
  { header: 'full_name', field: 'name', confidence: 'exact' },
  { header: 'phone_number', field: 'phone', confidence: 'alias' },
  { header: 'which_position_are_you_applying_for?', field: 'positionApplied', confidence: 'alias' },
  { header: 'my_custom_question', field: null, confidence: null },
  { header: 'contact', field: 'phone', confidence: 'alias' },
]

test('mapping preview model: confidence tiers, split, overrides and duplicate targets', () => {
  assert.equal(m.confidenceKind('exact'), 'high')
  assert.equal(m.confidenceKind('override'), 'high')
  assert.equal(m.confidenceKind('alias'), 'medium')
  assert.equal(m.confidenceKind(null), 'low')
  const { mapped, extra } = m.splitMappings(columns)
  assert.equal(mapped.length, 4)
  assert.deepEqual(extra.map((c) => c.header), ['my_custom_question'])
  // changing a column records only the change; setting it back removes the override
  let overrides = m.setMappingOverride({}, columns[3], 'city')
  assert.deepEqual(overrides, { my_custom_question: 'city' })
  overrides = m.setMappingOverride(overrides, columns[3], m.EXTRA_FIELD)
  assert.deepEqual(overrides, {})
  overrides = m.setMappingOverride({}, columns[0], m.EXTRA_FIELD)
  assert.deepEqual(overrides, { full_name: null })
  assert.equal(m.applyOverrides(columns, overrides)[0].field, null)
  assert.equal(m.applyOverrides(columns, overrides)[0].confidence, 'override')
  assert.deepEqual(m.duplicateTargets(columns, {}), ['phone'])
  assert.deepEqual(m.duplicateTargets(columns, { contact: null }), [])
  assert.equal(m.fieldLabel('positionApplied'), 'Position applied')
  assert.equal(m.fieldLabel('someNewField'), 'Some new field')
  assert.deepEqual(m.sampleValues([{ name: 'A' }, { name: 'A' }, { name: 'B' }, { name: 'C' }], 'name'), ['A', 'B'])
  assert.deepEqual(m.sampleValues([{ experienceRestaurantText: '10+ years' }], 'experienceRestaurant'), ['10+ years'])
  assert.deepEqual(m.sampleValues([{ name: 'A' }], null), [])
})

test('import request fields use the server names and send only what was changed', () => {
  assert.deepEqual(m.importFormFields({ dryRun: true }), { dry_run: 'true' })
  const fields = m.importFormFields({ dryRun: false, mapping: { x: null, y: 'city' }, assignTo: 'auto', label: ' Dubai ' })
  assert.equal(fields.dry_run, 'false')
  assert.deepEqual(JSON.parse(fields.mapping), { x: null, y: 'city' })
  assert.equal(fields.assign_to, 'auto')
  assert.equal(fields.campaign_label, 'Dubai')
  assert.equal(m.importFormFields({ dryRun: true, mapping: {} }).mapping, undefined)
})

test('import result wording and reasons', () => {
  assert.equal(m.resultSummary({ new: 28, duplicate: 1, invalid: 1 }), '28 new leads added, 1 duplicate ignored, 1 invalid row')
  assert.equal(m.resultSummary({ new: 1, duplicate: 0, invalid: 0 }), '1 new lead added, 0 duplicates ignored')
  assert.equal(m.duplicateReason({ reason: 'phone', scope: 'database' }), 'Same phone number as a lead already stored')
  assert.equal(m.duplicateReason({ reason: 'lead_id', scope: 'file', existingRow: 4 }), 'Same lead ID as row 4 in this file')
  assert.equal(m.invalidReason({ reason: 'no_contact' }), 'No usable phone number or email')
  assert.equal(m.invalidReason({ reason: 'bad_phone', message: 'Phone number is not valid' }), 'Phone number is not valid')
  const counts = m.countsOf({ rowsTotal: 30, rowsNew: 28, rowsDuplicate: 1, rowsInvalid: 1, rowsMatchedCandidates: 2, rowsUpdated: 0 })
  assert.deepEqual(counts, { total: 30, new: 28, duplicate: 1, invalid: 1, existingCandidate: 2, updated: 0 })
  assert.match(m.importErrorMessage(413, 'x'), /too large/)
  assert.equal(m.importErrorMessage(422, 'x', 'Save it as .xlsx'), 'Save it as .xlsx')
  assert.match(m.importErrorMessage(0, 'x'), /Cannot reach/)
})
