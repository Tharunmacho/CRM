import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import ts from 'typescript'
import { withRoleLabels } from './fixtures/role-labels-url.mjs'

function moduleUrl(source) {
  const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } })
  return `data:text/javascript;base64,${Buffer.from(withRoleLabels(outputText)).toString('base64')}`
}
const read = (path) => readFile(new URL(`../src/features/application-form/${path}`, import.meta.url), 'utf8')

const slotsUrl = moduleUrl(await readFile(new URL('../src/shared/model/document-slots.ts', import.meta.url), 'utf8'))
const readReportUrl = moduleUrl(await readFile(new URL('../src/features/candidates/read-report.ts', import.meta.url), 'utf8'))
const modelUrl = moduleUrl((await read('form-model.ts')).replace("'@/shared/model/document-slots'", `'${slotsUrl}'`).replace("'@/features/candidates/read-report'", `'${readReportUrl}'`))
const model = await import(modelUrl)
const bridge = await import(moduleUrl((await read('entry-bridge.ts')).replace("'./form-model'", `'${modelUrl}'`)))
const phone = await import(moduleUrl(await read('parts/phone.ts')))
const interest = await import(moduleUrl(await read('parts/interest.ts')))
const merge = await import(moduleUrl(await read('duplicate-merge.ts')))

const server = (patch = {}) => ({
  candidate: { id: 'c1', ref: 'CND-1', name: 'Asha Rao', canEdit: true, ownerName: 'Ravi' },
  application: { id: 'a1', orderId: 'o1', orderRef: 'ORD-1', status: 'active', currentStage: 4 },
  personal: { candidateName: 'Asha Rao', fatherName: 'K Rao', dateOfBirth: '1990-05-17', maritalStatus: 'Married', gender: 'Female', nationality: 'Indian', languagesKnown: ['English', 'hindi', 'English'], mobileNumber: '+91 98765 43210', currentAddress: 'Chennai' },
  passport: { passportNumber: '', placeOfIssue: null, issueDate: null, expiryDate: null, expiry: { status: 'unknown', daysLeft: null } },
  education: [{ level: 'B.Tech', passedYear: 2012, institute: 'ABC College' }],
  experience: { totalExperience: 6, experienceIndia: 4, experienceAbroad: 2, history: [{ company: 'XYZ', designation: 'Welder', country: 'India', period: '2018 - 2020' }] },
  documents: {
    resume: { slot: 'resume', uploaded: true, current: { attachmentId: 'f1', fileName: 'cv.pdf', url: '/api/files/f1', version: 1 }, extraction: { status: 'done', filled: ['personal.fatherName'] } },
    passport: { slot: 'passport', uploaded: false, current: null, extraction: null },
    photo: { slot: 'photo', uploaded: false, current: null, extraction: null },
  },
  recruitment: { client: 'Acme', country: 'Qatar', position: 'Welder', source: 'Email', recruiter: { id: 'u1', name: 'Ravi' } },
  tracking: { summary: { completed: 3, tracked: 11, percent: 27, currentStage: 4, currentStageLabel: 'Interview result', applicationStatus: 'active' }, rows: [
    { key: 'prescreening', label: 'Pre-screening', stage: 1, tracked: true, status: 'completed', date: '2026-10-01', completedBy: { id: 'u1', name: 'Ravi' } },
    { key: 'interview_result', label: 'Interview Result', stage: 4, extra: true, tracked: true, status: 'in_progress' },
    { key: 'pcc', label: 'PCC', stage: null, tracked: false, status: 'not_applicable' },
  ] },
  completeness: { percent: 63, filled: 12, total: 19, missing: [{ key: 'documents.passport', label: 'Passport', section: 'documents', kind: 'document', message: 'Passport not uploaded' }] },
  fieldSources: { 'personal.fatherName': 'extracted', 'personal.candidateName': 'manual' },
  ...patch,
})

test('the payload is normalised: languages de-duplicated, numbers as text, gender canonical', () => {
  const form = model.normalizeForm(server())
  assert.deepEqual(form.personal.languagesKnown, ['English', 'hindi'])
  assert.equal(form.personal.gender, 'female')
  assert.equal(form.experience.totalExperience, '6')
  assert.equal(form.education[0].passedYear, '2012')
  assert.equal(form.recruitment.recruiter, 'Ravi')
  assert.equal(form.tracking.rows[0].completedBy, 'Ravi')
  assert.equal(form.completeness.missing[0].section, 5)
  assert.equal(form.completeness.missing[0].kind, 'document')
})

test('an empty or partial payload still yields a complete, blank form', () => {
  const form = model.normalizeForm({})
  assert.equal(form.personal.candidateName, '')
  assert.deepEqual(form.education, [])
  assert.equal(form.application, null)
  assert.equal(form.documents.passport.uploaded, false)
  assert.equal(form.extrasSupported, false)
  // An older response counts its sixteen fields and the two original mandatory document slots.
  assert.equal(form.completeness.total, 18)
  assert.equal(form.completeness.percent, 0)
})

test('manual document marks preserve attribution without inventing file links, and all passport pages keep their own reading result', () => {
  const form = model.normalizeForm(server({ capabilities: { deferredDocumentReading: true }, documents: {
    medical: { uploaded: true, fileUploaded: false, current: null, manualStatus: { uploaded: true, by: { id: 'u1', name: 'Ravi' }, at: '2026-11-12T10:00:00Z', note: 'Received at the office' } },
    passport: { multiple: true, files: [
      { attachmentId: 'p1', fileName: 'front.pdf', url: '/api/files/p1', extraction: { status: 'failed', message: 'A clearer photo page is needed.' } },
      { attachmentId: 'p2', fileName: 'address.pdf', url: '/api/files/p2', extractionPending: true, extraction: { status: 'done', filled: ['passport.placeOfIssue'] } },
    ] },
  } }))
  assert.equal(form.documents.medical.uploaded, true)
  assert.equal(form.documents.medical.fileUploaded, false)
  assert.equal(form.documents.medical.manualStatusSupported, true)
  assert.equal(form.documents.medical.current, null)
  assert.deepEqual(form.documents.medical.files, [])
  assert.equal(form.documents.medical.manualStatus.by.name, 'Ravi')
  assert.equal(form.documents.medical.manualStatus.note, 'Received at the office')
  assert.equal(form.documents.passport.fileUploaded, true)
  assert.equal(form.documents.passport.multiple, true)
  assert.equal(form.capabilities.deferredDocumentReading, true)
  assert.equal(form.documents.passport.files[1].extractionPending, true)
  assert.deepEqual(form.documents.passport.files.map(file => file.extraction.status), ['failed', 'done'])
  const older = model.normalizeForm(server({ documents: { passport: { current: { attachmentId: 'old', fileName: 'saved.pdf', url: '/api/files/old' } } } }))
  assert.equal(older.documents.passport.files[0].attachmentId, 'old')
  assert.equal(older.documents.passport.manualStatusSupported, false)
  assert.equal(older.capabilities.deferredDocumentReading, false)
})

test('a file cut from a larger scan keeps the scan name and pages; a malformed splitFrom is dropped', () => {
  const file = (splitFrom) => ({ attachmentId: 'p', fileName: 'passport.pdf', url: '/api/files/p', splitFrom })
  const read = (splitFrom) => model.normalizeForm(server({ documents: { passport: { current: file(splitFrom), files: [file(splitFrom)] } } })).documents.passport
  assert.deepEqual(read({ fileName: 'scan.pdf', pages: [2, 3] }).files[0].splitFrom, { fileName: 'scan.pdf', pages: [2, 3] })
  assert.equal(read(undefined).files[0].splitFrom, undefined)
  assert.equal(read({ fileName: 'scan.pdf', pages: ['x'] }).files[0].splitFrom, undefined)
  assert.equal('splitFrom' in read(undefined).files[0], false)
})

test('missing-field detection preserves older payloads without counting optional photos', () => {
  const form = model.normalizeForm(server())
  const live = model.completenessOf(model.toDraft(form), form.documents)
  const keys = live.missing.map((item) => item.key)
  assert.ok(keys.includes('documents.passport'))
  assert.ok(!keys.includes('documents.photo'))
  assert.ok(keys.includes('passport.passportNumber'))
  assert.ok(!keys.includes('personal.fatherName'))
  assert.ok(!keys.includes('education'))
  assert.equal(live.total, 18)
  assert.equal(live.filled + live.missing.length, 18)
  assert.equal(live.percent, Math.round((live.filled / 18) * 100))
  // Experience in India / abroad are highlighted when empty but not counted.
  assert.ok(model.EXPERIENCE_FIELDS.filter((field) => !field.counted).length === 2)
})

test('completeness updates as the draft is filled in, and zero years is a value', () => {
  const form = model.normalizeForm(server())
  const draft = model.toDraft(form)
  const before = model.completenessOf(draft, form.documents).percent
  draft.passport.passportNumber = 'M1234567'
  draft.experience.experienceAbroad = '0'
  assert.ok(model.completenessOf(draft, form.documents).percent > before)
  assert.equal(model.fieldIsMissing(draft, 'experience.experienceAbroad'), false)
  assert.equal(model.fieldIsMissing(draft, 'passport.placeOfIssue'), true)
})

test('provenance names the document an extracted value came from, and manual wins', () => {
  const form = model.normalizeForm(server())
  assert.equal(model.provenanceOf(form, 'personal.fatherName', true), 'resume')
  assert.equal(model.provenanceOf(form, 'personal.candidateName', true), 'manual')
  assert.equal(model.provenanceOf(form, 'personal.fatherName', false), 'none')
  assert.equal(model.provenanceOf(form, 'personal.nationality', true), 'none')
  form.fieldSources['passport.passportNumber'] = 'extracted'
  assert.equal(model.provenanceOf(form, 'passport.passportNumber', true), 'passport')
  // A passport that arrives later and reports a field it filled takes the badge.
  form.fieldSources['personal.nationality'] = 'extracted'
  form.documents.passport = { ...form.documents.passport, uploaded: true, extraction: { status: 'done', filled: ['personal.nationality'], kept: [], kindsFound: [], message: '', updatedAt: '' } }
  assert.equal(model.provenanceOf(form, 'personal.nationality', true), 'passport')
})

test('the extraction note says reading, filled N fields, or enter manually', () => {
  const slot = (extraction, uploaded = true) => ({ uploaded, extraction })
  assert.equal(model.describeExtraction('passport', slot(null, false)).tone, 'none')
  assert.equal(model.describeExtraction('passport', slot({ status: 'processing', filled: [], message: '' })).tone, 'running')
  assert.equal(model.describeExtraction('passport', slot({ status: 'done', filled: ['a', 'b', 'c'], message: '' })).text, 'Filled 3 fields on the form')
  assert.equal(model.describeExtraction('resume', slot({ status: 'done', filled: ['a'], message: '' })).text, 'Filled 1 field on the form')
  const failed = model.describeExtraction('resume', slot({ status: 'unreadable', filled: [], message: 'No resume pages were found.' }))
  assert.equal(failed.tone, 'failed')
  assert.equal(failed.text, 'We could not read this file. Upload a clearer copy, or fill the details here.')
  assert.equal(model.describeExtraction('photo', slot({ status: 'done', filled: [], message: '' })).tone, 'none')
  assert.equal(model.isReading({ processing: false, documents: { resume: slot({ status: 'processing' }), passport: slot(null), photo: slot(null) } }), true)
})

test('the extraction note lists the fields the document had but the form did not take, never silently', () => {
  const slot = (extraction) => ({ uploaded: true, extraction })
  const done = model.describeExtraction('resume', slot({ status: 'done', filled: ['personal.fatherName'], kept: ['personal.dateOfBirth', 'personal.mobileNumber', 'personal.fatherName', 'document.identity'], message: '' }))
  // The green line says what was filled; what was left alone is a quiet note, never part of an alert box.
  assert.equal(done.text, 'Filled 1 field on the form')
  assert.deepEqual(done.notes, ['Not changed: Date of birth, Mobile number. Check them against the document.'])
  const nothing = model.describeExtraction('resume', slot({ status: 'done', filled: [], kept: ['personal.email', 'personal.email'], message: '' }))
  assert.equal(nothing.tone, 'note')
  assert.deepEqual(nothing.notes, ['Not changed: Email address. Check it against the document.'])
  const review = model.describeExtraction('resume', slot({ status: 'needs_review', filled: ['a'], kept: ['personal.dateOfBirth'], message: 'Some pages could not be read.' }))
  assert.equal(review.text, 'Filled 1 field on the form')
  assert.deepEqual(review.notes, ['Some pages could not be read.', 'Not changed: Date of birth. Check it against the document.'])
})

test('a rejected identity stays viewable but never counts as an accepted document or a successful reading', () => {
  const extraction = { status: 'needs_review', identityMismatch: true, filled: [], coverage: { complete: false }, message: 'Different person' }
  const file = { attachmentId: 'wrong', fileName: 'wrong-person.pdf', url: '/api/files/wrong', identityMismatch: true, extractionPending: true, extraction }
  const form = model.normalizeForm(server({ documents: { passport: { uploaded: true, current: file, files: [file], extraction } } }))
  const slot = form.documents.passport
  assert.equal(slot.uploaded, false)
  assert.equal(slot.fileUploaded, true)
  assert.equal(slot.files[0].identityMismatch, true)
  assert.equal(slot.extraction.identityMismatch, true)
  assert.deepEqual(model.describeExtraction('passport', slot), { tone: 'failed', notes: [], text: "This document was stopped as another person's. No details were filled. Choose Read again to see why." })
  assert.deepEqual(model.identityActions(slot.extraction), [{ kind: 'read_again' }])
  assert.ok(model.completenessOf(model.toDraft(form), form.documents).missing.some(item => item.key === 'documents.passport'))
  const valid = { attachmentId: 'valid', fileName: 'correct-back.pdf', url: '/api/files/valid' }
  const mixed = model.normalizeForm(server({ documents: { passport: { uploaded: true, current: file, files: [file, valid], extraction } } }))
  assert.equal(mixed.documents.passport.uploaded, true)
  assert.equal(mixed.documents.passport.files.length, 2)
})

test('a rejected document says what was read and what it was compared with, and never a number', () => {
  const rejected = (identityReasons, extra = {}) =>
    ({ uploaded: true, extraction: { status: 'needs_review', identityMismatch: true, filled: [], kept: [], kindsFound: [], message: '', updatedAt: '', identityReasons, ...extra } })
  const text = (reasons, extra) => model.describeExtraction('resume', rejected(reasons, extra)).text
  assert.equal(
    text([{ field: 'name', documentKind: 'resume', found: 'Rashid K. Faizal', expected: 'Rashid Khan', expectedFrom: 'candidate' }]),
    'The resume names "Rashid K. Faizal", but this candidate is "Rashid Khan". No details were filled. Open the document to check it. If it is the right person, confirm it below.')
  assert.match(text([{ field: 'name', documentKind: 'resume', found: 'A B', expected: 'C D', expectedFrom: 'other_document', expectedKind: 'passport' }]), /but the passport read with it names "C D"/)
  assert.match(text([{ field: 'name', documentKind: 'resume', found: 'A B', expected: 'C D', expectedFrom: 'stored_document' }]), /this candidate's other documents name "C D"/)
  assert.match(text([{ field: 'passport_no', documentKind: 'resume' }]), /^The resume's passport number differs from the passport already on this candidate\./)
  assert.match(text([{ field: 'candidate', documentKind: 'passport', matchedOn: ['phone number'] }]), /phone number already belongs to another candidate/)
  assert.match(text([{ field: 'candidate' }], { duplicateOf: { candidateId: 'x', careId: 'CI-157', name: 'Rashid Khan' } }), /^Already in the CRM as CI-157 · Rashid Khan/)
  assert.equal(text([]), "This document was stopped as another person's. No details were filled. Choose Read again to see why.")
  const actions = (identityReasons, extra = {}) => model.identityActions(rejected(identityReasons, extra).extraction)
  assert.deepEqual(actions([{ field: 'name' }]), [{ kind: 'read_again' }, { kind: 'confirm_name' }])
  assert.deepEqual(actions([{ field: 'name' }, { field: 'passport_no' }]), [{ kind: 'read_again' }, { kind: 'upload' }])
  assert.deepEqual(actions([{ field: 'passport_no' }]), [{ kind: 'check_passport' }, { kind: 'read_again' }])
  assert.deepEqual(actions([{ field: 'candidate' }], { duplicateOf: { candidateId: 'x', careId: 'CI-157', name: 'Rashid Khan' } }),
    [{ kind: 'open_candidate', candidateId: 'x', label: 'Open CI-157' }, { kind: 'read_again' }])
  assert.deepEqual(actions([{ field: 'name' }, { field: 'candidate' }]), [{ kind: 'read_again' }])
})

test('tracking: rows map to the docx wording, untracked rows are not progress, the current stage is flagged', () => {
  const form = model.normalizeForm(server())
  const rows = form.tracking.rows
  assert.equal(rows.find((row) => row.key === 'interview_result').current, true)
  assert.equal(rows.find((row) => row.key === 'prescreening').current, false)
  const summary = model.summariseTracking(rows)
  assert.equal(summary.total, 2)
  assert.equal(summary.completed, 1)
  assert.equal(summary.percent, 50)
  assert.equal(model.isOpenableRow(rows[0]), true)
  assert.equal(model.isOpenableRow(rows[2]), false)
  assert.equal(model.trackingStatus('Completed'), 'completed')
  assert.equal(model.trackingStatus('in-progress'), 'in_progress')
  assert.equal(model.trackingStatus('whatever'), 'not_started')
  // A closed application has no current stage.
  const closed = model.normalizeForm(server({ application: { id: 'a1', orderId: 'o1', status: 'closed', currentStage: 4 } }))
  assert.equal(closed.tracking.rows.some((row) => row.current), false)
})

test('invoice upload evidence is shown without becoming a twelfth workflow stage', () => {
  for (const invoiceStatus of ['not_started', 'completed']) {
    const form = model.normalizeForm(server({ tracking: { rows: [
      ...Array.from({ length: 11 }, (_, index) => ({ key: `stage-${index + 1}`, stage: index + 1, tracked: true, status: index < 5 ? 'completed' : 'not_started' })),
      { key: 'invoice_generated', label: 'Invoice', stage: null, tracked: true, status: invoiceStatus, completedBy: { id: 'u1', name: 'Ravi' } },
    ] } }))
    const summary = model.summariseTracking(form.tracking.rows)
    assert.equal(form.tracking.summary.tracked, 11)
    assert.equal(summary.total, 11)
    assert.equal(summary.completed, 5)
    assert.equal(summary.percent, 45)
    const invoice = form.tracking.rows.find(row => row.key === 'invoice_generated')
    assert.equal(invoice.status, invoiceStatus)
    assert.equal(invoice.completedBy, 'Ravi')
    assert.equal(model.isOpenableRow(invoice), false)
  }
})

test('date, age and years formatting', () => {
  assert.equal(model.formatFormDate('2026-03-09'), '09/03/2026')
  assert.equal(model.formatFormDate(''), '')
  assert.equal(model.formatFormDate('garbage'), 'garbage')
  const now = new Date(2026, 4, 16)
  assert.equal(model.ageFromDob('1990-05-17', now), 35)
  assert.equal(model.ageFromDob('1990-05-16', now), 36)
  assert.equal(model.ageFromDob('2030-01-01', now), null)
  assert.equal(model.ageFromDob('1990-02-31', now), null)
  assert.equal(model.yearsLabel(1), '1 year')
  assert.equal(model.yearsLabel('4'), '4 years')
  assert.equal(model.yearsLabel(''), '')
})

test('validation: dates, passport order, years, passed year, phone, e-mail', () => {
  const draft = model.toDraft(model.normalizeForm(server()))
  const now = new Date(2026, 4, 16)
  assert.deepEqual(model.validateDraft(draft, now), {})
  draft.personal.dateOfBirth = '2027-01-01'
  draft.passport.issueDate = '2020-01-01'
  draft.passport.expiryDate = '2019-01-01'
  draft.experience.totalExperience = '90'
  draft.education[0].passedYear = '12'
  draft.personal.mobileNumber = '123'
  draft.personal.email = 'nope'
  const errors = model.validateDraft(draft, now)
  assert.ok(errors['personal.dateOfBirth'])
  assert.ok(errors['passport.expiryDate'])
  assert.ok(errors['experience.totalExperience'])
  assert.ok(errors['education.0.passedYear'])
  assert.ok(errors['personal.mobileNumber'])
  assert.ok(errors['personal.email'])
})

test('repeating rows can be added, edited, moved and removed without touching the others', () => {
  const rows = [{ rowId: 'a', level: 'A' }, { rowId: 'b', level: 'B' }, { rowId: 'c', level: 'C' }]
  assert.deepEqual(model.updateRow(rows, 'b', { level: 'X' }).map((row) => row.level), ['A', 'X', 'C'])
  assert.deepEqual(model.moveRow(rows, 'c', -1).map((row) => row.rowId), ['a', 'c', 'b'])
  assert.deepEqual(model.moveRow(rows, 'a', -1).map((row) => row.rowId), ['a', 'b', 'c'])
  assert.deepEqual(model.moveRow(rows, 'c', 1).map((row) => row.rowId), ['a', 'b', 'c'])
  assert.deepEqual(model.removeRow(rows, 'b').map((row) => row.rowId), ['a', 'c'])
  assert.equal(model.updateRow(rows, 'b', { rowId: 'zzz' })[1].rowId, 'b')
  assert.equal(model.isBlankRow({ rowId: 'x', level: ' ', year: '' }), true)
  assert.equal(model.emptyEducationRow().rowId === model.emptyEducationRow().rowId, false)
})

test('a save sends only what changed, in the server shape, without blank rows', () => {
  const base = model.toDraft(model.normalizeForm(server()))
  const draft = model.toDraft(model.normalizeForm(server()))
  assert.equal(model.isDirty(base, draft), false)
  assert.deepEqual(model.buildSavePayload(base, draft), {})
  draft.passport.passportNumber = 'M1234567'
  draft.passport.issueDate = ''
  draft.personal.languagesKnown = ['English', 'Tamil']
  draft.experience.experienceIndia = '5'
  draft.education.push(model.emptyEducationRow())
  draft.education.push({ ...model.emptyEducationRow(), level: 'Diploma', passedYear: '2009', institute: 'Poly' })
  const payload = model.buildSavePayload(base, draft)
  assert.deepEqual(payload.passport, { passportNumber: 'M1234567' })
  assert.deepEqual(payload.personal, { languagesKnown: ['English', 'Tamil'] })
  assert.deepEqual(payload.experience, { experienceIndia: 5 })
  assert.deepEqual(payload.education, [
    { level: 'B.Tech', passedYear: 2012, institute: 'ABC College' },
    { level: 'Diploma', passedYear: 2009, institute: 'Poly' },
  ])
  assert.equal('rowId' in payload.education[0], false)
  // Clearing a date sends null.
  const cleared = model.toDraft(model.normalizeForm(server({ passport: { passportNumber: 'M1', issueDate: '2020-01-01' } })))
  const next = model.toDraft(model.normalizeForm(server({ passport: { passportNumber: 'M1', issueDate: '2020-01-01' } })))
  next.passport.issueDate = ''
  assert.deepEqual(model.buildSavePayload(cleared, next), { passport: { issueDate: null } })
})

test('a live refresh keeps what is being typed and takes the server value for everything else', () => {
  const oldBase = model.toDraft(model.normalizeForm(server()))
  const draft = model.toDraft(model.normalizeForm(server()))
  draft.personal.fatherName = 'Typed by hand'
  const fresh = model.normalizeForm(server({
    personal: { ...server().personal, fatherName: 'From passport', nationality: 'Indian Citizen' },
    passport: { passportNumber: 'M7654321', placeOfIssue: 'Chennai', issueDate: '2020-01-01', expiryDate: '2030-01-01' },
  }))
  const merged = model.rebaseDraft(oldBase, draft, model.toDraft(fresh))
  assert.equal(merged.personal.fatherName, 'Typed by hand')
  assert.equal(merged.personal.nationality, 'Indian Citizen')
  assert.equal(merged.passport.passportNumber, 'M7654321')
  assert.deepEqual(model.changedPaths(oldBase, model.toDraft(fresh)).includes('passport.passportNumber'), true)
})

test('screening details are optional, listed under `screening`, and ride the same save path', () => {
  assert.equal(model.normalizeForm(server()).extrasSupported, false)
  const form = model.normalizeForm(server({
    screening: { nativePlace: 'Madurai', countryPreference: 'Qatar', interestLevel: 'more_interested', jobPreference: ['Welder', 'Fitter'], skills: ['TIG'], nextFollowUpDate: '2026-12-01',
      serviceChargesStatus: 'not_discussed', callOutcome: { called: true, interested: 'yes', remarks: 'Call after 5', recordedAt: '2026-11-01T10:00:00Z' } },
  }))
  assert.equal(form.extrasSupported, true)
  assert.equal(form.extras['screening.countryPreference'], 'Qatar')
  assert.equal(form.extras['screening.jobPreference'], 'Welder, Fitter')
  assert.equal(form.call.remarks, 'Call after 5')
  const base = model.toDraft(form)
  const draft = model.toDraft(form)
  draft.extras['screening.interestLevel'] = 'not_interested'
  draft.extras['screening.nextFollowUpDate'] = ''
  draft.extras['screening.skills'] = 'TIG, Arc'
  assert.deepEqual(model.buildSavePayload(base, draft), { screening: { interestLevel: 'not_interested', nextFollowUpDate: null, skills: ['TIG', 'Arc'] } })
  draft.extras['screening.nextFollowUpDate'] = '2026-13-45'
  assert.ok(model.validateDraft(draft)['screening.nextFollowUpDate'])
})

test('a failed load is explained: outdated server, missing candidate, pending database update, network', async () => {
  const { classifyLoadError } = await import(moduleUrl(await read('load-error.ts')))
  assert.equal(classifyLoadError({ status: 404, message: 'Not Found' }).kind, 'server_outdated')
  assert.match(classifyLoadError({ status: 404, message: 'Not Found' }).text, /server needs to be updated/)
  assert.equal(classifyLoadError({ status: 404, message: 'Candidate not found' }).kind, 'candidate_not_found')
  assert.equal(classifyLoadError({ status: 404, message: 'Candidate not found' }).retryable, false)
  assert.equal(classifyLoadError({ status: 404, message: 'This candidate is not on that order.' }).kind, 'order_not_found')
  assert.equal(classifyLoadError({ status: 503, message: 'Update required: the database schema is out of date' }).kind, 'schema_pending')
  assert.equal(classifyLoadError({ status: 500, message: 'migration 0021 has not run' }).kind, 'schema_pending')
  assert.equal(classifyLoadError({ status: 403, message: 'No access' }).kind, 'forbidden')
  assert.equal(classifyLoadError({ status: 0, message: 'Cannot reach the server.' }).kind, 'network')
  assert.equal(classifyLoadError({ status: 500, message: 'Boom' }).kind, 'other')
  assert.equal(classifyLoadError(null).kind, 'other')
})

test('the form link carries the order or application it was opened for', () => {
  assert.equal(model.applicationFormHref('c1'), '/candidates/c1/application')
  assert.equal(model.applicationFormHref('c1', 'o 1'), '/candidates/c1/application?order=o%201')
  assert.equal(model.applicationFormHref('c1', 'o1', 'a1'), '/candidates/c1/application?application=a1')
})

test('Add candidate rows: JSON text round-trips, blank lists stay empty, payload is server-shaped', () => {
  const keys = bridge.EDUCATION_KEYS
  assert.deepEqual(bridge.parseRows('', keys), [])
  assert.deepEqual(bridge.parseRows('not json', keys), [])
  assert.deepEqual(bridge.parseRows('{"a":1}', keys), [])
  const rows = bridge.parseRows(JSON.stringify([{ level: 'B.E.', passedYear: 2014, institute: 'X' }, 'junk']), keys)
  assert.deepEqual(rows, [{ level: 'B.E.', passedYear: '2014', institute: 'X' }])
  assert.equal(bridge.serializeRows([bridge.emptyEntryRow(keys)]), '')
  assert.deepEqual(bridge.parseRows(bridge.serializeRows(rows), keys), rows)
  assert.deepEqual(bridge.educationPayload(bridge.serializeRows([{ level: 'ITI', passedYear: 'soon', institute: '' }, { level: '', passedYear: '', institute: '' }])), [{ level: 'ITI', passedYear: null, institute: null }])
  assert.deepEqual(bridge.historyPayload(JSON.stringify([{ company: 'A', designation: '', country: 'India', period: '' }])), [{ company: 'A', designation: null, country: 'India', period: null }])
})

test('the preview built from the Add candidate form marks typed and read fields, and counts missing items', () => {
  const entry = { name: 'Asha', fatherName: 'K Rao', phone: '+91 98765 43210', education: JSON.stringify([{ level: 'B.E.', passedYear: '2014', institute: 'X' }]), workHistory: '', totalExperience: '3', passportNo: 'M1234567', languages: 'English, Hindi' }
  const form = bridge.previewFormFromEntry(entry, {
    documents: [{ kind: 'resume', filename: 'cv.pdf', url: '/api/x', sizeKb: 10 }],
    sources: { fatherName: 'resume', passportNo: 'passport' },
    edited: new Set(['name']),
  })
  assert.equal(form.personal.candidateName, 'Asha')
  assert.deepEqual(form.personal.languagesKnown, ['English', 'Hindi'])
  assert.equal(form.education[0].passedYear, '2014')
  assert.equal(form.fieldSources['personal.candidateName'], 'manual')
  assert.equal(form.fieldSources['personal.fatherName'], 'extracted')
  assert.equal(model.provenanceOf(form, 'personal.fatherName', true), 'resume')
  assert.equal(model.provenanceOf(form, 'passport.passportNumber', true), 'passport')
  assert.equal(form.documents.resume.uploaded, true)
  assert.equal(form.documents.passport.uploaded, false)
  assert.ok(form.completeness.missing.some((item) => item.key === 'documents.passport'))
  assert.ok(form.completeness.missing.some((item) => item.key === 'experience.history'))
  assert.equal(form.application, null)
})

test('phone input helpers split and join the country code and describe the digits required', () => {
  assert.deepEqual(phone.splitPhone('+91 98765 43210'), { code: '+91', national: '98765 43210' })
  assert.deepEqual(phone.splitPhone('+971501234567'), { code: '+971', national: '501234567' })
  assert.deepEqual(phone.splitPhone('98765 43210'), { code: '+91', national: '98765 43210' })
  assert.deepEqual(phone.splitPhone('', '+974'), { code: '+974', national: '' })
  assert.equal(phone.joinPhone('+91', '98765 43210'), '+91 98765 43210')
  assert.equal(phone.joinPhone('+91', ' '), '')
  assert.equal(phone.phoneHint('+91'), 'Selected: India (+91) - Requires 10 digits')
  assert.equal(phone.phoneDigitsOk('+91', '98765 43210'), true)
  assert.equal(phone.phoneDigitsOk('+91', '9876'), false)
  assert.equal(phone.phoneDigitsOk('+91', ''), true)
})

test('interest levels map onto the existing initial-call result', () => {
  assert.deepEqual(interest.INTEREST_LEVELS.map((level) => level.tone), ['green', 'blue', 'amber', 'red'])
  assert.equal(interest.callInterestFor('more_interested'), 'yes')
  assert.equal(interest.callInterestFor('somewhat_interested'), 'yes')
  assert.equal(interest.callInterestFor('not_interested'), 'no')
  assert.equal(interest.callInterestFor(''), '')
})

test('the form shows only the last four digits of an Aadhaar number, whatever the server sent', () => {
  assert.equal(model.normalizeForm(server({ aadhaar: { maskedNumber: 'XXXX XXXX 7777' } })).aadhaarMasked, 'XXXX XXXX 7777')
  assert.equal(model.normalizeForm(server({ aadhaar: { maskedNumber: '999988887777' } })).aadhaarMasked, 'XXXX XXXX 7777')
  assert.ok(!JSON.stringify(model.normalizeForm(server({ aadhaar: { maskedNumber: '9999 8888 7777' } }))).includes('99998888'))
  assert.equal(model.normalizeForm(server({ aadhaar: null })).aadhaarMasked, '')
  assert.equal(model.normalizeForm(server()).aadhaarMasked, '')
  assert.equal(model.maskedAadhaar('12'), '')
})

test('the warning banner shows at most three plain warnings, once each, and hides technical ones', () => {
  const { visibleWarnings } = model
  assert.deepEqual(visibleWarnings([]), [])
  assert.deepEqual(visibleWarnings(['Passport expires soon.', 'Passport expires soon.', '  ', 'Date of birth is missing.']), ['Passport expires soon.', 'Date of birth is missing.'])
  assert.deepEqual(visibleWarnings([
    'MRZ region could not be located.', 'LLM structured complete', 'Passport check digits failed.', 'Enlarged local crop was used.',
    'Pages 2,3,4 assigned to adjoining document.', 'Reader disagree on name.', 'mrz.surname not seen before', 'Vision persistence failed.',
  ]), [])
  assert.deepEqual(visibleWarnings(['One.', 'Two.', 'Three.', 'Four.', 'Five.']), ['One.', 'Two.', 'Three.'])
  assert.deepEqual(visibleWarnings(['Crop it.', 'Photo is missing.']), ['Photo is missing.'])
})

const DUPLICATE = { candidateId: 'c139', careId: 'CI-139', name: 'Rashid Khan' }
const PLAN = { dryRun: true, allowed: true, source: { id: 'c167', careId: 'CI-167', name: 'Rashid K' }, mergedInto: { id: 'c139', careId: 'CI-139', name: 'Rashid Khan' },
  moved: { documents: 12, jobs: 4, notes: 3, tasks: 1, applications: 0, links: 0 }, skipped: [{ kind: 'document', name: 'cv.pdf', reason: 'The same file is already on CI-139.' }], refusals: [] }

test('the merge UI is switched off: no header button, no Merge into button, no Ask an admin text (the code and the endpoint stay)', async () => {
  assert.equal(merge.SHOW_MERGE_UI, false)
  const src = (path) => readFile(new URL(`../src/${path}`, import.meta.url), 'utf8')
  const screen = await src('screens/candidates/application-form.tsx')
  assert.match(screen, /SHOW_MERGE_UI && form && isAdminLevel\(me\.role\) && <MergeIntoPicker/)   // the only mount of the header button
  const line = await src('features/application-form/components/extraction-line.tsx')
  assert.match(line, /SHOW_MERGE_UI && wrongDocument && slot\.extraction\?\.duplicateOf && <DuplicateMergeAction/)
  assert.doesNotMatch(await src('features/application-form/components/documents-section.tsx'), /DuplicateMergeAction|MergeIntoPicker|candidate-merge/)
  // Even if mounted by mistake, the components render nothing while the flag is false.
  const merged = await src('features/application-form/components/candidate-merge.tsx')
  assert.match(merged, /SHOW_MERGE_UI \? mergeOffer\(/)
  assert.match(merged, /!SHOW_MERGE_UI \|\| !isAdminLevel\(store\.me\.role\)/)
  // The note keeps its words and the Open / Read again buttons; there is no merge action among them.
  const note = model.describeExtraction('passport', { uploaded: true, extraction: { identityMismatch: true, duplicateOf: DUPLICATE, identityReasons: [] } })
  assert.match(note.text, /^Already in the CRM as CI-139 · Rashid Khan/)
  assert.deepEqual(model.identityActions({ identityMismatch: true, duplicateOf: DUPLICATE, identityReasons: [] }).map((action) => action.kind), ['open_candidate', 'read_again'])
})

test('merge helpers (kept for when the UI is switched back on): an admin is offered Merge into CI-139; anybody else is told to ask an admin', () => {
  assert.deepEqual(merge.mergeOffer(DUPLICATE, { isAdmin: true, candidateId: 'c167' }), { kind: 'merge', targetId: 'c139', targetLabel: 'CI-139' })
  assert.equal(merge.mergeButtonLabel('CI-139'), 'Merge into CI-139')
  assert.deepEqual(merge.mergeOffer(DUPLICATE, { isAdmin: false, candidateId: 'c167' }), { kind: 'ask_admin', text: 'Ask an admin to merge it' })
  assert.equal(merge.mergeOffer({ ...DUPLICATE, careId: null }, { isAdmin: true, candidateId: 'c167' }).targetLabel, 'Rashid Khan')
  assert.equal(merge.mergeOffer(DUPLICATE, { isAdmin: true, candidateId: 'c139' }), null)       // never a merge with oneself
  assert.equal(merge.mergeOffer(null, { isAdmin: true, candidateId: 'c167' }), null)
  // The note itself and its other buttons are unchanged.
  assert.match(model.describeExtraction('passport', { uploaded: true, extraction: { identityMismatch: true, duplicateOf: DUPLICATE, identityReasons: [] } }).text, /^Already in the CRM as CI-139 · Rashid Khan/)
})

test('the merge confirmation says what moves and that the duplicate is removed', () => {
  const plan = merge.parseMergePlan(PLAN)
  assert.equal(merge.mergeConfirmText(plan, 'CI-167', 'CI-139'),
    '12 documents, 3 notes, 1 task will be moved to CI-139; this candidate CI-167 is then removed. Files already on CI-139 are kept as they are.')
  assert.equal(merge.movedSummary({ documents: 1, notes: 0, tasks: 2, applications: 1 }), '1 document, 2 tasks, 1 placement')
  assert.equal(merge.mergeConfirmText({ moved: { documents: 0, notes: 0, tasks: 0 } }, 'CI-167', 'CI-139'),
    'Nothing needs to move to CI-139; this candidate CI-167 is removed. Files already on CI-139 are kept as they are.')
  assert.equal(merge.mergeDoneText({ identityCheck: { accepted: 1, stillNeedReview: 0, requeued: 0 } }, { source: 'CI-167', target: 'CI-139' }),
    'CI-167 was merged into CI-139. 1 stopped document is now read for CI-139.')
  assert.equal(merge.mergeDoneText({ identityCheck: null }, { source: 'CI-167', target: 'CI-139' }), 'CI-167 was merged into CI-139.')
})

test('the merge dialog state: checking, ready, refused, confirmable refusal, merging, failed', () => {
  const labels = { source: 'CI-167', target: 'CI-139' }
  const plan = merge.parseMergePlan(PLAN)
  const checking = merge.mergeDialogView({ phase: 'checking', plan: null }, labels)
  assert.deepEqual([checking.busy, checking.confirmEnabled], [true, false])
  const ready = merge.mergeDialogView({ phase: 'ready', plan }, labels)
  assert.equal(ready.title, 'Merge CI-167 into CI-139')
  assert.equal(ready.confirmLabel, 'Merge and remove CI-167')
  assert.equal(ready.cancelLabel, 'Cancel')
  assert.deepEqual([ready.confirmEnabled, ready.blocked, ready.needsIdentityConfirm], [true, false, false])
  assert.deepEqual(ready.skipped, ['cv.pdf: The same file is already on CI-139.'])
  assert.equal(ready.lines.length, 1)                                                      // no deployed notice for an ordinary target
  const deployed = merge.parseMergePlan({ ...PLAN, targetDeployed: true, notice: 'CI-139 is deployed: documents and empty details are added, its stages are not touched.',
    skipped: [{ kind: 'placement', name: 'ORD-2', reason: 'CI-139 is deployed, so its stages are not touched: the active placement on ORD-2 is not carried over and goes with CI-167.' }] })
  const deployedView = merge.mergeDialogView({ phase: 'ready', plan: deployed }, labels)
  assert.equal(deployedView.lines[0], 'CI-139 is deployed: documents and empty details are added, its stages are not touched.')
  assert.match(deployedView.lines[1], /^12 documents, 3 notes, 1 task will be moved to CI-139/)
  assert.equal(deployedView.confirmEnabled, true)
  assert.match(deployedView.skipped[0], /^ORD-2: CI-139 is deployed/)
  assert.equal(merge.mergeDialogView({ phase: 'ready', plan: merge.parseMergePlan({ ...PLAN, targetDeployed: true }) }, labels).lines[0], 'CI-139 is deployed: documents and empty details are added, its stages are not touched.')   // an older server's answer still says it
  const hard = merge.parseMergePlan({ allowed: false, refusals: [{ code: 'kept_on_record', message: 'CI-139 has travelled.', canConfirm: false }] })
  const refused = merge.mergeDialogView({ phase: 'ready', plan: hard, identityConfirmed: true }, labels)
  assert.deepEqual([refused.blocked, refused.confirmEnabled, refused.needsIdentityConfirm, refused.cancelLabel, refused.lines], [true, false, false, 'Close', ['CI-139 has travelled.']])
  const soft = merge.parseMergePlan({ allowed: false, refusals: [{ code: 'identity_unconfirmed', message: 'Nothing shows they are one person.', canConfirm: true }] })
  assert.deepEqual([merge.mergeDialogView({ phase: 'ready', plan: soft }, labels).confirmEnabled, merge.mergeDialogView({ phase: 'ready', plan: soft, identityConfirmed: true }, labels).confirmEnabled], [false, true])
  const mixed = merge.parseMergePlan({ allowed: false, refusals: [{ code: 'kept_on_record', message: 'a', canConfirm: false }, { code: 'identity_unconfirmed', message: 'b', canConfirm: true }] })
  assert.equal(merge.mergeDialogView({ phase: 'ready', plan: mixed, identityConfirmed: true }, labels).confirmEnabled, false)   // a confirmation never overrides a hard refusal
  const merging = merge.mergeDialogView({ phase: 'merging', plan }, labels)
  assert.deepEqual([merging.busy, merging.confirmEnabled, merging.confirmLabel], [true, false, 'Merging…'])
  assert.deepEqual(merge.mergeDialogView({ phase: 'failed', plan: null, error: 'Both candidates are on an order.' }, labels).lines, ['Both candidates are on an order.'])
  assert.match(merge.mergeDialogView({ phase: 'failed', plan: null }, labels).lines[0], /could not be checked/)
  assert.equal(merge.mergeDialogView({ phase: 'ready', plan: merge.parseMergePlan({ alreadyMerged: true, mergedInto: PLAN.mergedInto }) }, labels).lines[0], 'CI-167 was already merged into CI-139.')
})

test('the server answer is read defensively', () => {
  const empty = merge.parseMergePlan(null)
  assert.deepEqual([empty.allowed, empty.alreadyMerged, empty.source, empty.mergedInto, empty.skipped, empty.refusals, empty.identityCheck, empty.targetDeployed, empty.notice], [true, false, null, null, [], [], null, false, null])
  assert.deepEqual(empty.moved, { documents: 0, jobs: 0, notes: 0, tasks: 0, applications: 0 })
  assert.equal(merge.parseMergePlan({ moved: { documents: -3, notes: 'x', tasks: 2.7 } }).moved.tasks, 2)
})

test('the candidate to merge into is found by name, Care India ID, reference or phone, never the candidate itself', () => {
  const people = [
    { id: 'a', careId: 'CI-139', ref: 'CND-01', name: 'Rashid Khan', phone: '+91 98765 43210' },
    { id: 'b', careId: 'CI-167', ref: 'CND-02', name: 'Rashid K', phone: '90000 11111' },
    { id: 'c', careId: 'CI-140', ref: 'CND-03', name: 'Asha Rao', phone: '' },
  ]
  assert.deepEqual(merge.findMergeTargets(people, 'ci-139', 'b').map((p) => p.id), ['a'])
  assert.deepEqual(merge.findMergeTargets(people, 'rashid', 'b').map((p) => p.id), ['a'])         // the duplicate itself is left out
  assert.deepEqual(merge.findMergeTargets(people, 'rashid', 'zzz').map((p) => p.id), ['b', 'a'])  // both start with the search: sorted by name
  assert.deepEqual(merge.findMergeTargets(people, '98765', 'b').map((p) => p.id), ['a'])
  assert.deepEqual(merge.findMergeTargets(people, 'cnd-03', 'b').map((p) => p.id), ['c'])
  assert.deepEqual(merge.findMergeTargets(people, 's', 'b'), [])                                   // one letter searches nothing
  assert.deepEqual(merge.findMergeTargets(people, 'nobody', 'b'), [])
})
