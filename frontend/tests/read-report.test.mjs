import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'

async function loadSource(path) {
  const source = await readFile(new URL(path, import.meta.url), 'utf8')
  const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } })
  return import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`)
}

const { buildReadReport, pageNumbersText, pagesLabel, fieldWords, conflictText, unmappedViews, unmappedCountText, jobReports, formReports } = await loadSource('../src/features/candidates/read-report.ts')

// Synthetic records only: no real names, numbers or documents.
const coverage = (over = {}) => ({
  summary: 'Read: resume pages 1-5, passport page 6, Aadhaar pages 13-14; 2 pages ignored (7, 15).',
  pages: { total: 15, sent: [1, 2, 3, 4, 5, 6, 13, 14], not_sent: [], ignored: [7, 15], unresolved: [], probed: [] },
  kinds: {
    aadhaar: { pages_sent: [13, 14], fields: { received: 10, mapped: 8, unknown: 0 }, left_out: [], conflicts: 0 },
    resume: { pages_sent: [1, 2, 3, 4, 5], fields: { received: 40, mapped: 30, unknown: 0 }, left_out: [], conflicts: 0 },
    passport: { pages_sent: [6], fields: { received: 12, mapped: 12, unknown: 0 }, left_out: [], conflicts: 0, crossCheck: { verified: ['passport_number', 'dob'], conflicts: [] } },
  },
  unmapped: [], issues: [], notes: [], complete: true, ...over,
})

test('page numbers read as plain words', () => {
  assert.equal(pageNumbersText([1, 2, 3, 4, 5]), '1 to 5')
  assert.equal(pageNumbersText([13, 14]), '13 and 14')
  assert.equal(pageNumbersText([6]), '6')
  assert.equal(pageNumbersText([1, 2, 3, 7]), '1 to 3 and 7')
  assert.equal(pageNumbersText([9, 2, 2, 4]), '2, 4 and 9')
  assert.equal(pagesLabel([6]), 'page 6')
  assert.equal(pagesLabel([13, 14]), 'pages 13 and 14')
  assert.equal(pagesLabel([]), '')
  assert.equal(pagesLabel(['x', null, -1, 0]), '')
})

test('the headline is the server summary and the chips follow the fixed order with their pages', () => {
  const report = buildReadReport(coverage())
  assert.equal(report.headline, 'Read: resume pages 1-5, passport page 6, Aadhaar pages 13-14; 2 pages ignored (7, 15).')
  assert.deepEqual(report.chips.map((chip) => chip.label), ['Resume pages 1 to 5', 'Passport page 6', 'Aadhaar pages 13 and 14'])
  assert.deepEqual(report.chips.map((chip) => chip.state), ['read', 'verified', 'read'])
  assert.equal(report.chips[1].title, 'Cross-checked: passport number and date of birth')
})

test('a cross-check conflict turns the chip amber and names the fields in plain words', () => {
  const data = coverage()
  data.kinds.passport.crossCheck = { verified: ['surname'], conflicts: ['passport_number'] }
  const report = buildReadReport(data)
  assert.equal(report.chips[1].state, 'check')
  const note = report.notes.find((item) => item.key === 'check:passport')
  assert.equal(note.tone, 'warn')
  assert.equal(note.text, 'Passport number differs between the scan and the reader: please check.')
  assert.equal(conflictText(['date of birth', 'passport number']), 'Date of birth and passport number differ between the scan and the reader: please check.')
  data.kinds.aadhaar.crossCheck = { verified: [], conflicts: [{ field: 'dob' }] }
  assert.equal(buildReadReport(data).notes.find((item) => item.key === 'check:aadhaar').text, 'Aadhaar: Date of birth differs between the scan and the reader: please check.')
  assert.equal(fieldWords('passport_number'), 'passport number')
  assert.equal(fieldWords('dateOfBirth'), 'date of birth')
})

test('ignored and unresolved pages are listed, the unresolved one offers Read again', () => {
  const data = coverage({ pages: { total: 16, sent: [1], not_sent: [{ page: 16, reason: 'x', blocking: true }], ignored: [7, 15], unresolved: [16], probed: [] } })
  const report = buildReadReport(data)
  const unresolved = report.notes.find((note) => note.key === 'unresolved')
  assert.equal(unresolved.text, 'Unresolved page 16: could not be read.')
  assert.equal(unresolved.retry, true)
  assert.equal(unresolved.tone, 'warn')
  const ignored = report.notes.find((note) => note.key === 'ignored')
  assert.equal(ignored.text, '2 pages ignored (7, 15)')
  assert.equal(ignored.tone, 'muted')
  assert.equal(report.notes.at(-1).key, 'ignored', 'muted notes come last')
  assert.equal(buildReadReport(coverage({ pages: { ignored: [4] } })).notes[0].text, '1 page ignored (4)')
  assert.equal(buildReadReport(coverage({ pages: { unresolved: [3, 4] } })).notes[0].text, 'Unresolved pages 3 and 4: could not be read.')
  assert.equal(buildReadReport(coverage({ pages: { ignored: Array.from({ length: 14 }, (_, i) => i + 1) } })).notes[0].text, '14 pages ignored (1, 2, 3, 4, 5, 6, 7, 8, 9, 10 and 4 more)')
})

test('rows left out and differing parts are called out', () => {
  const data = coverage()
  data.kinds.resume.left_out = [{ what: 'work history', received: 9, mapped: 6, left_out: 3 }]
  data.kinds.resume.conflicts = 2
  const notes = buildReadReport(data).notes.map((note) => note.text)
  assert.ok(notes.includes('Resume: 3 work history entries not placed on the form.'))
  assert.ok(notes.includes('Resume: different parts gave different values. The first was kept: please check.'))
})

test('unplaced details are grouped in words and capped at five samples', () => {
  const views = unmappedViews([
    { kind: 'passport', path: 'fields[].label', count: 3, values: ['One', 'Two', 'Three'], tier: 'content' },
    { kind: 'resume', group: 'hobbies[]', count: 8, samples: ['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h'] },
  ])
  assert.deepEqual(views.map((view) => view.kind), ['resume', 'passport'], 'ordered resume, passport, Aadhaar')
  assert.equal(views[0].group, 'Hobbies')
  assert.equal(views[0].count, 8)
  assert.equal(views[0].samples.length, 5)
  assert.equal(views[1].group, 'Fields label')
  assert.equal(views[1].kindName, 'Passport')
  assert.equal(unmappedViews([{ kind: 'resume', path: 'resume.summary_text', count: 1, values: ['x'.repeat(200)] }])[0].samples[0].length, 80)
})

test('sensitive groups never show values, only how many were found', () => {
  const views = unmappedViews([
    { kind: 'aadhaar', path: 'aadhaar.mobile_number', count: 1, values: ['0000000000'] },
    { kind: 'aadhaar', path: 'aadhaar.vid', count: 2, samples: ['1111 2222 3333 4444'] },
    { kind: 'aadhaar', path: 'aadhaar.enrollment_id', count: 1, samples: ['E-1'] },
    { kind: 'aadhaar', path: 'aadhaar.aadhaar_number', count: 1, samples: ['9999'] },
    { kind: 'aadhaar', path: 'aadhaar.something', count: 1, tier: 'sensitive', values: ['7'] },
    { kind: 'aadhaar', path: 'aadhaar.address_line', count: 1, values: ['Synthetic street'] },
  ])
  for (const view of views.slice(0, 5)) { assert.equal(view.hidden, true, view.group); assert.deepEqual(view.samples, []) }
  assert.equal(unmappedCountText(views[1]), '2 found, hidden')
  assert.equal(views[5].hidden, false)
  assert.deepEqual(views[5].samples, ['Synthetic street'])
  assert.equal(unmappedCountText(views[5]), '1')
  assert.equal(views[0].group, 'Mobile number')
})

test('a report carries the unplaced total, from the coverage or from beside it', () => {
  const inside = buildReadReport(coverage({ unmapped: [{ kind: 'resume', path: 'a', count: 2, values: ['x'] }, { kind: 'passport', path: 'b', count: 1, values: [] }] }))
  assert.equal(inside.unmappedCount, 3)
  const beside = buildReadReport(coverage(), [{ kind: 'resume', path: 'a', count: 4, values: ['x'] }])
  assert.equal(beside.unmappedCount, 4)
  assert.equal(buildReadReport(null, [{ kind: 'resume', path: 'a', count: 1, values: ['x'] }]).headline, 'Some details were not placed on the form.')
})

test('missing or odd keys never break and an older server gives no report', () => {
  assert.equal(buildReadReport(undefined), null)
  assert.equal(buildReadReport(null), null)
  assert.equal(buildReadReport({}), null)
  assert.equal(buildReadReport('nope'), null)
  assert.equal(buildReadReport([]), null)
  assert.equal(buildReadReport({}, []), null)
  const bare = buildReadReport({ summary: 'Read: nothing.' })
  assert.deepEqual({ ...bare }, { headline: 'Read: nothing.', chips: [], notes: [], unmapped: [], unmappedCount: 0 })
  const odd = buildReadReport({ summary: 5, pages: 'x', kinds: { resume: null, passport: { pages_sent: 'x', crossCheck: { verified: 'a', conflicts: [null, 3] } } }, unmapped: [null, 4, {}] })
  assert.deepEqual(odd.chips.map((chip) => chip.label), ['Resume', 'Passport'])
  assert.deepEqual(odd.unmapped, [])
  assert.equal(unmappedViews('x').length, 0)
  // The older shape named in the brief works too.
  const named = buildReadReport({ kinds: { resume: { rowsLeftOut: 2, conflicts: 0 } } })
  assert.equal(named.notes[0].text, 'Resume: 2 entries not placed on the form.')
})

test('add candidate: one report per document file that has coverage in its job result', () => {
  const jobs = [
    { id: 'a', filename: 'one.pdf', kind: 'resume', status: 'completed', result: { coverage: coverage() } },
    { id: 'b', filename: 'two.pdf', kind: 'resume', status: 'completed', result: { scans: {} } },
    { id: 'c', filename: 'three.pdf', kind: 'resume', status: 'dismissed', result: { coverage: coverage() } },
    { id: 'd', filename: 'four.pdf', kind: 'resume', status: 'needs_review', result: undefined, output: { coverage: coverage() } },
  ]
  assert.deepEqual(jobReports(jobs).map((item) => item.id), ['a', 'd'])
  assert.equal(jobReports(jobs)[0].filename, 'one.pdf')
  assert.deepEqual(jobReports([]), [])
})

test('application form: one report per reading, newest first, none without data', () => {
  const slots = [
    { slot: 'resume', label: 'Resume', extraction: { jobId: 'j1', updatedAt: '2026-01-02T00:00:00Z', coverage: coverage() } },
    { slot: 'passport', label: 'Passport', extraction: { jobId: 'j1', updatedAt: '2026-01-02T00:00:00Z', coverage: coverage() } },
    { slot: 'photo', label: 'Photo', extraction: null },
    { slot: 'visa', label: 'Visa' },
    { slot: 'medical', label: 'Medical', extraction: { jobId: 'j2', updatedAt: '2026-01-03T00:00:00Z', unmapped: [{ kind: 'resume', path: 'a', count: 1, values: ['x'] }] } },
    { slot: 'other', label: 'Other', extraction: { jobId: 'j3', updatedAt: '2026-01-04T00:00:00Z' } },
  ]
  const reports = formReports(slots)
  assert.deepEqual(reports.map((item) => item.id), ['j2', 'j1'])
  assert.equal(reports[1].caption, 'Resume')
  assert.deepEqual(formReports([]), [])
})
