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
  assert.deepEqual(m.cardFilters('not_contacted', NOW), { status: 'new', from: '', to: '', recent: '' })
  assert.equal(m.activeCard({ ...base, ...m.cardFilters('not_contacted', NOW) }, NOW), 'not_contacted')
  assert.equal(m.activeCard({ ...base, ...m.cardFilters('new_today', NOW) }, NOW), 'new_today')
  assert.deepEqual(m.cardFilters('new_today'), { status: '', from: '', to: '', recent: '24h' }, 'a rolling 24 hours, not a calendar day')
  assert.equal(m.buildLeadQuery({ ...base, ...m.cardFilters('new_today') }, sort).includes('uploaded_within_hours=24'), true)
  assert.equal(m.activeCard({ ...base, ...m.cardFilters('new_today'), ...m.cardFilters('converted') }, NOW), 'converted', 'picking another card clears it')
  assert.equal(m.activeCard({ ...base, ...m.cardFilters('new_today'), status: 'new' }, NOW), '')
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

test('experience answers preserve Yes/No, ranges and missing values from mixed lead sheets', () => {
  for (const value of ['Yes', 'No', 'Unknown', 'N/A', ' ', '-2', '2–5 years']) assert.equal(m.yearsLabel(value), null)
  assert.equal(m.yearsLabel('2 years'), '2 yrs')
  assert.equal(m.experienceLabel(null, ' yes '), 'Yes')
  assert.equal(m.experienceLabel(0, 'NO'), 'No', 'a legacy parsed zero must not replace the actual No answer')
  assert.equal(m.experienceLabel(0, 'N/A'), 'N/A')
  assert.equal(m.experienceLabel(5, '5+ years'), '5+ years')
  assert.equal(m.experienceLabel(2, '2–5 years'), '2–5 years')
  assert.equal(m.experienceLabel(0.5, '6 months'), '6 months')
  assert.equal(m.experienceLabel(10, '10'), '10 yrs')
  assert.equal(m.experienceLabel(null, '0'), '0 yrs')
  assert.equal(m.experienceLabel(null, ''), null)
  assert.deepEqual(m.experienceLines({ experienceTotalYears: null, experienceTotalText: 'Yes' }), { restaurant: null, total: 'Yes' })
  assert.deepEqual(m.experienceLines({ experienceRestaurantYears: 0, experienceRestaurantText: 'No', experienceTotalYears: 8 }), { restaurant: 'No', total: '8 yrs' })
})

test('experience question summaries follow the source threshold and retain qualification context', () => {
  assert.equal(m.experienceQuestionSummary('Do you have 5+ years of experience in reputed companies with an MBA or relevant qualification?'), '5+ years + qualification')
  assert.equal(m.experienceQuestionSummary('Do you have 10 years of work experience?'), '10 years')
  assert.equal(m.experienceQuestionSummary('do_you_have_3+_years_of_experience?'), '3+ years')
  assert.equal(m.experienceQuestionSummary('Do you have at least 3 years of experience?'), 'at least 3 years')
  assert.equal(m.experienceQuestionSummary('Do you have 2–5 years of experience?'), '2–5 years')
  assert.equal(m.experienceQuestionSummary('Do you have less than 2 years of experience?'), 'less than 2 years')
  assert.equal(m.experienceQuestionSummary('Do you have relevant experience and a degree?'), 'Experience + qualification')
  assert.equal(m.experienceQuestionSummary(null), 'Experience question')
})

test('lead date uses source creation when known and labels an import fallback honestly', () => {
  const source = '2026-11-04T12:00:00Z'
  const imported = '2026-11-12T11:13:15Z'
  assert.deepEqual(m.leadDate({ createdTime: source, createdAt: imported }), { value: source, label: 'Created' })
  assert.deepEqual(m.leadDate({ createdTime: null, createdAt: imported }), { value: imported, label: 'Imported' })
  assert.deepEqual(m.leadDate({ createdTime: 'bad date', createdAt: imported }), { value: imported, label: 'Imported' })
  assert.equal(m.leadDate({}), null)
  assert.equal(m.leadDate({ createdTime: 'bad date' }), null)
})

test('lead age is calculated at the birthday without inventing missing ages', () => {
  const today = new Date(2026, 10, 12)
  assert.equal(m.leadAge('1990-11-12', today), 36)
  assert.equal(m.leadAge('1990-11-13', today), 35)
  assert.equal(m.leadAge('1990-11-11', today), 36)
  for (const dob of [null, '', 'unknown', '2026-02-30', '2027-01-01']) assert.equal(m.leadAge(dob, today), null)
})

test('India and abroad experience stay distinct from restaurant and total answers', () => {
  const lead = { id: '1', name: 'Test', status: 'new', experienceRestaurantYears: 8, experienceTotalYears: 10 }
  assert.deepEqual(m.experienceAnswer(lead, 'india'), { value: null, question: null })
  assert.deepEqual(m.experienceAnswer(lead, 'abroad'), { value: null, question: null })
  assert.deepEqual(m.experienceAnswer({ ...lead, experienceIndiaYears: 3, experienceIndiaText: '3+' }, 'india'), { value: '3+', question: null })
  assert.deepEqual(m.experienceAnswer({ ...lead, experienceAbroadYears: 0, experienceAbroadText: 'No', experienceAbroadQuestion: 'Have you worked abroad?' }, 'abroad'), { value: 'No', question: 'Have you worked abroad?' })
})

const admin = { id: 'a', role: 'admin' }
const manager = { id: 'm', role: 'manager' }
const staff = { id: 's1', role: 'staff' }

test('permissions: managers and staff only work their own leads, admin works any, converted leads cannot convert again', () => {
  assert.equal(m.canWorkLead(admin, { ownerId: null }), true)
  assert.equal(m.canWorkLead(manager, { ownerId: 's2' }), false)
  assert.equal(m.canWorkLead(manager, { ownerId: 'm' }), true)
  assert.equal(m.canWorkLead(manager, { ownerId: null }), false)
  assert.match(m.leadRefusal(manager, { ownerId: 's2' }, 'Asha'), /assigned to Asha/)
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
  assert.equal(m.fieldLabel('positionApplied'), 'Job applied')
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

test('dates: presets are agency (IST) calendar days, whatever the browser zone is', () => {
  // 20:00 UTC on 5 Nov is already 01:30 IST on 6 Nov; 17:00 UTC on 5 Nov is still 5 Nov 22:30 IST
  const lateUtc = new Date('2026-11-05T20:00:00Z')
  assert.equal(m.agencyDay(lateUtc), '2026-11-06')
  assert.equal(m.todayKey(lateUtc), '2026-11-06')
  assert.equal(m.agencyDay(new Date('2026-11-05T17:00:00Z')), '2026-11-05')
  assert.equal(m.agencyDay(new Date('2026-11-05T18:29:59Z')), '2026-11-05')
  assert.equal(m.agencyDay(new Date('2026-11-05T18:30:00Z')), '2026-11-06', 'IST midnight')
  assert.deepEqual(m.presetRange('today', lateUtc), { from: '2026-11-06', to: '2026-11-06' })
  assert.deepEqual(m.presetRange('yesterday', lateUtc), { from: '2026-11-05', to: '2026-11-05' })
  assert.deepEqual(m.presetRange('last7', lateUtc), { from: '2026-10-31', to: '2026-11-06' }, 'today and the six days before')
  assert.deepEqual(m.presetRange('yesterday', new Date('2026-03-01T10:00:00Z')), { from: '2026-02-28', to: '2026-02-28' }, 'month boundaries')
  assert.deepEqual(m.presetRange('last7', new Date('2027-01-03T10:00:00Z')), { from: '2026-12-28', to: '2027-01-03' }, 'year boundaries')
  assert.deepEqual(m.DATE_PRESETS.map((p) => p.label), ['Today', 'Yesterday', 'Last 7 days'])
  assert.equal(m.activePreset({ from: '2026-11-06', to: '2026-11-06' }, lateUtc), 'today')
  assert.equal(m.activePreset(m.presetRange('last7', lateUtc), lateUtc), 'last7')
  assert.equal(m.activePreset({ from: '2026-11-06', to: '' }, lateUtc), '')
  assert.equal(m.activePreset({ from: '', to: '' }, lateUtc), '')
})

test('dates: Uploaded is the default, Created is chosen explicitly, and both are sent only with a date', () => {
  assert.equal(m.EMPTY_FILTERS.dateField, 'uploaded')
  assert.deepEqual(m.DATE_FIELDS.map((f) => f.label), ['Uploaded', 'Created'])
  const q = (patch) => new URLSearchParams(m.buildLeadQuery({ ...base, ...patch }, sort))
  assert.equal(q({}).has('date_field'), false, 'no dates, no date_field')
  assert.equal(q({ dateField: 'created' }).has('date_field'), false)
  assert.equal(q({ from: '2026-11-06', to: '2026-11-06' }).get('date_field'), 'uploaded')
  assert.equal(q({ from: '2026-11-06', dateField: 'created' }).get('date_field'), 'created')
  assert.equal(q({ to: '2026-11-06', dateField: 'created' }).get('created_to'), '2026-11-06')
  assert.equal(m.activeLeadFilterCount({ ...base, dateField: 'created' }), 0, 'choosing the date alone filters nothing')
  assert.equal(m.activeLeadFilterCount({ ...base, dateField: 'created', from: '2026-11-06' }), 1)
  assert.equal(m.activeLeadFilterCount({ ...base, ...m.cardFilters('new_today') }), 1)
  assert.equal(m.activeCard({ ...base, from: '2026-11-06', to: '2026-11-06' }, NOW), '', 'a date range is a filter, not the card')
})

test('dates: Uploaded shows the agency date and time, Created keeps the lead own date', () => {
  assert.equal(m.uploadedLabel('2026-11-14T04:42:00Z'), '14 Nov 2026, 10:12 IST')
  assert.equal(m.uploadedLabel('2026-11-14T19:00:00Z'), '15 Nov 2026, 00:30 IST', 'agency day, not UTC')
  assert.deepEqual(m.uploadedParts('2026-11-14T04:42:00Z'), { day: '14 Nov', time: '10:12' })
  assert.equal(m.uploadedLabel(null), '')
  assert.equal(m.uploadedLabel('nonsense'), '')
  assert.equal(m.uploadedParts(''), null)
  assert.equal(m.NEW_CARD_LABEL, 'Uploaded in 24 hrs')
  assert.equal(m.NEW_CARD_DETAIL, 'Uploaded in the last 24 hours')
  assert.ok(m.LEAD_SORTS.some(([value]) => value === 'uploaded:desc'))
})

test('screen wiring: the card, the Dates panel presets and the remembered date choice are in the screen source', async () => {
  const { readFile } = await import('node:fs/promises')
  const read = (path) => readFile(new URL(path, import.meta.url), 'utf8')
  const screen = await read('../src/screens/leads/leads.tsx')
  const filters = await read('../src/features/leads/components/lead-filters.tsx')
  const css = await read('../src/styles/leads.css')
  assert.match(screen, /label: NEW_CARD_LABEL, value: fmt\(summary\?\.newToday\)[^\n]*onSelect: \(\) => pickCard\('new_today'\)/, 'the card selects exactly its own filter')
  assert.match(screen, /sessionStorage/, 'the Uploaded / Created choice is kept while the sheet is open')
  assert.match(screen, /dateField: current\.dateField/, 'Clear and the other views keep the choice')
  assert.match(filters, /DATE_PRESETS\.map/)
  assert.match(filters, /DATE_FIELDS\.map/)
  assert.match(filters, /aria-pressed/)
  assert.match(filters, /\$\{dateLabel\} from/, 'the inputs are named after the chosen date')
  assert.match(css, /\.ld-datebar \{[^}]*flex-wrap: wrap/, 'the presets wrap on a 360px screen')
})
