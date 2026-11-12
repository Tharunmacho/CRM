import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import ts from 'typescript'

function moduleUrl(source) {
  const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } })
  return `data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`
}
const slotsUrl = moduleUrl(await readFile(new URL('../src/shared/model/document-slots.ts', import.meta.url), 'utf8'))
const modelSource = await readFile(new URL('../src/features/application-form/form-model.ts', import.meta.url), 'utf8')
const modelUrl = moduleUrl(modelSource.replace("'@/shared/model/document-slots'", `'${slotsUrl}'`))
const model = await import(modelUrl)
const fallbackSource = await readFile(new URL('../src/features/application-form/assignment-fallback.ts', import.meta.url), 'utf8')
const fallback = await import(moduleUrl(fallbackSource.replace("'./form-model'", `'${modelUrl}'`)))
const { DOCUMENT_SLOT_KEYS } = await import(slotsUrl)

const base = (patch = {}) => ({
  candidate: { id: 'c1', ref: 'CND-1', name: 'Asha Rao', canEdit: true },
  application: { id: 'a1', orderId: 'o1', orderRef: 'ORD-1', status: 'active', currentStage: 5, position: 'Welder', country: 'UAE' },
  personal: { candidateName: 'Asha Rao', fatherName: 'K Rao', dateOfBirth: '1990-05-17', maritalStatus: 'Married', gender: 'female', nationality: 'Indian', languagesKnown: ['English'], mobileNumber: '+91 98765 43210', currentAddress: 'Chennai' },
  passport: { passportNumber: 'M1234567', placeOfIssue: 'Chennai', issueDate: '2020-01-01', expiryDate: '2030-01-01' },
  education: [{ level: 'ITI', passedYear: 2012, institute: 'Govt ITI' }],
  experience: { totalExperience: 4, experienceIndia: 4, experienceAbroad: 0, history: [{ company: 'XYZ', designation: 'Welder', country: 'India', period: '2018 - 2020' }] },
  recruitment: { client: 'Gulf Builders LLC', position: 'Welder', orderRef: 'ORD-1', country: 'UAE', source: 'Walk-in', sourceCode: 'walk_in', sourceDetail: 'Kerala Recruiters', recruiter: { id: 'u1', name: 'Ravi' } },
  tracking: { summary: {}, rows: [] },
  ...patch,
})

const slot = (key, extra = {}) => ({ slot: key, uploaded: false, current: null, files: [], ...extra })
const doc = (name) => ({ attachmentId: `f-${name}`, fileName: name, sizeKb: 120, version: 1, url: `/api/files/${name}`, uploadedAt: '2026-11-05T10:00:00Z' })

const v2 = (patch = {}) => base({
  documentOrder: DOCUMENT_SLOT_KEYS,
  documents: Object.fromEntries(DOCUMENT_SLOT_KEYS.map((key) => [key, slot(key, {
    required: ['resume', 'passport', 'signed_offer'].includes(key),
    requiredFromStage: { signed_offer: 5, medical: 6, pcc: 7, visa: 9, emigration: 10, air_ticket: 11, undertaking_letter: 11, insurance: 11, invoice: 11 }[key] ?? null,
    multiple: key === 'other',
  })])),
  assignment: {
    client: { id: 'cl1', name: 'Al Noor Staffing', companyId: 'co1', companyName: 'Gulf Builders LLC' },
    job: { orderId: 'o1', ref: 'ORD-1', title: 'Welder', applicationId: 'a1', stage: 5, country: 'UAE' },
    source: { key: 'walk_in', label: 'Walk-in' },
    agency: { id: 'ag1', name: 'Kerala Recruiters' },
    recruiter: { id: 'u1', name: 'Ravi' },
    can: { job: true, source: true, agency: true, recruiter: false },
    options: {
      clients: [
        { id: 'cl1', name: 'Al Noor Staffing', companyId: 'co1', companyName: 'Gulf Builders LLC', jobs: [
          { orderId: 'o1', ref: 'ORD-1', title: 'Welder', openings: 3, status: 'open' },
          { orderId: 'o2', ref: 'ORD-2', title: 'Fitter', openings: 0, status: 'filled' },
          { orderId: 'o3', ref: 'ORD-3', title: 'Rigger', openings: 2, status: 'on_hold' },
        ] },
        { id: 'cl2', name: 'Doha Projects', companyId: 'co2', companyName: 'Doha Projects WLL', jobs: [{ orderId: 'o4', ref: 'ORD-4', title: 'Electrician', openings: 5, status: 'open' }] },
      ],
      agencies: [{ id: 'ag1', name: 'Kerala Recruiters' }, { id: 'ag2', name: 'Chennai Manpower' }],
      recruiters: [],
    },
  },
  feedback: { items: [{ id: 'n1', kind: 'delay', reason: 'waiting_client', body: 'Client has not replied', stage: 3, authorId: 'u1', authorName: 'Ravi', createdAt: '2026-11-05T10:00:00Z' }], canWrite: true,
    stageClock: { stage: 3, stageLabel: 'Interview scheduling', since: '2026-11-03T10:00:00Z', hours: 61, limitHours: 48, overdue: true, needsReason: true } },
  ...patch,
})

test('the documents list the mandatory checklist first and retain optional evidence', () => {
  const form = model.normalizeForm(v2())
  const labels = form.documentOrder.map((key) => form.documents[key].label)
  assert.deepEqual(labels, ['Application form', 'Resume', 'Passport', 'Aadhaar copy', 'Medical', 'Signed offer', 'Police clearance certificate (PCC)',
    'Visa', 'Air ticket', 'Undertaking letter', 'Emigration', 'Insurance', 'Invoice', 'Photo', 'Education certificate', 'Experience certificate', 'Google review', 'Visa stamping', 'Other documents'])
  assert.ok(form.documentOrder.indexOf('google_review') < form.documentOrder.indexOf('visa_stamping'))
  assert.equal(form.documents.other.multiple, true)
  assert.equal(form.documents.photo.hint.length > 0, true)
})

test('an older server that sends three documents still yields the whole checklist, and only counts what it sent', () => {
  const form = model.normalizeForm(base({ documents: { resume: slot('resume', { uploaded: true, current: doc('cv.pdf') }), passport: slot('passport'), photo: slot('photo') } }))
  assert.equal(form.documentOrder.length, 19)
  assert.equal(form.documents.education_certificate.listed, false)
  // Stage 5 asks for the signed offer, worked out locally because the server did not say.
  assert.equal(form.documents.signed_offer.required, true)
  assert.equal(form.documents.medical.required, false)
  const live = model.completenessOf(model.toDraft(form), form.documents)
  assert.equal(live.total, 18)
  assert.ok(!live.missing.some((item) => item.key === 'documents.education_certificate'))
})

test('the server decides what is required, and the missing list names the missing required documents', () => {
  const form = model.normalizeForm(v2())
  const live = model.completenessOf(model.toDraft(form), form.documents)
  const keys = live.missing.map((item) => item.key)
  assert.deepEqual(keys.filter((key) => key.startsWith('documents.')), ['documents.resume', 'documents.passport', 'documents.signed_offer'])
  assert.equal(live.total, 16 + 3)
  assert.equal(form.documents.application_form.required, false)
  assert.equal(form.documents.aadhaar.required, false)
  assert.ok(!keys.includes('documents.experience_certificate'))
  assert.ok(!keys.includes('documents.medical'))
})

test('uploads raise the percentage at once, before the server answers', () => {
  const form = model.normalizeForm(v2())
  const draft = model.toDraft(form)
  const before = model.completenessOf(draft, form.documents)
  const pending = { resume: { fileName: 'resume.pdf', sizeKb: 80 }, other: { fileName: 'extra.pdf', sizeKb: 10 } }
  const documents = model.applyOptimisticUploads(form.documents, pending)
  const after = model.completenessOf(draft, documents)
  assert.ok(after.percent > before.percent)
  assert.equal(after.filled, before.filled + 1)
  assert.equal(documents.resume.uploaded, true)
  assert.equal(documents.resume.current.fileName, 'resume.pdf')
  assert.equal(documents.other.files[0].fileName, 'extra.pdf')
  assert.equal(form.documents.resume.uploaded, false)
  assert.equal(model.applyOptimisticUploads(form.documents, {}), form.documents)
  // An optional document never changes the percentage.
  assert.equal(model.completenessOf(draft, model.applyOptimisticUploads(form.documents, { other: pending.other })).percent, before.percent)
})

test('documents are grouped as required now, later in the process and optional, keeping the contract order', () => {
  const form = model.normalizeForm(v2())
  const groups = model.groupDocuments(form.documents, form.documentOrder)
  assert.deepEqual(groups.required.map((item) => item.slot), ['resume', 'passport', 'signed_offer'])
  assert.deepEqual(groups.later.map((item) => item.slot), ['medical', 'pcc', 'visa', 'air_ticket', 'undertaking_letter', 'emigration', 'insurance', 'invoice'])
  assert.deepEqual(groups.optional.map((item) => item.slot), ['application_form', 'aadhaar', 'photo', 'education_certificate', 'experience_certificate', 'google_review', 'visa_stamping', 'other'])
  assert.equal(model.documentState(form.documents.medical), 'later')
  assert.equal(model.documentState(form.documents.other), 'optional')
  assert.equal(model.documentState(form.documents.resume), 'required')
  assert.equal(model.documentState({ uploaded: true, required: true, requiredFromStage: null }), 'uploaded')
  assert.equal(model.requiredByStageText(form.documents.medical), 'Needed from Medical')
  assert.equal(model.requiredByStageText(form.documents.resume), '')
  assert.deepEqual(model.requiredDocumentProgress({ a: { uploaded: true, required: true }, b: { uploaded: false, required: true }, c: { uploaded: false, required: false } }), { uploaded: 1, total: 2, percent: 50 })
  assert.equal(model.sizeLabel(220), '220 KB')
  assert.equal(model.sizeLabel(2048), '2.0 MB')
})

test('the assignment block is read from the payload, with Direct as the label for walk-in', () => {
  const form = model.normalizeForm(v2())
  const { assignment } = form
  assert.equal(assignment.supported, true)
  assert.equal(assignment.client.name, 'Al Noor Staffing')
  assert.equal(assignment.job.title, 'Welder')
  assert.deepEqual(assignment.source, { key: 'walk_in', label: 'Direct' })
  assert.equal(assignment.agency.name, 'Kerala Recruiters')
  assert.equal(assignment.can.recruiter, false)
  assert.equal(assignment.options.clients.length, 2)
  assert.deepEqual(model.SOURCE_OPTIONS.map((item) => item.label), ['WhatsApp', 'Email', 'Direct', 'Referral', 'Other'])
})

test('without an assignment key the block is built from the recruitment details and is read only', () => {
  const { assignment } = model.normalizeForm(base())
  assert.equal(assignment.supported, false)
  assert.equal(assignment.client.name, 'Gulf Builders LLC')
  assert.equal(assignment.job.title, 'Welder')
  assert.equal(assignment.source.label, 'Direct')
  assert.equal(assignment.agency.name, 'Kerala Recruiters')
  assert.equal(assignment.recruiter.name, 'Ravi')
  assert.deepEqual(assignment.can, { job: false, source: false, agency: false, recruiter: false })
  const blank = model.normalizeForm({}).assignment
  assert.equal(blank.client, null)
  assert.equal(blank.job, null)
  assert.equal(blank.source.label, '')
})

test('jobs that cannot be chosen say why, and a job reads as text with its openings', () => {
  const jobs = model.normalizeForm(v2()).assignment.options.clients[0].jobs
  assert.equal(model.jobUnavailableReason(jobs[0]), '')
  assert.equal(model.jobUnavailableReason(jobs[1]), 'Filled')
  assert.equal(model.jobUnavailableReason(jobs[2]), 'On hold')
  assert.equal(model.jobUnavailableReason({ status: 'closed', openings: 4 }), 'Closed')
  assert.equal(model.jobUnavailableReason({ status: 'open', openings: 0 }), 'No openings left')
  assert.equal(model.jobOptionText(jobs[0]), 'Welder · ORD-1 · 3 openings')
  assert.equal(model.jobOptionText({ title: 'Cook', ref: 'ORD-9', openings: 1 }), 'Cook · ORD-9 · 1 opening')
})

test('a change to the assignment shows at once: a job brings its client, a source its label, an agency its name', () => {
  const { assignment } = model.normalizeForm(v2())
  const moved = model.applyAssignmentChange(assignment, { orderId: 'o4' })
  assert.equal(moved.job.title, 'Electrician')
  assert.equal(moved.client.name, 'Doha Projects')
  assert.equal(assignment.job.title, 'Welder')
  assert.equal(model.applyAssignmentChange(assignment, { orderId: null }).job, null)
  assert.deepEqual(model.applyAssignmentChange(assignment, { source: 'whatsapp' }).source, { key: 'whatsapp', label: 'WhatsApp' })
  assert.equal(model.applyAssignmentChange(assignment, { agencyId: 'ag2' }).agency.name, 'Chennai Manpower')
  assert.deepEqual(model.applyAssignmentChange(assignment, { agencyId: null, agencyName: 'Local friend' }).agency, { id: '', name: 'Local friend' })
  assert.equal(model.applyAssignmentChange(assignment, { agencyId: null, agencyName: null }).agency, null)
  assert.equal(model.clientOfJob(assignment.options, 'o3').id, 'cl1')
})

test('recruiter notes are read from the payload and the stage clock is worded in days and hours', () => {
  const { feedback } = model.normalizeForm(v2())
  assert.equal(feedback.supported, true)
  assert.equal(feedback.items[0].authorName, 'Ravi')
  assert.equal(feedback.stageClock.needsReason, true)
  assert.equal(model.stageClockText(feedback.stageClock), 'Interview scheduling, 2 days 13 hours, limit 2 days')
  assert.equal(model.durationText(48), '2 days')
  assert.equal(model.durationText(5), '5 hours')
  assert.equal(model.durationText(1), '1 hour')
  assert.equal(model.durationText(0.3), 'less than an hour')
  assert.equal(model.durationText(25), '1 day 1 hour')
  assert.equal(model.normalizeForm(base()).feedback.supported, false)
  assert.equal(model.normalizeForm(v2({ feedback: { items: [], canWrite: false, stageClock: null } })).feedback.stageClock, null)
})

test('the stage clock is calm, then amber near the limit, then red when overdue', () => {
  assert.equal(model.stageClockTone({ hours: 10, limitHours: 48, overdue: false }), 'ok')
  assert.equal(model.stageClockTone({ hours: 40, limitHours: 48, overdue: false }), 'warn')
  assert.equal(model.stageClockTone({ hours: 49, limitHours: 48, overdue: false }), 'over')
  assert.equal(model.stageClockTone({ hours: 5, limitHours: 48, overdue: true }), 'over')
})

test('a note can be deleted by an administrator at any time, by its author only within 15 minutes', () => {
  const now = new Date('2026-11-05T10:20:00Z')
  const note = (minutes, authorId = 'u1') => ({ authorId, createdAt: new Date(now.getTime() - minutes * 60_000).toISOString() })
  assert.equal(model.canDeleteFeedback(note(5), { id: 'u1', role: 'staff' }, now), true)
  assert.equal(model.canDeleteFeedback(note(16), { id: 'u1', role: 'staff' }, now), false)
  assert.equal(model.canDeleteFeedback(note(5, 'u2'), { id: 'u1', role: 'manager' }, now), false)
  assert.equal(model.canDeleteFeedback(note(600, 'u2'), { id: 'a1', role: 'admin' }, now), true)
  assert.deepEqual(model.FEEDBACK_KINDS.map((item) => item.label), ['Update', 'Delay', 'Issue', 'Client feedback', 'Candidate feedback'])
  assert.equal(model.FEEDBACK_REASONS.length, 6)
  assert.equal(model.FEEDBACK_MAX_LENGTH, 2000)
})

test('labels use proper names: nothing is abbreviated or capitalised like a heading', () => {
  const labels = [...model.PERSONAL_FIELDS, ...model.PASSPORT_FIELDS, ...model.EXPERIENCE_FIELDS, ...model.EXTRA_FIELDS].map((field) => field.label)
  for (const wanted of ["Father's name", 'Date of birth', 'Place of issue', 'Date of issue', 'Date of expiry', 'Contact number of a family member', 'Email address', 'Languages known']) {
    assert.ok(labels.includes(wanted), wanted)
  }
  for (const label of labels) assert.ok(!/[A-Z][a-z]+ [A-Z][a-z]+/.test(label.replace(/WhatsApp/, 'W')), `Title Case label: ${label}`)
  assert.deepEqual(model.EDUCATION_COLUMNS.map((column) => column.label), ['Qualification or course', 'Year of passing', 'Name and address of institution'])
  assert.deepEqual(model.EDUCATION_FORM_COLUMNS.map((column) => column.label), ['Qualification or course', 'Year of passing', 'Name of institution', 'Address of institution'])
  assert.deepEqual(model.EXPERIENCE_COLUMNS.map((column) => column.label), ['Name of company', 'Designation', 'Country', 'Period of employment'])
  assert.equal(model.SECTION_TITLES[6], 'Client and recruitment details')
})

test('the institution is one stored text shown as a name and an address, and joins back without eating typed spaces', () => {
  assert.deepEqual(model.splitInstitution('ABC College, Chennai'), { name: 'ABC College', address: 'Chennai' })
  assert.deepEqual(model.splitInstitution('ABC College'), { name: 'ABC College', address: '' })
  assert.deepEqual(model.splitInstitution('ABC College, Anna Nagar, Chennai'), { name: 'ABC College', address: 'Anna Nagar, Chennai' })
  assert.deepEqual(model.splitInstitution(', Chennai'), { name: '', address: 'Chennai' })
  assert.equal(model.joinInstitution('ABC College', 'Chennai'), 'ABC College, Chennai')
  assert.equal(model.joinInstitution('ABC College', ''), 'ABC College')
  assert.equal(model.joinInstitution('ABC College', 'Anna '), 'ABC College, Anna ')
  for (const text of ['ABC College, Chennai', 'ABC', 'ABC College, Anna Nagar, Chennai']) {
    const { name, address } = model.splitInstitution(text)
    assert.equal(model.joinInstitution(name, address), text)
  }
})

test('a note carries the answer of the server about deleting when it gives one', () => {
  const form = model.normalizeForm(v2({ feedback: { canWrite: true, stageClock: null, items: [
    { id: 'n1', kind: 'delay', body: 'x', authorId: 'u9', createdAt: '2026-11-05T10:00:00Z', canDelete: true },
    { id: 'n2', kind: 'update', body: 'y', authorId: 'u1', createdAt: '2026-11-05T10:00:00Z' },
  ] } }))
  assert.equal(form.feedback.items[0].canDelete, true)
  assert.equal(form.feedback.items[1].canDelete, null)
  const viewer = { id: 'u1', role: 'staff' }
  assert.equal(model.canDeleteFeedback(form.feedback.items[0], viewer, new Date('2026-11-06T10:00:00Z')), true)
  assert.equal(model.canDeleteFeedback(form.feedback.items[1], viewer, new Date('2026-11-06T10:00:00Z')), false)
  assert.equal(model.canDeleteFeedback({ ...form.feedback.items[1], canDelete: false }, { id: 'a', role: 'admin' }, new Date()), false)
})

const workspace = () => ({
  clients: [{ id: 'cl1', name: 'Al Noor Staffing', status: 'active' }, { id: 'cl2', name: 'Zed Builders', status: 'active' }, { id: 'cl3', name: 'Old Client', status: 'inactive' }],
  companies: [{ id: 'co1', clientId: 'cl1', name: 'Gulf Builders LLC', status: 'active' }, { id: 'co2', clientId: 'cl2', name: 'Zed One', status: 'active' }, { id: 'co3', clientId: 'cl2', name: 'Zed Two', status: 'active' }],
  designations: [{ id: 'd1', title: 'Welder' }, { id: 'd2', title: 'Fitter' }],
  orders: [
    { id: 'o1', ref: 'ORD-1', companyId: 'co1', designationId: 'd1', openings: 3, country: 'UAE', status: 'open' },
    { id: 'o2', ref: 'ORD-2', companyId: 'co1', designationId: 'd2', openings: 0, country: 'UAE', status: 'filled' },
    { id: 'o3', ref: 'ORD-3', companyId: 'co3', designationId: 'd2', openings: 2, country: 'Qatar', status: 'on_hold' },
  ],
  users: [
    { id: 'u1', name: 'Ravi', role: 'staff', active: true, onLeave: false },
    { id: 'u2', name: 'Meena', role: 'manager', active: true, onLeave: false },
    { id: 'u3', name: 'Gone', role: 'staff', active: false, onLeave: false },
    { id: 'u4', name: 'Away', role: 'staff', active: true, onLeave: true },
  ],
  candidate: { source: 'walk_in', sourceDetail: 'Kerala Recruiters', ownerId: 'u1' },
  agencies: [{ id: 'ag1', name: 'Asha', agencyName: 'Kerala Recruiters' }, { id: 'ag2', name: 'Biju', agencyName: 'Tamil Agents' }],
  me: { id: 'u1', role: 'staff' },
  canEdit: true,
})

test('an older server sends no assignment: the block is built from the workspace, with dropdown options and permissions', () => {
  const form = model.normalizeForm(base({ recruitment: { client: 'Gulf Builders LLC', position: 'Welder', orderRef: 'ORD-1', source: 'Walk-in', recruiter: { id: 'u1', name: 'Ravi' } } }))
  assert.equal(form.assignment.supported, false)
  const block = fallback.completeAssignment(form.assignment, { ...workspace(), activeOrderId: 'o1' })
  assert.equal(block.client.id, 'cl1')
  assert.equal(block.client.name, 'Al Noor Staffing')
  assert.equal(block.job.orderId, 'o1')
  assert.equal(block.job.title, 'Welder')
  assert.deepEqual(block.source, { key: 'walk_in', label: 'Direct' })
  assert.deepEqual(block.agency, { id: 'ag1', name: 'Kerala Recruiters' })
  assert.equal(block.recruiter.name, 'Ravi')
  assert.deepEqual(block.can, { job: true, source: true, agency: true, recruiter: false })
  assert.deepEqual(block.options.clients.map((item) => item.name), ['Al Noor Staffing', 'Zed Builders'])
  const noor = block.options.clients[0]
  assert.equal(noor.companyName, 'Gulf Builders LLC')
  assert.deepEqual(noor.jobs.map((job) => [job.ref, job.title, job.status]), [['ORD-1', 'Welder', 'open'], ['ORD-2', 'Fitter', 'filled']])
  assert.equal(block.options.clients[1].companyName, '')
  assert.deepEqual(block.options.agencies.map((item) => item.name), ['Kerala Recruiters', 'Tamil Agents'])
  assert.deepEqual(block.options.recruiters, [])
  assert.equal(model.jobUnavailableReason(noor.jobs[1]), 'Filled')
})

test('without a way to work the candidate nothing is editable; managers and admins also pick the recruiter', () => {
  const form = model.normalizeForm(base())
  const staff = fallback.completeAssignment(form.assignment, { ...workspace(), canEdit: false })
  assert.deepEqual(staff.can, { job: false, source: false, agency: false, recruiter: false })
  const manager = fallback.completeAssignment(form.assignment, { ...workspace(), canEdit: false, me: { id: 'u2', role: 'manager' } })
  assert.deepEqual(manager.can, { job: true, source: true, agency: true, recruiter: true })
  assert.deepEqual(manager.options.recruiters.map((item) => item.name), ['Meena', 'Ravi'])
})

test('a server that sends the block keeps its own values, options and permissions', () => {
  const served = model.normalizeForm(v2()).assignment
  const completed = fallback.completeAssignment(served, { ...workspace(), canEdit: false })
  assert.deepEqual(completed.can, served.can)
  for (const client of served.options.clients) assert.deepEqual(completed.options.clients.find(item => item.id === client.id), client)
  const partial = model.normalizeForm(v2({ assignment: { client: { id: 'clx', name: 'Served Client' }, job: null, source: { key: 'email' }, agency: null, recruiter: null } })).assignment
  assert.equal(partial.supported, true)
  assert.equal(partial.serverOptions, false)
  const filled = fallback.completeAssignment(partial, workspace())
  assert.equal(filled.client.name, 'Served Client')
  assert.equal(filled.source.key, 'email')
  assert.equal(filled.options.clients.length, 2)
  assert.equal(filled.can.job, true)
})

test('active clients and all labelled workspace jobs stay visible when an older server omits them, without reopening unavailable jobs', () => {
  const served = model.normalizeForm(v2()).assignment
  served.options.clients = []
  const completed = fallback.completeAssignment(served, workspace())
  assert.deepEqual(completed.options.clients.map(client => client.id), ['cl1', 'cl2'])
  assert.deepEqual(completed.options.clients.flatMap(client => client.jobs.map(job => job.orderId)), ['o1', 'o2', 'o3'])
  assert.equal(model.jobUnavailableReason(completed.options.clients[0].jobs[1]), 'Filled')
  assert.equal(model.jobUnavailableReason(completed.options.clients[1].jobs[0]), 'On hold')
  served.can.job = false
  assert.equal(fallback.completeAssignment(served, workspace()), served)
})

/* ------------------------------------------------------------------ The paper form and the screen around it */
const paper = await import(moduleUrl((await readFile(new URL('../src/features/application-form/paper-model.ts', import.meta.url), 'utf8')).replace("'./form-model'", `'${modelUrl}'`)))

test('the lines under the letterhead show only what is known and follow the live assignment', () => {
  const form = model.normalizeForm(v2())
  assert.deepEqual(paper.identLines(form.assignment).map((item) => [item.label, item.value]),
    [['Client', 'Al Noor Staffing'], ['Job', 'Welder · ORD-1'], ['Source', 'Direct'], ['Agency', 'Kerala Recruiters']])
  // No agency, no client: they are left out, never printed as a dash or "Not set".
  const sparse = { ...form.assignment, client: null, agency: null }
  assert.deepEqual(paper.identLines(sparse).map((item) => item.key), ['job', 'source'])
  // Nothing known except the source: the strip is not printed at all.
  assert.deepEqual(paper.identLines({ ...sparse, job: null }), [])
  assert.deepEqual(paper.identLines(undefined), [])
  for (const item of paper.identLines(sparse)) assert.ok(!/^(–|-|Not set)$/.test(item.value))
  // A job picked a moment ago (optimistic) shows with its client at once, before the server answers.
  const picked = model.applyAssignmentChange(form.assignment, { orderId: 'o4' })
  const lines = paper.identLines(picked)
  assert.equal(lines.find((item) => item.key === 'client').value, 'Doha Projects')
  assert.equal(lines.find((item) => item.key === 'job').value, 'Electrician · ORD-4')
  // A title or a reference alone is enough.
  assert.equal(paper.jobText({ title: '', ref: 'ORD-9' }), 'ORD-9')
  assert.equal(paper.jobText({ title: 'Fitter', ref: '' }), 'Fitter')
})

test('section 6 rows are Client, Job, Country, Source, Agency, Recruiter; empty rows are left out', () => {
  const form = model.normalizeForm(v2())
  const rows = paper.recruitmentRows(form.assignment, form.recruitment, form.application?.orderId)
  assert.deepEqual(rows.map((row) => [row.label, row.value]),
    [['Client', 'Al Noor Staffing'], ['Job', 'Welder · ORD-1'], ['Country', 'UAE'], ['Source', 'Direct'], ['Agency', 'Kerala Recruiters'], ['Recruiter', 'Ravi']])
  assert.ok(!rows.some((row) => row.label === 'Position'))
  const without = paper.recruitmentRows({ ...form.assignment, agency: null, recruiter: null, client: null }, form.recruitment, 'o1')
  assert.deepEqual(without.map((row) => row.label), ['Job', 'Country', 'Source'])
  // A job that was only just picked has no country yet: the old job's country is not shown for it.
  const picked = model.applyAssignmentChange(form.assignment, { orderId: 'o4' })
  assert.ok(!paper.recruitmentRows(picked, form.recruitment, 'o1').some((row) => row.label === 'Country'))
  // Nothing known at all: no rows, and the page prints the single muted line.
  const empty = { ...form.assignment, client: null, job: null, agency: null, recruiter: null, source: { key: '', label: '' } }
  assert.deepEqual(paper.recruitmentRows(empty, form.recruitment, ''), [])
  assert.equal(paper.RECRUITMENT_EMPTY_TEXT, 'Not matched to a job yet')
  assert.equal(model.RECRUITMENT_FIELDS.find((field) => field.key === 'position').label, 'Job')
})

test('the paper form lists the documents received, from the live list, and never prints a missing one', () => {
  const form = model.normalizeForm(v2())
  assert.deepEqual(paper.receivedFromForm(form), [])
  const uploaded = model.applyOptimisticUploads(form.documents, { passport: { fileName: 'passport.pdf', sizeKb: 80 }, other: { fileName: 'letter.pdf', sizeKb: 20 } })
  const received = paper.documentsReceived(uploaded, form.documentOrder)
  assert.deepEqual(received.map((item) => [item.label, item.fileNames]), [['Passport', ['passport.pdf']], ['Other documents', ['letter.pdf']]])
  // Required but missing (resume), later (medical) and optional documents are simply absent.
  assert.ok(!received.some((item) => ['resume', 'medical', 'google_review'].includes(item.kind)))
  // Several files of a multiple slot are all named, newest first.
  const many = { ...uploaded, other: { ...uploaded.other, files: [{ fileName: 'b.pdf' }, { fileName: 'a.pdf' }] } }
  assert.deepEqual(paper.documentsReceived(many, form.documentOrder).find((item) => item.kind === 'other').fileNames, ['b.pdf', 'a.pdf'])
})

test('the view opens on Fill details for people who can work the candidate and on Form view for everyone else', () => {
  assert.equal(paper.defaultView(true), 'fill')
  assert.equal(paper.defaultView(false), 'form')
  assert.deepEqual(paper.FORM_VIEWS.map((item) => item.label), ['Fill details', 'Form view'])
})

test('the completeness ring shows one label, and the documents line reads as a sentence', () => {
  assert.equal(paper.ringLabel(5), '5%')
  assert.equal(paper.ringLabel(100), '100%')
  assert.equal(paper.ringLabel(4.6), '5%')
  assert.equal(paper.ringLabel(-3), '0%')
  assert.equal(paper.ringLabel(140), '100%')
  assert.equal(paper.ringLabel(NaN), '0%')
  assert.equal(paper.documentsProgressText({ uploaded: 5, total: 8 }), '5 of 8 required documents')
  assert.equal(paper.documentsProgressText({ uploaded: 0, total: 0 }), 'No documents are required yet')
  const form = model.normalizeForm(v2())
  const progress = model.requiredDocumentProgress(form.documents)
  assert.equal(paper.documentsProgressText(progress), `0 of ${progress.total} required documents`)
})

/* ------------------------------------------------------------------ The documents list: one compact row each, never locked */
const rowsSource = await readFile(new URL('../src/features/application-form/document-rows.ts', import.meta.url), 'utf8')
const rows = await import(moduleUrl(rowsSource.replace("'@/shared/model/document-slots'", `'${slotsUrl}'`)))
const slotsModule = await import(slotsUrl)
const stateOf = (key, hasFile = false, canWork = true) => rows.documentRowState({ entry: slotsModule.documentSlot(key), hasFile, canWork })

test('optional application-form evidence stays accessible without a pending row or completeness requirement', () => {
  const served = v2()
  served.documents.application_form.required = true // A response from the older server.
  const empty = model.normalizeForm(served)
  assert.equal(empty.documents.application_form.required, false)
  assert.equal(rows.documentRowStates(empty, true).some(({ slot }) => slot.slot === 'application_form'), false)
  const before = model.completenessOf(model.toDraft(empty), empty.documents)
  served.documents.application_form = slot('application_form', { required: true, uploaded: true, current: doc('saved-form.pdf') })
  const saved = model.normalizeForm(served)
  const evidence = rows.documentRowStates(saved, true).find(({ slot }) => slot.slot === 'application_form')
  assert.equal(evidence.slot.current.fileName, 'saved-form.pdf')
  assert.equal(evidence.state.counted, false)
  assert.equal(evidence.state.chip, 'Uploaded')
  assert.deepEqual(model.completenessOf(model.toDraft(saved), saved.documents), before)
})

test('no document is ever locked: each is Pending, Optional or Uploaded, and can always be uploaded', () => {
  for (const key of slotsModule.DOCUMENT_SLOT_KEYS) {
    const state = stateOf(key)
    assert.ok(['pending', 'optional'].includes(state.key), key)
    assert.ok(['Pending', 'Optional'].includes(state.chip), key)
    assert.equal(state.disabled, false, key)
    assert.ok(!('locked' in state) && !('tooltip' in state) && !/^(un)?lock$/.test(state.icon), key)
  }
  for (const key of ['resume', 'passport', 'medical', 'signed_offer', 'pcc', 'visa', 'air_ticket', 'undertaking_letter', 'emigration', 'insurance', 'invoice']) {
    assert.deepEqual([stateOf(key).key, stateOf(key).chip, stateOf(key).icon], ['pending', 'Pending', 'clock'], key)
  }
  for (const key of ['application_form', 'aadhaar', 'photo', 'education_certificate', 'experience_certificate', 'google_review', 'visa_stamping', 'other']) assert.deepEqual([stateOf(key).key, stateOf(key).chip], ['optional', 'Optional'], key)
  const done = stateOf('air_ticket', true)
  assert.deepEqual([done.key, done.chip, done.icon], ['uploaded', 'Uploaded', 'check'])
  assert.equal(stateOf('other', true).key, 'uploaded')
})

test('people who cannot work the candidate cannot upload', () => {
  assert.equal(stateOf('resume', false, false).disabled, true)
  assert.equal(stateOf('resume', true, false).disabled, true)
  assert.equal(stateOf('resume', false, true).disabled, false)
})

test('the count is every document that is not optional', () => {
  const form = model.normalizeForm(v2())
  const at = (uploads = {}) => {
    const documents = model.applyOptimisticUploads(form.documents, uploads)
    return rows.documentRowsProgress(rows.documentRowStates({ documents, documentOrder: form.documentOrder }, true).map((row) => row.state))
  }
  assert.deepEqual(at(), { uploaded: 0, total: 11, percent: 0 })
  assert.equal(at({ passport: { fileName: 'p.pdf', sizeKb: 1 }, air_ticket: { fileName: 'a.pdf', sizeKb: 1 }, other: { fileName: 'o.pdf', sizeKb: 1 } }).uploaded, 2)
  assert.equal(rows.documentsCountText({ uploaded: 5, total: 8 }), '5 of 8 documents')
})

test('the shared labels always win: the photo is "Photo" even when an older server says otherwise', () => {
  const served = v2()
  served.documents.photo.label = 'Passport size photo'
  served.documents.passport.label = 'Passport copy'
  const form = model.normalizeForm(served)
  assert.equal(form.documents.photo.label, 'Photo')
  assert.equal(form.documents.passport.label, 'Passport')
  assert.ok(!form.optionalDocuments.some((item) => /passport size/i.test(item.label)))
  const incomplete = model.completenessOf(model.toDraft(form), form.documents)
  assert.ok(!incomplete.missing.some((item) => item.key === 'documents.photo'))
  assert.ok(!JSON.stringify(incomplete).match(/passport size/i))
})

/* ------------------------------------------------------------------ Process tracking: the stages are the part that locks */
const stagesSource = await readFile(new URL('../src/features/application-form/process-stages.ts', import.meta.url), 'utf8')
const stages = await import(moduleUrl(stagesSource.replace("'./form-model'", `'${modelUrl}'`).replace("'@/shared/model/document-slots'", `'${slotsUrl}'`)))
const trackingRows = (done, current) => Array.from({ length: 11 }, (_, index) => ({
  key: `s${index + 1}`, label: `Stage ${index + 1}`, systemLabel: '', stage: index + 1, extra: false, tracked: true,
  status: index + 1 <= done ? 'completed' : index + 1 === current ? 'in_progress' : 'not_started',
  date: index + 1 <= done ? `2026-10-${String(index + 1).padStart(2, '0')}` : '', completedBy: index + 1 <= done ? 'Ravi' : '', outcome: '', attempt: 1, note: '', current: index + 1 === current,
}))
const stageForm = (stage, extra = {}) => {
  const form = model.normalizeForm(v2({ application: { id: 'a1', orderId: 'o1', orderRef: 'ORD-1', status: 'active', currentStage: stage, position: 'Welder', country: 'UAE' } }))
  return { ...form, tracking: { summary: { ...form.tracking.summary, currentStage: stage }, rows: trackingRows(stage - 1, stage) }, ...extra }
}

test('with no job all eleven stages are locked and nothing can be opened', () => {
  const form = { ...model.normalizeForm(v2()), application: null }
  const list = stages.stageRows({ ...form, tracking: { summary: {}, rows: [] } }, true)
  assert.equal(list.length, 11)
  assert.ok(list.every((row) => row.key === 'locked' && row.icon === 'lock' && row.chip === 'Locked' && !row.openable && row.date === ''))
  assert.equal(stages.PROCESS_EMPTY_LINE, 'Choose a job at the top to start the process.')
})

test('the lock opens by itself as the candidate reaches each stage: done, current, then locked', () => {
  for (const stage of [1, 5, 9, 11]) {
    const list = stages.stageRows(stageForm(stage), true)
    list.forEach((row) => {
      const expected = row.stage < stage ? 'done' : row.stage === stage ? 'current' : 'locked'
      assert.equal(row.key, expected, `stage ${stage}, row ${row.stage}`)
    })
    const now = list[stage - 1]
    assert.deepEqual([now.icon, now.chip], ['unlock', 'In progress'])
    if (stage > 1) assert.deepEqual([list[0].icon, list[0].chip, list[0].date], ['check', 'Completed', '2026-10-01'])
    if (stage < 11) assert.deepEqual([list[10].icon, list[10].chip], ['lock', 'Locked'])
  }
  // The same candidate a moment later, one stage on: the next row opens, nothing else changes.
  assert.equal(stages.stageRows(stageForm(5), true)[5].key, 'locked')
  assert.equal(stages.stageRows(stageForm(6), true)[5].key, 'current')
  assert.equal(stages.stageRows(stageForm(6), true)[4].key, 'done')
  // Clicking opens the stage form only for people who can work the candidate.
  assert.equal(stages.stageRows(stageForm(5), false).some((row) => row.openable), false)
  assert.equal(stages.stageRows(stageForm(5), true).every((row) => row.openable), true)
  // A failed stage is not shown as reached.
  const failed = stageForm(3)
  failed.tracking.rows[1].status = 'failed'
  assert.deepEqual([stages.stageRows(failed, true)[1].key, stages.stageRows(failed, true)[1].chip], ['failed', 'Failed'])
})

test('each stage lists the documents that belong to it, with their live status', () => {
  const form = stageForm(6)
  form.documents = model.applyOptimisticUploads(form.documents, { medical: { fileName: 'm.pdf', sizeKb: 1 }, google_review: { fileName: 'g.png', sizeKb: 1 } })
  const list = stages.stageRows(form, true)
  const docs = (stage) => list[stage - 1].documents.map((item) => [item.label, item.uploaded])
  assert.deepEqual(docs(5), [['Signed offer', false]])
  assert.deepEqual(docs(6), [['Medical', true]])
  assert.deepEqual(docs(7), [['Police clearance certificate (PCC)', false]])
  assert.deepEqual(docs(9), [['Visa', false]])
  assert.deepEqual(docs(10), [['Emigration', false]])
  assert.deepEqual(docs(11), [['Air ticket', false], ['Undertaking letter', false], ['Insurance', false], ['Invoice', false]])
  for (const stage of [1, 2, 3, 4, 8]) assert.deepEqual(docs(stage), [])
})

test('stage cards retain saved dates for stages omitted from the printed form', () => {
  const form = stageForm(9)
  for (const stage of [3, 4, 7, 8]) form.tracking.rows[stage - 1].extra = true
  const rows = stages.stageRows(form, true)
  for (const stage of [3, 4, 7, 8]) {
    assert.equal(rows[stage - 1].key, 'done')
    assert.equal(rows[stage - 1].date, `2026-10-${String(stage).padStart(2, '0')}`)
    assert.equal(rows[stage - 1].completedBy, 'Ravi')
  }
})

test('already deployed candidates show all stages complete without inventing missing completion dates', () => {
  const form = stageForm(11)
  form.application.status = 'deployed'
  form.tracking.rows = []
  const rows = stages.stageRows(form, true)
  assert.equal(stages.stagesDoneText(rows), '11 of 11 stages completed')
  assert.ok(rows.every((row) => row.date === '' && row.completedBy === ''))
})

test('the last stage says whether the candidate is ready to deploy; an older server says nothing', () => {
  assert.deepEqual(stages.deploymentText({ ready: true, missing: [] }), { text: 'Ready to deploy', tone: 'ready' })
  assert.deepEqual(stages.deploymentText({ ready: false, missing: [{ slot: 'medical', label: 'Medical' }, { slot: 'visa', label: 'Visa' }] }), { text: 'Needs: Medical, Visa', tone: 'needs' })
  assert.equal(stages.deploymentText(undefined), null)
  const withBlock = model.normalizeForm(v2({ deployment: { ready: false, missing: [{ slot: 'medical', label: 'Medical report' }, { slot: 'visa' }] } }))
  assert.deepEqual(withBlock.deployment.missing.map((item) => item.label), ['Medical', 'Visa'])
  const list = stages.stageRows({ ...stageForm(11), deployment: withBlock.deployment }, true)
  assert.equal(list[10].readiness.text, 'Needs: Medical, Visa')
  assert.ok(list.slice(0, 10).every((row) => row.readiness === null))
  assert.equal(stages.stageRows(stageForm(11), true)[10].readiness, null)
  assert.equal(model.normalizeForm(v2()).deployment, undefined)
  assert.equal(stages.stagesDoneText(stages.stageRows(stageForm(4), true)), '3 of 11 stages completed')
})

/* ------------------------------------------------------------------ The paper form: the seven sections of the official form, nothing more */
test('the paper form has exactly seven numbered sections and nothing after them', async () => {
  assert.deepEqual(paper.paperSections().map((item) => [item.no, item.title]), [
    [1, 'Personal details'], [2, 'Passport details'], [3, 'Educational details'], [4, 'Previous experience'],
    [5, model.SECTION_TITLES[5]], [6, 'Client and recruitment details'], [7, 'Recruitment process tracking'],
  ])
  const sheet = await readFile(new URL('../src/features/application-form/components/form-sheet.tsx', import.meta.url), 'utf8')
  const used = [...sheet.matchAll(/<([A-Za-z]+Section)\b/g)].map((match) => match[1])
  assert.deepEqual(used, ['PersonalSection', 'PassportSection', 'EducationSection', 'ExperienceSection', 'ReceivedDocumentsSection', 'RecruitmentSection', 'TrackingSection'])
  assert.ok(!/Assessment|FollowUp|extras/i.test(sheet))
  const sections = await readFile(new URL('../src/features/application-form/components/sections.tsx', import.meta.url), 'utf8')
  assert.deepEqual([...sections.matchAll(/<Section no=\{(\d)\}/g)].map((match) => Number(match[1])), [1, 2, 3, 4, 5, 6])
  // None of the removed fields is printed.
  assert.ok(!/whatsapp|personal\.email|familyMemberNumber|nativePlace|sourceDetail|Source details/i.test(sections))
})

test('the side panel jumps and the missing items use the same seven sections, and removed extras never show as missing', () => {
  assert.deepEqual(paper.paperSections().map((item) => item.no), [1, 2, 3, 4, 5, 6, 7])
  for (const no of [1, 2, 3, 4, 5, 6, 7]) assert.ok(paper.sectionTargetId(no).startsWith('af-'))
  // The sections keep the paper form's numbers on the page; the passport is section 2 there, not part of the documents.
  assert.deepEqual([1, 2, 3, 4, 5, 6, 7].map(paper.sectionTargetId), ['af-fill-section-1', 'af-fill-section-2', 'af-fill-section-3', 'af-fill-section-4', 'af-documents', 'af-assignment', 'af-process'])
  const listed = [
    { key: 'personal.candidateName', label: 'Candidate name', section: 1, kind: 'field', message: '' },
    { key: 'personal.email', label: 'Email address', section: 1, kind: 'field', message: '' },
    { key: 'personal.whatsapp', label: 'WhatsApp number', section: 1, kind: 'field', message: '' },
    { key: 'screening.nativePlace', label: 'Native place', section: 1, kind: 'field', message: '' },
    { key: 'education', label: 'Educational details', section: 3, kind: 'field', message: '' },
    { key: 'experience.history', label: 'Previous experience', section: 4, kind: 'field', message: '' },
    { key: 'documents.photo', label: 'Photo', section: 5, kind: 'document', message: '' },
  ]
  assert.deepEqual(paper.visibleMissing(listed).map((item) => item.key), ['personal.candidateName', 'education', 'experience.history', 'documents.photo'])
  // The percentage is worked out from the official fields and the documents only.
  const form = model.normalizeForm(v2())
  const result = model.completenessOf(model.toDraft(form), form.documents)
  assert.ok(result.missing.every((item) => paper.visibleMissing([item]).length === 1))
})

test('the Passport row is a normal document row: the passport details are not inside it', async () => {
  const docs = await readFile(new URL('../src/features/application-form/components/documents-section.tsx', import.meta.url), 'utf8')
  assert.ok(!/PassportDetails|passport-details|docrow-extra/.test(docs))
  const css = await readFile(new URL('../src/styles/application-form-v2.css', import.meta.url), 'utf8')
  assert.ok(!/docrow-extra/.test(css))
})

test('the working cards share one header with an icon chip and no section number', async () => {
  const read = (path) => readFile(new URL(path, import.meta.url), 'utf8')
  const headerOf = (source) => source.slice(source.indexOf('<SectionHeader'), source.indexOf('<SectionHeader') + 260)
  const cases = [
    ['../src/features/application-form/components/documents-section.tsx', 'title="Documents"', '<Files />'],
    ['../src/features/application-form/components/process-section.tsx', 'title="Recruitment process tracking"', '<Route />'],
    ['../src/screens/candidates/application-form.tsx', 'title="Client and recruitment details"', '<Building2 />'],
    ['../src/features/application-form/components/feedback-panel.tsx', 'title="Recruiter notes"', '<MessageSquareText />'],
  ]
  for (const [path, title, icon] of cases) {
    const header = headerOf(await read(path))
    assert.ok(header.includes(title) && header.includes(icon), path)
    assert.ok(!header.includes('number='), path)
  }
  const component = await read('../src/features/application-form/components/section-header.tsx')
  assert.ok(!/number|badge/i.test(component.replace(/section number/gi, '')))
  const css = await read('../src/styles/application-form.css')
  assert.ok(!/af-sh-badge/.test(css))
})

/* ------------------------------------------------------------------ The top bar: live title, subtitle, percentage */
test('the title follows the typed name; the placeholder and an empty name read as Unnamed candidate, muted', () => {
  assert.deepEqual(paper.candidateTitle('Asha Rao', 'Old Name'), { text: 'Asha Rao', muted: false })
  assert.deepEqual(paper.candidateTitle('  Asha R  ', ''), { text: 'Asha R', muted: false })
  // Typing replaces the saved name at once, and clearing the box does not bring the placeholder back.
  assert.deepEqual(paper.candidateTitle('', 'Old Name'), { text: 'Unnamed candidate', muted: true })
  assert.deepEqual(paper.candidateTitle('Name needs review', 'Old Name'), { text: 'Unnamed candidate', muted: true })
  assert.deepEqual(paper.candidateTitle('name NEEDS review', ''), { text: 'Unnamed candidate', muted: true })
  // Until the draft is there the saved name is used, unless it is the placeholder.
  assert.deepEqual(paper.candidateTitle(undefined, 'Saved Name'), { text: 'Saved Name', muted: false })
  assert.deepEqual(paper.candidateTitle(undefined, 'Name needs review'), { text: 'Unnamed candidate', muted: true })
  assert.deepEqual(paper.candidateTitle(undefined, undefined), { text: 'Unnamed candidate', muted: true })
})

test('the subtitle is the reference, the client and the job, with empty parts left out and no dashes', () => {
  const assignment = model.normalizeForm(v2()).assignment
  assert.equal(paper.candidateSubtitle('CND-01004', assignment), 'CND-01004 · Al Noor Staffing · Welder')
  assert.equal(paper.candidateSubtitle('CND-01004', { client: null, job: assignment.job }), 'CND-01004 · Welder')
  assert.equal(paper.candidateSubtitle('CND-01004', { client: assignment.client, job: null }), 'CND-01004 · Al Noor Staffing')
  assert.equal(paper.candidateSubtitle('', { client: null, job: { ...assignment.job, title: '', ref: 'ORD-9' } }), 'ORD-9')
  assert.equal(paper.candidateSubtitle('CND-01004', null), 'CND-01004')
  assert.equal(paper.candidateSubtitle(undefined, undefined), '')
  // A job picked a moment ago shows in the bar before the server answers.
  const picked = model.applyAssignmentChange(assignment, { orderId: 'o4' })
  assert.equal(paper.candidateSubtitle('CND-01004', picked), 'CND-01004 · Doha Projects · Electrician')
  assert.equal(paper.completeText(5), '5% complete')
  assert.equal(paper.completeText(100), '100% complete')
})

/* ------------------------------------------------------------------ Intake pointers are never an agency; long references are not shown */
test('an intake pointer in the source detail is never an agency, in the server block, the older server block, the fallback and the paper form', () => {
  const pointer = 'email_message:8a9c0b52-5d99-4c1e-9b0a-2f3d4e5f6a7b'
  for (const prefix of ['email_message:', 'whatsapp_message:', 'person_review:', 'lead_sheet:']) assert.equal(model.cleanAgencyName(`${prefix}12`), '')
  assert.equal(model.cleanAgencyName('  Kerala Recruiters '), 'Kerala Recruiters')
  assert.equal(model.cleanAgencyName(null), '')
  // A server that sends the pointer as the agency name.
  const served = model.normalizeForm(v2({ assignment: { ...v2().assignment, agency: { id: '', name: pointer } } }))
  assert.equal(served.assignment.agency, null)
  // An older server: the recruitment details hold the pointer.
  const older = model.normalizeForm(base({ recruitment: { ...base().recruitment, sourceDetail: pointer } }))
  assert.equal(older.assignment.agency, null)
  assert.equal(older.recruitment.sourceDetail, '')
  // The fallback completer reads the candidate's own source detail.
  const filled = fallback.completeAssignment(older.assignment, { ...workspace(), candidate: { source: 'email', sourceDetail: pointer, ownerId: null } })
  assert.equal(filled.agency, null)
  const typed = fallback.completeAssignment(older.assignment, { ...workspace(), candidate: { source: 'email', sourceDetail: 'Tamil Agents', ownerId: null } })
  assert.equal(typed.agency.name, 'Tamil Agents')
  // And the paper form omits it even if a pointer got through.
  const leaked = { ...model.normalizeForm(v2()).assignment, agency: { id: '', name: pointer } }
  assert.ok(!paper.identLines(leaked).some((line) => line.key === 'agency'))
  assert.ok(!paper.recruitmentRows(leaked, null, 'o1').some((line) => line.key === 'agency'))
})

test('import provenance stays out of legacy agency fallbacks while explicit referring agencies remain visible', () => {
  const older = model.normalizeForm(base({ recruitment: { ...base().recruitment, source: 'Other', sourceCode: 'other', sourceDetail: '  Bulk  import  ' } }))
  assert.equal(older.assignment.agency, null)
  assert.equal(older.recruitment.sourceDetail, '')
  const imported = { ...workspace(), candidate: { source: 'other', sourceDetail: 'Bulk import', ownerId: null } }
  assert.equal(fallback.completeAssignment(older.assignment, imported).agency, null)
  const referral = fallback.completeAssignment(older.assignment, { ...imported, candidate: { ...imported.candidate, source: 'referral', sourceDetail: 'Tamil Agents' } })
  assert.equal(referral.agency.name, 'Tamil Agents')
  const known = fallback.completeAssignment(older.assignment, { ...imported, agencies: [{ id: 'agency-1', agencyName: 'Bulk import' }] })
  assert.equal(known.agency.name, 'Bulk import')
  const explicit = model.normalizeForm(v2({ assignment: { ...v2().assignment, agency: { id: 'agency-1', name: 'Bulk import' } } }))
  assert.equal(explicit.assignment.agency.name, 'Bulk import')
})

test('the reference is shown only when it is a short readable code', () => {
  assert.equal(paper.readableReference('CND-01004'), 'CND-01004')
  assert.equal(paper.readableReference('CND-E-f238e22d89f44a5a8fd49f6be920a7a9'), '')
  assert.equal(paper.readableReference('12345678901234'), '12345678901234')
  assert.equal(paper.readableReference('123456789012345'), '')
  assert.equal(paper.readableReference(undefined), '')
  const assignment = model.normalizeForm(v2()).assignment
  assert.equal(paper.candidateSubtitle('CND-E-f238e22d89f44a5a8fd49f6be920a7a9', assignment), 'Al Noor Staffing · Welder')
})

test('the experience totals never produce a warning, a message or a blocking error', () => {
  assert.equal(model.experienceSplitHint, undefined)
  const form = model.normalizeForm(v2({ warnings: ['Experience in India plus experience abroad does not match the total experience.', 'Something else'], experience: { totalExperience: 5, experienceIndia: 1, experienceAbroad: 1, history: [] } }))
  assert.deepEqual(form.warnings, ['Something else'])
  const draft = model.toDraft(form)
  assert.deepEqual(Object.keys(model.validateDraft(draft)).filter((key) => key.startsWith('experience.')), [])
})

test('with several applications the Job list holds them: running ones first, the one on screen marked, no separate toolbar', async () => {
  const apps = [
    { id: 'a3', orderId: 'o3', orderRef: 'ORD-3', clientName: 'Doha Projects', position: 'Rigger', status: 'rejected', currentStage: 2 },
    { id: 'a2', orderId: 'o2', orderRef: 'ORD-2', clientName: 'Al Noor', position: 'Fitter', status: 'active', currentStage: 4 },
    { id: 'a1', orderId: 'o1', orderRef: 'ORD-1', clientName: 'Al Noor', position: 'Welder', status: 'active', currentStage: 5 },
  ]
  assert.deepEqual(paper.applicationOptions(apps, 'a1').map((item) => item.id), ['a1', 'a2', 'a3'])
  assert.deepEqual(paper.applicationOptions(apps, 'a2').map((item) => item.id), ['a2', 'a1', 'a3'])
  assert.equal(paper.applicationOptionText(apps[2], true), 'ORD-1 · Welder · Al Noor (shown now)')
  assert.equal(paper.applicationOptionText(apps[0], false), 'ORD-3 · Rigger · Doha Projects (rejected)')
  const screen = await readFile(new URL('../src/screens/candidates/application-form.tsx', import.meta.url), 'utf8')
  assert.ok(!/Job shown|af-toolbar/.test(screen))
  assert.ok(screen.includes('onSwitchApplication='))
})
