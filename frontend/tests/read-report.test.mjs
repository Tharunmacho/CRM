import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'

async function loadSource(path) {
  const source = await readFile(new URL(path, import.meta.url), 'utf8')
  const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } })
  return import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`)
}

const { buildReadReport, fieldWords, conflictText, unmappedViews, jobReports, formReports } = await loadSource('../src/features/candidates/read-report.ts')

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

test('the headline names the documents and the chips follow the fixed order, with no page numbers', () => {
  const report = buildReadReport(coverage())
  assert.equal(report.headline, 'Resume, Passport and Aadhaar read.')
  assert.deepEqual(report.chips.map((chip) => chip.label), ['Resume', 'Passport', 'Aadhaar'])
  assert.deepEqual(report.chips.map((chip) => chip.state), ['read', 'verified', 'read'])
  assert.equal(report.chips[1].title, 'Cross-checked: passport number and date of birth')
  assert.equal(buildReadReport(coverage({ kinds: { passport: { pages_sent: [6] } } })).headline, 'Passport read.')
  assert.doesNotMatch(JSON.stringify(report.chips) + report.headline, /page/i)
})

test('a cross-check conflict turns the chip amber and says what to check in plain words', () => {
  const data = coverage()
  data.kinds.passport.crossCheck = { verified: ['surname'], conflicts: ['doe'] }
  const report = buildReadReport(data)
  assert.equal(report.chips[1].state, 'check')
  const note = report.notes.find((item) => item.key === 'check:passport')
  assert.equal(note.tone, 'warn')
  assert.equal(note.text, 'Passport: date of expiry — please check it against the passport.')
  data.kinds.passport.crossCheck = { verified: [], conflicts: ['passport_number'] }
  assert.equal(buildReadReport(data).notes.find((item) => item.key === 'check:passport').text, 'Passport number — please check it against the passport.')
  assert.equal(conflictText('passport', ['date of issue', 'date of expiry']), 'Passport: date of issue and date of expiry — please check them against the passport.')
  data.kinds.aadhaar.crossCheck = { verified: [], conflicts: [{ field: 'dob' }] }
  assert.equal(buildReadReport(data).notes.find((item) => item.key === 'check:aadhaar').text, 'Aadhaar: date of birth — please check it against the aadhaar.')
  assert.equal(fieldWords('passport_number'), 'passport number')
  assert.equal(fieldWords('dateOfBirth'), 'date of birth')
})

test('unresolved pages give one plain note with Read again; ignored pages are not mentioned', () => {
  const data = coverage({ pages: { total: 16, sent: [1], not_sent: [{ page: 16, reason: 'x', blocking: true }], ignored: [7, 15], unresolved: [16], probed: [] } })
  const report = buildReadReport(data)
  const unresolved = report.notes.find((note) => note.key === 'unresolved')
  assert.equal(unresolved.text, 'Page 16 could not be fully read.')
  assert.equal(unresolved.retry, true)
  assert.equal(unresolved.tone, 'warn')
  assert.equal(report.notes.some((note) => note.key === 'ignored'), false)
  assert.deepEqual(buildReadReport(coverage({ pages: { ignored: [4] } })).notes, [])
  assert.equal(buildReadReport(coverage({ pages: { unresolved: [3, 4] } })).notes[0].text, 'Pages 3 and 4 could not be fully read.')
})

test('incomplete coverage is never silently presented as a complete reading for any document kind', () => {
  for (const kind of ['resume', 'passport', 'aadhaar']) {
    const data = { kinds: { [kind]: {} }, complete: false }
    assert.equal(buildReadReport(data).notes[0].text, 'The extracted details need review.')
    data.pages = { not_sent: [{ page: 2, blocking: true }], unresolved: [] }
    assert.equal(buildReadReport(data).notes[0].text, 'Page 2 could not be fully read.')
  }
  assert.equal(buildReadReport({ complete: false }).notes[0].retry, true)
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
  assert.equal(views[5].hidden, false)
  assert.deepEqual(views[5].samples, ['Synthetic street'])
  assert.equal(views[0].group, 'Mobile number')
})

test('details with no place on the form are kept out of the report text and never carry sensitive values', () => {
  const report = buildReadReport(coverage(), [{ kind: 'aadhaar', path: 'aadhaar.vid', count: 1, samples: ['1111 2222 3333 4444'] }])
  assert.deepEqual(report.unmapped[0].samples, [])
  assert.doesNotMatch(JSON.stringify({ headline: report.headline, chips: report.chips, notes: report.notes }), /1111/)
  assert.equal(buildReadReport(null, [{ kind: 'resume', path: 'a', count: 1, values: ['x'] }]), null)
})

test('missing or odd keys never break and an older server gives no report', () => {
  assert.equal(buildReadReport(undefined), null)
  assert.equal(buildReadReport(null), null)
  assert.equal(buildReadReport({}), null)
  assert.equal(buildReadReport('nope'), null)
  assert.equal(buildReadReport([]), null)
  assert.equal(buildReadReport({}, []), null)
  assert.equal(buildReadReport({ summary: 'Read: nothing.' }), null)
  const odd = buildReadReport({ summary: 5, pages: 'x', kinds: { resume: null, passport: { pages_sent: 'x', crossCheck: { verified: 'a', conflicts: [null, 3] } } }, unmapped: [null, 4, {}] })
  assert.deepEqual(odd.chips.map((chip) => chip.label), ['Resume', 'Passport'])
  assert.equal(odd.headline, 'Resume and Passport read.')
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
    { slot: 'medical', label: 'Medical', extraction: { jobId: 'j2', updatedAt: '2026-01-03T00:00:00Z', coverage: coverage() } },
    { slot: 'other', label: 'Other', extraction: { jobId: 'j3', updatedAt: '2026-01-04T00:00:00Z' } },
  ]
  const reports = formReports(slots)
  assert.deepEqual(reports.map((item) => item.id), ['j2', 'j1'])
  assert.equal(reports[1].caption, 'Resume')
  assert.deepEqual(formReports([]), [])
})

test('multi-file passport reports retain each page reading instead of only the latest aggregate result', () => {
  const reports = formReports([{ slot: 'passport', label: 'Passport', multiple: true,
    extraction: { jobId: 'aggregate', coverage: coverage() }, files: [
      { attachmentId: 'front', fileName: 'front.pdf', extraction: { jobId: 'j-front', updatedAt: '2026-11-12T10:00:00Z', coverage: coverage() } },
      { attachmentId: 'address', fileName: 'address.pdf', extraction: { jobId: 'j-address', updatedAt: '2026-11-12T10:01:00Z', coverage: coverage() } },
    ] }])
  assert.deepEqual(reports.map(item => item.id), ['j-address', 'j-front'])
  assert.equal(reports[0].caption, 'Passport · address.pdf')
  assert.equal(reports[1].caption, 'Passport · front.pdf')
})
