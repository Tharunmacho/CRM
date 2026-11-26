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
  assert.equal(m.hasDetails({ details: { position: '  ' } }), false)
  assert.equal(m.hasDetails({ details: { remarks: 'a' } }), true)
})

/* ---- the client's "Job Requirement Form": positions, their requirements, general details ---- */

const paratha = { id: 'p1', title: 'Paratha Master', vacancies: 'To be confirmed', salary: 'To be confirmed', requirements: [{ id: 'r1', field: 'Practical Skill / Video', details: 'Working video required.' }] }
const captain = { id: 'p2', title: 'Captain', vacancies: '2', salary: '1500 AED', requirements: [
  { id: 'r2', field: 'Qualification', details: 'Hotel management qualification.' }, { id: 'r3', field: 'Maximum Age', details: 'Up to 30 years.' }, { id: 'r4', field: 'Experience', details: 'Minimum 5 years.' },
] }
const saved = (over = {}) => item({ positions: [paratha, captain], general: [{ id: 'g1', label: 'Weekly off', details: '2 days per month', remarks: 'As provided by client' }, { id: 'g2', label: 'Air Ticket', details: 'Return', remarks: '' }], details: { deadline: '2026-12-01', remarks: 'Pending confirmation.' }, ...over })

test('the suggestions and defaults are the client\'s: eight particulars in his order, requirement fields to pick from', () => {
  assert.deepEqual([...m.GENERAL_DEFAULTS], ['Working Hours', 'Contract Period', 'Place of Work', 'Restaurant Type', 'Weekly Off', 'Food', 'Accommodation', 'Visa Type'])
  for (const name of ['Qualification', 'Maximum Age', 'Minimum Age', 'Experience', 'Skills / Trade', 'Practical Skill / Video', 'Language', 'Gender', 'Nationality', 'Driving Licence', 'Certificate', 'Other']) assert.ok(m.REQUIREMENT_FIELD_SUGGESTIONS.includes(name), name)
  for (const name of ['Overtime', 'OT Calculation', 'Insurance', 'Transport', 'Air Ticket', 'Medical']) assert.ok(m.GENERAL_SUGGESTIONS.includes(name), name)
  assert.ok(m.QUICK_REQUIREMENT_FIELDS.every((name) => m.REQUIREMENT_FIELD_SUGGESTIONS.includes(name)))
  assert.notEqual(m.makeId(), m.makeId())
  assert.deepEqual([...m.DETAIL_KEYS].filter((key) => key === 'deadline' || key === 'remarks'), ['remarks', 'deadline'], 'the legacy keys are still the server\'s')
  assert.equal(m.DETAIL_KEYS.length, 17)
  for (const old of ['salary', 'benefits', 'ageLimit', 'experience', 'gender', 'education', 'interviewDate', 'notes', 'text']) assert.ok(!m.DETAIL_KEYS.includes(old), old)
})

test('a new requirement opens with one blank position and the eight default particulars', () => {
  const form = m.formFor(item())
  assert.equal(form.positions.length, 1)
  assert.deepEqual(form.positions[0].requirements, [])
  assert.equal(form.positions[0].title, '')
  assert.deepEqual(form.general.map((row) => row.label), [...m.GENERAL_DEFAULTS])
  assert.equal(form.deadline, '')
  assert.deepEqual(m.buildPatch(form, form), {}, 'nothing typed, nothing sent')
  assert.deepEqual(m.buildPatch(form, m.formFor(item())), {}, 'a blank form read twice is still unchanged (new ids are not changes)')
})

test('saved positions and general rows open as saved; the general rows are merged by name over the defaults', () => {
  const form = m.formFor(saved())
  assert.deepEqual(form.positions.map((p) => p.title), ['Paratha Master', 'Captain'])
  assert.deepEqual(form.positions[1].requirements.map((r) => r.field), ['Qualification', 'Maximum Age', 'Experience'])
  assert.deepEqual(form.general.map((row) => row.label), ['Working Hours', 'Contract Period', 'Place of Work', 'Restaurant Type', 'Weekly off', 'Food', 'Accommodation', 'Visa Type', 'Air Ticket'])
  assert.equal(form.general[4].details, '2 days per month', 'matched "Weekly off" to the default "Weekly Off" ignoring case, kept in its place')
  assert.equal(form.general[8].details, 'Return', 'a particular that is not a default comes after them')
  assert.equal(form.deadline, '2026-12-01')
  assert.equal(form.remarks, 'Pending confirmation.')
  assert.deepEqual(m.buildPatch(form, m.formFor(saved())), {}, 'opened and not touched: nothing to send')
})

test('an old row with only the flat keys opens pre-filled: position 1 with its requirements, and general rows', () => {
  const old = item({ details: {
    position: 'Welder', vacancies: '5', basicSalary: '1200 KWD', qualification: 'ITI', agePreference: '25-35', additionalSkills: 'Arc welding', placeOfWork: 'Kuwait',
    contractPeriod: '2 years', workingHours: '8', overtime: 'Paid', otCalculation: '1.25x', food: 'Provided', accommodation: 'Provided', insurance: 'Yes', transport: 'Yes', remarks: 'r', deadline: '2026-12-05',
  } })
  const form = m.formFor(old)
  assert.deepEqual(form.positions.map((p) => [p.title, p.vacancies, p.salary]), [['Welder', '5', '1200 KWD']])
  assert.deepEqual(form.positions[0].requirements.map((r) => [r.field, r.details]), [['Qualification', 'ITI'], ['Age Preference', '25-35'], ['Skills', 'Arc welding']])
  const byLabel = Object.fromEntries(form.general.map((row) => [row.label, row.details]))
  assert.equal(byLabel['Working Hours'], '8')
  assert.equal(byLabel['Contract Period'], '2 years')
  assert.equal(byLabel['Place of Work'], 'Kuwait')
  assert.equal(byLabel['Restaurant Type'], '')
  assert.equal(byLabel['OT Calculation'], '1.25x')
  assert.deepEqual(form.general.map((row) => row.label).slice(0, 8), [...m.GENERAL_DEFAULTS])
  assert.deepEqual(form.general.slice(8).map((row) => row.label), ['Overtime', 'OT Calculation', 'Insurance', 'Transport'])
  assert.deepEqual(form.fromLegacy, { positions: true, general: true })
  assert.deepEqual(m.buildPatch(form, m.formFor(old)), {}, 'opening an old row and closing it changes nothing')
  assert.equal(m.formFor(item({ details: { food: 'x' } })).positions[0].title, '', 'no position keys: one blank position')
})

test('the patch holds only what changed, trimmed, blank rows removed, and sends whole lists', () => {
  const start = m.formFor(saved())
  const typed = m.formFor(saved())
  typed.positions[1].requirements[2].details = '  Minimum 6 years.  '
  typed.positions.push({ ...m.blankPosition(), title: '  Waiter ', vacancies: ' 4', requirements: [m.blankRequirement('Qualification'), { ...m.blankRequirement(''), field: '  ', details: ' ' }] })
  typed.positions.push(m.blankPosition())
  const patch = m.buildPatch(start, typed)
  assert.deepEqual(Object.keys(patch), ['positions'])
  assert.equal(patch.positions.length, 3, 'the blank position and the blank requirement are removed')
  assert.deepEqual(patch.positions[2], { title: 'Waiter', vacancies: '4', salary: '', requirements: [{ field: 'Qualification', details: '' }] }, 'new rows carry no client id')
  assert.equal(patch.positions[1].requirements[2].details, 'Minimum 6 years.')
  assert.equal(patch.positions[1].id, 'p2', 'saved rows keep their id')
  assert.equal(patch.positions[1].requirements[0].id, 'r2')
  assert.equal(patch.general, undefined, 'the general list was not touched')
})

test('general rows, the deadline and the confirmation are sent when they change, and cleared when emptied', () => {
  const start = m.formFor(saved())
  const typed = m.formFor(saved())
  typed.general[0].details = ' 12 hours '
  typed.general = typed.general.filter((row) => row.label !== 'Air Ticket')
  typed.deadline = ''
  typed.remarks = 'Vacancies and salary details are pending confirmation.'
  const patch = m.buildPatch(start, typed)
  assert.deepEqual(patch.general.map((row) => [row.label, row.details]), [['Working Hours', '12 hours'], ['Weekly off', '2 days per month']], 'only rows with something typed, whole list, in order')
  assert.equal(patch.general[1].id, 'g1')
  assert.deepEqual(patch.details, { deadline: '', remarks: 'Vacancies and salary details are pending confirmation.' })
  assert.equal(patch.positions, undefined)
  const emptied = m.formFor(saved())
  emptied.positions = [m.blankPosition()]
  assert.deepEqual(m.buildPatch(start, emptied).positions, [], 'removing every position sends the empty list (it clears)')
  assert.equal(m.isEmptyPatch({}), true)
  assert.equal(m.isEmptyPatch(patch), false)
})

test('emptying a list that was read from the old flat keys also clears those keys so it does not come back', () => {
  const old = item({ details: { position: 'Welder', basicSalary: '9', food: 'Provided', deadline: '2026-12-01' } })
  const start = m.formFor(old)
  const typed = m.formFor(old)
  typed.positions = [m.blankPosition()]
  typed.general.forEach((row) => { row.details = '' })
  const patch = m.buildPatch(start, typed)
  assert.deepEqual(patch.positions, [])
  assert.deepEqual(patch.general, [])
  for (const key of ['position', 'vacancies', 'basicSalary', 'qualification', 'agePreference', 'additionalSkills', 'workingHours', 'contractPeriod', 'placeOfWork', 'overtime', 'otCalculation', 'food', 'accommodation', 'insurance', 'transport']) assert.equal(patch.details[key], '', key)
  assert.equal(patch.details.deadline, undefined, 'the deadline is not touched')
  const edited = m.formFor(old)
  edited.positions[0].title = 'Fitter'
  assert.equal(m.buildPatch(start, edited).details, undefined, 'a non-empty list leaves the old keys alone')
})

test('a row with something typed but no name is a problem on that row, never silently dropped', () => {
  const form = m.formFor(item())
  form.positions[0].vacancies = '3'
  const noField = m.blankRequirement('')
  noField.details = 'Up to 30'
  form.positions.push({ ...m.blankPosition(), title: 'Captain', requirements: [noField] })
  form.general[0].label = ''
  form.general[0].details = '12 hours'
  const problems = m.formProblems(form)
  assert.deepEqual(problems.map((p) => p.kind), ['position', 'requirement', 'general'])
  assert.equal(problems[0].id, form.positions[0].id)
  assert.equal(problems[1].id, noField.id)
  assert.deepEqual(m.formProblems(m.formFor(saved())), [])
  assert.deepEqual(m.formProblems(m.formFor(item())), [], 'a blank form has no problem')
  const blankRows = m.formFor(item())
  blankRows.positions.push(m.blankPosition())
  assert.deepEqual(m.formProblems(blankRows), [], 'untouched blank rows are fine')
})

test('"To be confirmed" fills only the empty vacancies and salary boxes of positions in use', () => {
  const filled = m.fillToBeConfirmed([{ ...m.blankPosition(), title: 'Waiter', vacancies: '4' }, m.blankPosition()])
  assert.deepEqual([filled[0].vacancies, filled[0].salary], ['4', 'To be confirmed'])
  assert.deepEqual([filled[1].vacancies, filled[1].salary], ['', ''], 'an unused blank row stays blank')
})

test('"use these fields for the other positions" adds the missing field names, no details, and never duplicates', () => {
  const waiter = { id: 'p3', title: 'Waiter', vacancies: '', salary: '', requirements: [{ id: 'r9', field: 'qualification', details: 'Diploma' }] }
  const untitled = { ...m.blankPosition(), requirements: [] }
  const out = m.copyFieldsToOthers([paratha, captain, waiter, untitled], 'p2')
  assert.deepEqual(out[0].requirements.map((r) => r.field), ['Practical Skill / Video', 'Qualification', 'Maximum Age', 'Experience'])
  assert.deepEqual(out[0].requirements.slice(1).map((r) => r.details), ['', '', ''])
  assert.deepEqual(out[1], captain, 'the source is unchanged')
  assert.deepEqual(out[2].requirements.map((r) => [r.field, r.details]), [['qualification', 'Diploma'], ['Maximum Age', ''], ['Experience', '']])
  assert.deepEqual(out[3].requirements, [], 'a position with no title is left alone')
  assert.deepEqual(m.unusedNames(m.QUICK_REQUIREMENT_FIELDS, ['qualification', 'Experience']), ['Maximum Age', 'Skills / Trade'])
})

test('the table cell: titles, number of positions, vacancies total only when every figure is a number; old rows fall back', () => {
  assert.equal(m.positionsSummary(saved({ positions: [{ ...paratha, vacancies: '3' }, captain, { id: 'p3', title: 'Waiter', vacancies: '7', salary: '', requirements: [] }] })), 'Paratha Master, Captain, Waiter · 3 positions · 12 vacancies')
  assert.equal(m.positionsSummary(saved()), 'Paratha Master, Captain · 2 positions', '"To be confirmed" is not a number: no total')
  assert.equal(m.positionsSummary(item({ positions: [{ id: 'x', title: 'Welder', vacancies: '1', salary: '', requirements: [] }] })), 'Welder · 1 vacancy')
  assert.equal(m.positionsSummary(item({ details: { position: 'Welder', vacancies: '5' } })), 'Welder · 5', 'the old flat summary')
  assert.equal(m.positionsSummary(item({ positions: [] })), '')
  assert.equal(m.positionsSummary({ details: {} }), '', 'rows without the lists at all (old cached data) do not crash')
  assert.deepEqual(m.positionsInfo(saved()), { titles: ['Paratha Master', 'Captain'], count: 2, vacancies: null })
  assert.deepEqual(m.positionsInfo({}), { titles: [], count: 0, vacancies: null })
  assert.equal(m.hasDetails(item({ positions: [paratha] })), true)
  assert.equal(m.hasDetails(item({ general: [{ id: 'g', label: 'Food', details: 'x', remarks: '' }] })), true)
  assert.equal(m.hasDetails(item()), false)
  assert.equal(m.hasDetails({ details: undefined }), false)
})

test('the downloaded form is named after the client', () => {
  assert.equal(m.formFileName('Al Noor Restaurant'), 'Al-Noor-Restaurant-requirement-form.xlsx')
  assert.equal(m.formFileName('  '), 'job-requirement-form.xlsx')
  assert.equal(m.formFileName('A/B: "C"'), 'A-B-C-requirement-form.xlsx')
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
  assert.equal(m.applyPatch(item(), { ownerIds: ['u9'] }).ownerId, 'u9')
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

test('a duplicate keeps the client, country, details, positions with requirements, general rows and extra columns, not the ad, date or status', () => {
  const copy = m.duplicateBody(item({ details: { position: ' Welder ', basicSalary: '', remarks: 'abc' }, custom: { f1: 'x' }, adUrl: '/ad', status: 'closed' }))
  assert.deepEqual(copy, { clientName: 'xyz company', country: 'Kuwait', details: { position: 'Welder', remarks: 'abc' }, custom: { f1: 'x' } })
  const full = m.duplicateBody(saved())
  assert.deepEqual(full.positions.map((p) => [p.title, p.requirements.length]), [['Paratha Master', 1], ['Captain', 3]])
  assert.ok(full.positions.every((p) => p.id === undefined && p.requirements.every((r) => r.id === undefined)), 'new rows, new ids')
  assert.deepEqual(full.general, [{ label: 'Weekly off', details: '2 days per month', remarks: 'As provided by client' }, { label: 'Air Ticket', details: 'Return', remarks: '' }])
  assert.deepEqual(full.details, { remarks: 'Pending confirmation.', deadline: '2026-12-01' })
})

test('an optimistic change replaces the positions and general lists wholesale, giving new rows an id', () => {
  const row = saved()
  const edited = m.applyPatch(row, { positions: [{ title: 'Cook', vacancies: '1', salary: '', requirements: [{ field: 'Experience', details: '3 years' }] }], details: { remarks: 'x' } })
  assert.deepEqual(edited.positions.map((p) => p.title), ['Cook'])
  assert.ok(edited.positions[0].id && edited.positions[0].requirements[0].id)
  assert.equal(edited.general.length, 2, 'a list that is not in the patch is untouched')
  assert.deepEqual(edited.details, { deadline: '2026-12-01', remarks: 'x' })
  assert.deepEqual(m.applyPatch(row, { general: [] }).general, [])
  assert.deepEqual(m.applyPatch(item(), { clientName: 'z' }).positions, undefined, 'no list is invented')
  const later = m.mergePatches({ positions: [{ title: 'a', vacancies: '', salary: '', requirements: [] }], details: { remarks: '1' } }, { positions: [], details: { deadline: 'd' } })
  assert.deepEqual(later, { positions: [], details: { remarks: '1', deadline: 'd' } }, 'the later list replaces the earlier one, details merge by key')
  assert.deepEqual(m.mergePatches({ general: [] }, { clientName: 'k' }), { general: [], clientName: 'k' })
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
  assert.ok(plain.client >= 160 && plain.details >= 180)
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
  assert.ok(long.owner > short.owner && long.owner <= 204)
  assert.equal(long.total, 1500, 'still exactly the box')
  const tight = m.columnWidths(1053, 0, { client: 20, details: 30, country: 20, owner: 30 })
  assert.equal(tight.total, 1053, 'no scroll bar at 1366 even for long texts')
  assert.ok(tight.client >= 160 && tight.details >= 180, 'Client and Details keep their minimum')
  const crowded = m.columnWidths(1053, 3, { client: 20, details: 30, country: 20, owner: 30 })
  assert.equal(crowded.country, m.FIXED_WIDTHS.country, 'with extra columns nothing grows: the box scrolls instead')
  assert.equal(crowded.owner, m.FIXED_WIDTHS.owner)
})
