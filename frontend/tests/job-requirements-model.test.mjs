import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'

/* The pure rules of the Job requirements sheet (src/features/job-requirements/job-requirements-model.ts): rows, typed dates, summaries, deadlines, filters. */
const moduleUrl = (source) => {
  const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } })
  return `data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`
}
const read = (path) => readFile(new URL(path, import.meta.url), 'utf8')
const m = await import(moduleUrl(await read('../src/features/job-requirements/job-requirements-model.ts')))
const card = await import(moduleUrl(await read('../src/features/dashboard/job-requirements-card.ts')))

const item = (over = {}) => ({
  id: 'a', docDate: '2026-11-21', clientName: 'xyz company', country: 'Kuwait', details: {}, adUrl: null, adName: null, status: 'active', custom: {},
  createdAt: '2026-11-21T08:00:00Z', updatedAt: '2026-11-21T08:00:00Z', createdBy: { id: 'u1', name: 'Asha' }, updatedBy: { id: 'u2', name: 'Ravi' }, ...over,
})

test('S.No is the running number of the rows shown, 1..n, and restarts inside a tab', () => {
  const items = [item({ id: '1' }), item({ id: '2', status: 'closed' }), item({ id: '3' })]
  assert.deepEqual(m.buildRows(items, 'all').map((r) => r.serial), [1, 2, 3])
  assert.deepEqual(m.buildRows(items, 'active').map((r) => [r.serial, r.item.id]), [[1, '1'], [2, '3']])
  assert.deepEqual(m.buildRows(items, 'closed').map((r) => [r.serial, r.item.id]), [[1, '2']])
  assert.deepEqual(m.statusCounts(items), { all: 3, active: 2, closed: 1 })
})

test('a typed date is shaped as dd/mm/yyyy, accepted only when real, and stored as YYYY-MM-DD', () => {
  assert.equal(m.shapeDateText('21112026'), '21/11/2026')
  assert.equal(m.shapeDateText('2111'), '21/11')
  assert.equal(m.isoToText('2026-11-21'), '21/11/2026')
  assert.equal(m.isoToText(null), '')
  assert.deepEqual(m.readTypedDate('21/11/2026'), { ok: true, iso: '2026-11-21' })
  assert.deepEqual(m.readTypedDate('21-11-2026'), { ok: true, iso: '2026-11-21' })
  assert.deepEqual(m.readTypedDate(''), { ok: true, iso: null })
  assert.equal(m.readTypedDate('31/02/2026').ok, false)
  assert.equal(m.readTypedDate('21/11').ok, false)
  assert.equal(m.readTypedDate('21/11/1999').ok, false)
})

test('the details summary reads "Position \u00B7 No. \u00B7 Place \u00B7 Salary", leaves out what is empty and falls back to the remarks', () => {
  assert.equal(m.detailsSummary({ position: 'Welder', vacancies: '5', placeOfWork: 'Kuwait', basicSalary: '1200 KWD' }), 'Welder \u00B7 5 \u00B7 Kuwait \u00B7 1200 KWD')
  assert.equal(m.detailsSummary({ position: 'Welder', basicSalary: '900' }), 'Welder \u00B7 900')
  assert.equal(m.detailsSummary({ placeOfWork: ' Doha ' }), 'Doha')
  assert.equal(m.detailsSummary({ remarks: 'Need ten drivers\nwith licence' }), 'Need ten drivers with licence')
  assert.equal(m.detailsSummary({ remarks: 'x'.repeat(80) }).length, 36)
  assert.equal(m.detailsSummary({ food: 'Provided' }), 'Details added')
  assert.equal(m.detailsSummary({ deadline: '2026-12-01' }), 'Details added')
  assert.equal(m.detailsSummary({}), '')
  assert.equal(m.hasDetails({ position: '  ' }), false)
  assert.equal(m.hasDetails({ remarks: 'a' }), true)
})

test("the form is the owner's sheet: 16 numbered items in his order and labels, then the Deadline", () => {
  assert.deepEqual(m.DETAIL_INPUTS.map((i) => [i.no, i.key, i.label]), [
    [1, 'position', 'Name of the Position'], [2, 'vacancies', 'No. of Requirements'], [3, 'placeOfWork', 'Place of Work'], [4, 'contractPeriod', 'Period of Contract'],
    [5, 'qualification', 'Qualification'], [6, 'agePreference', 'Age Preference'], [7, 'additionalSkills', 'Additional Skills'], [8, 'basicSalary', 'Basic Salary'],
    [9, 'workingHours', 'Working Hours'], [10, 'overtime', 'Overtime'], [11, 'otCalculation', 'OT Calculation'], [12, 'food', 'Food'], [13, 'accommodation', 'Accommodation'],
    [14, 'insurance', 'Insurance'], [15, 'transport', 'Transport'], [16, 'remarks', 'Remarks / Special Conditions'], [17, 'deadline', 'Deadline'],
  ])
  assert.equal(m.DETAIL_INPUTS.find((i) => i.key === 'remarks').kind, 'area')
  assert.equal(m.DETAIL_INPUTS.find((i) => i.key === 'deadline').kind, 'date')
  assert.match(m.DETAIL_INPUTS.find((i) => i.key === 'deadline').hint, /due-date reminders/)
  assert.deepEqual([...m.DETAIL_KEYS], m.DETAIL_INPUTS.map((i) => i.key))
  for (const old of ['salary', 'benefits', 'ageLimit', 'experience', 'gender', 'education', 'interviewDate', 'notes', 'text']) assert.ok(!m.DETAIL_KEYS.includes(old), old)
})

test('the form sends only the keys that changed, trimmed', () => {
  const before = m.detailsFor({ details: { position: 'Welder', basicSalary: '1200' } })
  assert.deepEqual(m.detailsChanges(before, { ...before, basicSalary: ' 1300 ', remarks: 'urgent' }), { basicSalary: '1300', remarks: 'urgent' })
  assert.deepEqual(m.detailsChanges(before, { ...before, position: '' }), { position: '' })
  assert.deepEqual(m.detailsChanges(before, before), {})
  assert.equal(m.DETAIL_KEYS.length, 17)
  assert.equal(m.detailsFor({ details: { salary: 'old', position: 'x' } }).salary, undefined, 'a key the server no longer has is never sent back')
})

test('extra columns are checked by type and shown readably', () => {
  assert.deepEqual(m.readFieldInput({ type: 'number' }, '12,5'), { ok: true, value: '12.5' })
  assert.equal(m.readFieldInput({ type: 'number' }, 'abc').ok, false)
  assert.deepEqual(m.readFieldInput({ type: 'date' }, '21/11/2026'), { ok: true, value: '2026-11-21' })
  assert.equal(m.readFieldInput({ type: 'date' }, '2111').ok, false)
  assert.deepEqual(m.readFieldInput({ type: 'yes_no' }, 'yes'), { ok: true, value: 'yes' })
  assert.equal(m.readFieldInput({ type: 'yes_no' }, 'maybe').ok, false)
  assert.deepEqual(m.readFieldInput({ type: 'text' }, '  hi '), { ok: true, value: 'hi' })
  assert.equal(m.fieldDisplay({ type: 'date' }, '2026-11-21'), '21/11/2026')
  assert.equal(m.fieldDisplay({ type: 'yes_no' }, 'no'), 'No')
})

test('a column name is required, short and unique', () => {
  const fields = [{ id: 'f1', label: 'Visa type' }]
  assert.match(m.fieldLabelError('', fields), /name/)
  assert.match(m.fieldLabelError('visa TYPE', fields), /already/)
  assert.equal(m.fieldLabelError('visa TYPE', fields, 'f1'), '')
  assert.match(m.fieldLabelError('x'.repeat(61), fields), /60/)
  assert.equal(m.fieldLabelError('Medical', fields), '')
})

test('the Ad file: an image or a PDF up to 10 MB', () => {
  assert.equal(m.adFileError({ name: 'a.png', size: 1000, type: 'image/png' }), '')
  assert.equal(m.adFileError({ name: 'a.pdf', size: 1000, type: 'application/pdf' }), '')
  assert.match(m.adFileError({ name: 'a.docx', size: 1000, type: 'application/msword' }), /photo|PDF/)
  assert.match(m.adFileError({ name: 'a.png', size: 11 * 1024 * 1024, type: 'image/png' }), /10 MB/)
  assert.equal(m.adKind({ adUrl: null, adName: null }), 'none')
  assert.equal(m.adKind({ adUrl: '/api/x/ad', adName: 'flyer.PDF' }), 'pdf')
  assert.equal(m.adKind({ adUrl: '/api/x/ad', adName: 'flyer.jpg' }), 'image')
  assert.match(m.adSrc({ adUrl: '/api/x/ad', updatedAt: '2026-11-21T10:00:00Z' }), /^\/api\/x\/ad\?v=/)
})

test('an optimistic change merges like the server: details by key, custom by field, reopening clears the closing', () => {
  const closed = item({ status: 'closed', closedReason: 'filled', closedAt: '2026-11-20T10:00:00Z', details: { position: 'Welder' }, custom: { f1: 'a' } })
  const edited = m.applyPatch(closed, { details: { basicSalary: '9' }, custom: { f2: 'b' }, clientName: 'abc' })
  assert.deepEqual(edited.details, { position: 'Welder', basicSalary: '9' })
  assert.deepEqual(edited.custom, { f1: 'a', f2: 'b' })
  assert.equal(edited.clientName, 'abc')
  const reopened = m.applyPatch(closed, { status: 'active' })
  assert.equal(reopened.closedReason, '')
  assert.equal(reopened.closedAt, null)
  assert.equal(m.applyPatch(item(), { status: 'closed', closedReason: 'expired' }).closedReason, 'expired')
  assert.equal(m.applyPatch(item(), { ownerId: 'u9' }).ownerId, 'u9')
  assert.deepEqual(m.mergePatches({ details: { a: '1' }, clientName: 'x' }, { details: { b: '2' }, clientName: 'y' }), { details: { a: '1', b: '2' }, clientName: 'y' })
  assert.equal(m.flipStatus('active'), 'closed')
})

test('after a save the server only adds who and when; what was typed since stays', () => {
  const local = item({ clientName: 'typed later' })
  const merged = m.mergeSaved(local, item({ clientName: 'older', updatedAt: '2026-11-22T00:00:00Z', closedAt: '2026-11-22T00:00:00Z' }))
  assert.equal(merged.clientName, 'typed later')
  assert.equal(merged.updatedAt, '2026-11-22T00:00:00Z')
  assert.equal(merged.closedAt, '2026-11-22T00:00:00Z')
})

test('the deadline chip: red when overdue, amber within 7 days, plain beyond, none when closed or absent', () => {
  const today = '2026-11-21'
  const withDeadline = (deadline, status = 'active') => ({ status, details: { deadline } })
  assert.deepEqual(m.deadlineChip(withDeadline('2026-11-18'), today), { tone: 'red', text: 'Overdue 3 days', days: -3 })
  assert.equal(m.deadlineChip(withDeadline('2026-11-20'), today).text, 'Overdue 1 day')
  assert.deepEqual(m.deadlineChip(withDeadline('2026-11-21'), today), { tone: 'amber', text: 'Due today', days: 0 })
  assert.deepEqual(m.deadlineChip(withDeadline('2026-11-22'), today), { tone: 'amber', text: 'Due in 1 day', days: 1 })
  assert.equal(m.deadlineChip(withDeadline('2026-11-28'), today).tone, 'amber')
  assert.equal(m.deadlineChip(withDeadline('2026-11-28'), today).text, 'Due in 7 days')
  assert.equal(m.deadlineChip(withDeadline('2026-11-29'), today).tone, 'plain')
  assert.equal(m.deadlineChip(withDeadline('2026-11-18', 'closed'), today), null)
  assert.equal(m.deadlineChip(withDeadline(''), today), null)
  assert.equal(m.deadlineChip({ status: 'active', details: {} }, today), null)
  assert.equal(m.daysUntil('2027-01-01', '2026-12-31'), 1, 'across a year end')
  assert.equal(m.isDueSoon(withDeadline('2026-11-25'), today), true)
  assert.equal(m.isDueSoon(withDeadline('2026-11-20'), today), false)
  assert.equal(m.isOverdue(withDeadline('2026-11-20'), today), true)
  assert.equal(m.isOverdue(withDeadline('2026-11-21'), today), false)
})

test('the card views Due in 7 days / Overdue only show active rows with such a deadline', () => {
  const today = '2026-11-21'
  const items = [
    item({ id: 'soon', details: { deadline: '2026-11-23' } }), item({ id: 'late', details: { deadline: '2026-11-01' } }),
    item({ id: 'far', details: { deadline: '2027-01-01' } }), item({ id: 'shut', status: 'closed', details: { deadline: '2026-11-01' } }), item({ id: 'none' }),
  ]
  assert.deepEqual(m.buildRows(items, 'dueSoon', today).map((r) => r.item.id), ['soon'])
  assert.deepEqual(m.buildRows(items, 'overdue', today).map((r) => r.item.id), ['late'])
  const summary = m.localSummary(items, today)
  assert.deepEqual([summary.active, summary.closed, summary.dueSoon, summary.overdue], [4, 1, 1, 1])
  assert.deepEqual(m.summaryCounts(summary), { all: 5, active: 4, closed: 1 })
})

test('who handles a requirement: active recruiters and the MD only, the current owner kept', () => {
  const users = [
    { id: 's1', name: 'Zed', role: 'staff', active: true }, { id: 'm1', name: 'Mia', role: 'manager', active: true }, { id: 'a1', name: 'Boss', role: 'admin', active: true },
    { id: 's2', name: 'Off', role: 'staff', active: false }, { id: 's3', name: 'Gone', role: 'staff', active: true, deleted: true },
  ]
  assert.deepEqual(m.ownerChoices(users).map((u) => u.id), ['m1', 's1'])
  assert.deepEqual(m.ownerChoices(users, { ownerId: 's2', ownerName: 'Off' }).map((u) => u.id), ['m1', 's2', 's1'])
  assert.deepEqual(m.ownerChoices(users, { ownerId: 'm1', ownerName: 'Mia' }).map((u) => u.id), ['m1', 's1'])
})

test('closing: reasons, and the tooltip names the day and the reason', () => {
  assert.deepEqual(m.CLOSED_REASONS.map((r) => r.value), ['filled', 'client_cancelled', 'expired', 'other'])
  assert.equal(m.closedReasonLabel('client_cancelled'), 'Client cancelled')
  assert.equal(m.closedReasonLabel(''), '')
  assert.equal(m.closedTitle(item()), '')
  assert.equal(m.closedTitle(item({ status: 'closed', closedAt: '2026-11-20T10:00:00Z', closedReason: 'filled' })), 'Closed on 20 Nov 2026 (Filled)')
  assert.equal(m.closedTitle(item({ status: 'closed' })), 'Closed')
})

test('the filters become the same query for the list and the export', () => {
  const filters = { ...m.NO_FILTERS, q: '  welder  kuwait ', owner: 'u1', from: '2026-11-01' }
  assert.equal(m.filterCount(filters), 2)
  assert.equal(m.listQuery(filters), 'status=all&limit=200&offset=0&q=welder+kuwait&owner=u1&from=2026-11-01')
  assert.equal(m.exportQuery(filters, 'xlsx'), 'q=welder+kuwait&owner=u1&from=2026-11-01&format=xlsx')
  assert.equal(m.listQuery(m.NO_FILTERS, 200, 200), 'status=all&limit=200&offset=200')
  assert.equal(m.listQuery({ ...m.NO_FILTERS, due: 'soon' }), 'status=all&limit=200&offset=0&due=soon')
  assert.equal(m.filterCount({ ...m.NO_FILTERS, due: 'overdue' }), 0, 'a card view is not a filter chip')
  assert.equal(m.daysBefore('2026-11-21', 6), '2026-11-15')
  assert.equal(m.daysBefore('2026-03-02', 6), '2026-02-24')
})

test('a duplicate keeps the client, country, details and extra columns, not the ad, date or status', () => {
  const copy = m.duplicateBody(item({ details: { position: ' Welder ', basicSalary: '', remarks: 'abc' }, custom: { f1: 'x' }, adUrl: '/ad', status: 'closed' }))
  assert.deepEqual(copy, { clientName: 'xyz company', country: 'Kuwait', details: { position: 'Welder', remarks: 'abc' }, custom: { f1: 'x' } })
})

test('the row tooltip says who added it and who last updated it', () => {
  assert.equal(m.whoWhen(item()), 'Added by Asha on 21 Nov 2026 · last updated by Ravi on 21 Nov 2026')
  assert.equal(m.whoWhen(item({ updatedBy: null })), 'Added by Asha on 21 Nov 2026')
})

test('the dashboard card: active count, due soon and overdue', () => {
  assert.deepEqual(card.jobRequirementsCard(null), { value: null, note: 'loading', danger: false })
  assert.deepEqual(card.jobRequirementsCard({ total: 12, summary: { active: 12, dueSoon: 3, overdue: 0 } }), { value: 12, note: '3 due soon', danger: false })
  assert.deepEqual(card.jobRequirementsCard({ total: 12, summary: { active: 12, dueSoon: 0, overdue: 2 } }), { value: 12, note: 'none due soon, 2 overdue', danger: true })
  assert.deepEqual(card.jobRequirementsCard({ total: 5 }), { value: 5, note: 'none due soon', danger: false })
})

test('the column widths: no scroll without extra columns from 1053px, extra columns scroll, text-length decides the split', () => {
  const plain = m.columnWidths(1053, 0, { client: 20, details: 30 })
  assert.equal(plain.total, 1053, 'exactly the panel: no scroll bar at 1366')
  assert.equal(plain.client + plain.details + plain.country + plain.owner + (m.FIXED_TOTAL - m.FIXED_WIDTHS.country - m.FIXED_WIDTHS.owner), 1053)
  assert.ok(plain.client >= 170 && plain.details >= 160)
  const wide = m.columnWidths(1580, 0, { client: 20, details: 30 })
  assert.equal(wide.total, 1580, 'fills a wide panel too')
  assert.ok(m.columnWidths(1053, 0, { client: 60, details: 10 }).client > m.columnWidths(1053, 0, { client: 10, details: 60 }).client, 'a long client name gets more room')
  const withCustom = m.columnWidths(1053, 4, { client: 20, details: 30 })
  assert.equal(withCustom.total, m.FIXED_TOTAL + m.FLEX_MIN + 4 * m.CUSTOM_WIDTH)
  assert.ok(withCustom.total > 1053, 'with extra columns the box scrolls')
  assert.ok(m.columnWidths(0, 0, { client: 0, details: 0 }).total >= m.FIXED_TOTAL + m.FLEX_MIN, 'before the width is known the minimum holds')
  assert.ok(m.FIXED_TOTAL + m.FLEX_MIN <= 1051, 'the fixed columns plus the minimum fit a 1053px panel')
})

test('Country and Assigned to grow for long texts, out of the spare room only, and never past their limits', () => {
  const short = m.columnWidths(1500, 0, { client: 20, details: 30, country: 6, owner: 10 })
  const long = m.columnWidths(1500, 0, { client: 20, details: 30, country: 20, owner: 30 })
  assert.equal(short.country, m.FIXED_WIDTHS.country)
  assert.ok(long.country > short.country && long.country <= 134)
  assert.ok(long.owner > short.owner && long.owner <= 176)
  assert.equal(long.total, 1500, 'still exactly the box')
  const tight = m.columnWidths(1053, 0, { client: 20, details: 30, country: 20, owner: 30 })
  assert.equal(tight.total, 1053, 'no scroll bar at 1366 even for long texts')
  assert.ok(tight.client >= 170 && tight.details >= 160, 'Client and Details keep their minimum')
  const crowded = m.columnWidths(1053, 3, { client: 20, details: 30, country: 20, owner: 30 })
  assert.equal(crowded.country, m.FIXED_WIDTHS.country, 'with extra columns nothing grows: the box scrolls instead')
  assert.equal(crowded.owner, m.FIXED_WIDTHS.owner)
})
