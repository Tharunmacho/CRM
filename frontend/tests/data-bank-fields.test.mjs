import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'

function moduleUrl(source) {
  const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText
  return `data:text/javascript;base64,${Buffer.from(js).toString('base64')}`
}

async function load(path, imports = {}) {
  let source = await readFile(new URL(path, import.meta.url), 'utf8')
  for (const [from, url] of Object.entries(imports)) source = source.replaceAll(`'${from}'`, `'${url}'`)
  const url = moduleUrl(source)
  return { url, module: await import(url) }
}

const slotsUrl = moduleUrl(await readFile(new URL('../src/shared/model/document-slots.ts', import.meta.url), 'utf8'))
const documentsModule = await load('../src/features/candidates/data-bank-documents.ts', { '@/shared/model/document-slots': slotsUrl })
const fields = (await load('../src/features/candidates/data-bank-fields.ts', { './data-bank-documents': documentsModule.url })).module
const { documentsOnFile } = documentsModule.module

const context = {
  ownerName: (id) => (id === 'u1' ? 'Asha Rao' : ''),
  agent: (c) => c.sourceDetail ?? '',
  statusLabel: (c) => (c.status === 'in_process' ? 'In process' : c.status),
  sourceLabel: (s) => ({ email: 'Email', walk_in: 'Walk-in' })[s] ?? s,
  optionLabel: (category, value) => `${category}:${value}`,
  application: (id) => (id === 'c1' ? { stage: 'Interview', order: 'ORD-7' } : null),
}

const candidate = {
  id: 'c1', ref: 'CND-1', name: 'Test Person', phone: '+910000000001', whatsapp: '+910000000002', email: 'test@example.test',
  dob: '1990-01-05', gender: 'Male', nationality: 'Indian', currentCity: 'Chennai', languages: ['English', 'Tamil'],
  passportNo: 'X0000000', passportIssue: '2020-02-03', passportExpiry: '2030-02-02', passportPlace: 'Chennai',
  designations: ['Cook', 'Helper'], totalExperience: 5, gulfExperience: 1, experienceIndia: 4, skills: ['Grill', 'Prep'], expectedSalary: 150000,
  fatherName: 'Test Father', maritalStatus: 'single', currentAddress: '1 Test Street', nativePlace: 'Madurai', familyMemberNumber: '+910000000003',
  jobDurationPreference: 'long', bestTimeToContact: 'evening', countryPreference: 'Qatar', serviceChargesStatus: 'agreed', interestLevel: 'high',
  nextFollowUpDate: '2026-11-20', source: 'email', sourceDetail: 'Agent One', status: 'in_process', ownerId: 'u1', addedAt: '2026-11-01T10:00:00Z',
  documents: [{ key: 'resume', label: 'Resume', versions: [{}] }, { key: 'passport', label: 'Passport', versions: [] }],
  callOutcome: { called: true, interested: 'yes', remarks: '', recordedAt: '2026-11-02T10:00:00Z' },
}

test('the registry covers every candidate field the list API returns', async () => {
  const server = await readFile(new URL('../../backend/app/api/schemas.py', import.meta.url), 'utf8')
  const block = server.slice(server.indexOf('class CandidateOut(Wire):'), server.indexOf('class CandidateWithFormOut'))
  const covered = {
    id: null, care_id: null, owner_id: 'owner', phone: 'phone', whatsapp: 'whatsapp', email: 'email', dob: 'dob', gender: 'gender', nationality: 'nationality',
    current_city: 'currentCity', languages: 'languages', passport_no: 'passportNo', passport_issue: 'passportIssue', passport_expiry: 'passportExpiry',
    passport_place: 'passportPlace', designations: 'designations', total_experience: 'totalExperience', gulf_experience: 'abroadExperience', skills: 'skills',
    expected_salary: 'expectedSalary', father_name: 'fatherName', marital_status: 'maritalStatus', current_address: 'currentAddress',
    experience_india: 'indiaExperience', native_place: 'nativePlace', family_member_number: 'familyNumber', job_duration_preference: 'jobDuration',
    country_preference: 'countryPreference', best_time_to_contact: 'bestTime', service_charges_status: 'serviceCharges', interest_level: 'interestLevel',
    next_follow_up_date: 'followUp', source: 'source', source_detail: 'agent', status: 'status', needs_review: 'status', added_at: 'added',
    resume_text: null, photo_tone: null, documents: 'documents', call_outcome: 'callOutcome', ref: 'ref', name: 'name',
  }
  const apiFields = [...block.matchAll(/^    (\w+):/gm)].map((match) => match[1])
  assert.ok(apiFields.length > 30)
  for (const name of apiFields) {
    assert.ok(name in covered, `CandidateOut.${name} has no entry in this test's map: add it to data-bank-fields.ts first`)
    if (covered[name]) assert.ok(fields.bankFieldDef(covered[name]), `no registry field "${covered[name]}" for ${name}`)
  }
})

test('field ids and headings are unique, and every group is a known group', () => {
  const ids = fields.BANK_FIELD_DEFS.map((field) => field.id)
  const labels = fields.BANK_FIELD_DEFS.map((field) => field.label)
  assert.equal(new Set(ids).size, ids.length)
  assert.equal(new Set(labels).size, labels.length)
  for (const field of fields.BANK_FIELD_DEFS) assert.ok(fields.BANK_GROUPS.includes(field.group), field.id)
  for (const id of fields.DEFAULT_COLUMN_FIELDS) assert.ok(fields.bankFieldDef(id), `default column field "${id}" is not in the registry`)
})

test('every field of a full candidate is written, with the exact headings the PRD uses', () => {
  const rows = Object.fromEntries(fields.bankFieldRows(candidate, context).map((row) => [row.key, row]))
  assert.equal(rows.dob.value, '05 Jan 1990')
  assert.equal(rows.gender.value, 'Male')
  assert.equal(rows.passportExpiry.value, '02 Feb 2030')
  assert.equal(rows.fatherName.label, "Father's name")
  assert.equal(rows.fatherName.value, 'Test Father')
  assert.equal(rows.languages.value, 'English, Tamil')
  assert.equal(rows.totalExperience.value, '5 yrs')
  assert.equal(rows.abroadExperience.value, '1 yr')
  assert.equal(rows.indiaExperience.value, '4 yrs')
  assert.equal(rows.expectedSalary.value, '1,50,000')
  assert.equal(rows.owner.value, 'Asha Rao')
  assert.equal(rows.agent.value, 'Agent One')
  assert.equal(rows.source.value, 'Email')
  assert.equal(rows.stage.value, 'Interview')
  assert.equal(rows.order.value, 'ORD-7')
  assert.equal(rows.status.value, 'In process')
  assert.equal(rows.serviceCharges.value, 'service_charges_status:agreed')
  assert.equal(rows.interestLevel.value, 'interest_level:high')
  assert.equal(rows.bestTime.value, 'best_time_to_contact:evening')
  assert.equal(rows.followUp.value, '20 Nov 2026')
  assert.equal(rows.documents.value, 'Resume')
  assert.equal(rows.callOutcome.value, 'Interested')
  for (const row of Object.values(rows)) if (!row.large) assert.notEqual(row.value, '', `${row.key} is empty for a full candidate`)
})

test('a sparse candidate leaves every field listed but empty (the row shows a dash)', () => {
  const sparse = { id: 'c2', ref: 'CND-2', name: 'Only Name', phone: '+910000000009', source: 'walk_in', status: 'new', ownerId: null, designations: [], languages: [], skills: [], totalExperience: 0, gulfExperience: 0, documents: [] }
  const rows = fields.bankFieldRows(sparse, context)
  assert.equal(rows.length, fields.BANK_FIELD_DEFS.length)
  const filled = rows.filter((row) => row.value).map((row) => row.key).sort()
  assert.deepEqual(filled, ['name', 'phone', 'ref', 'source', 'status'])
})

test('education and work history are never values of the list: they are pointers to the profile', () => {
  const rows = fields.bankFieldRows({ ...candidate, education: [{ level: 'secret' }], workHistory: [{ employer: 'secret' }] }, context)
  const large = rows.filter((row) => row.large).map((row) => row.key)
  assert.deepEqual(large, ['education', 'workHistory'])
  assert.ok(rows.every((row) => !row.value.includes('secret')))
  assert.ok(fields.BANK_FIELD_DEFS.filter((field) => !field.inColumns).every((field) => field.width === 0))
})

test('a date-only value is the same calendar day in every time zone', () => {
  assert.equal(fields.dayLabel('1999-12-31'), '31 Dec 1999')
  assert.equal(fields.dayLabel('not a date'), '')
  assert.equal(fields.dayLabel(null), '')
  assert.equal(fields.yearsText(0), '')
  assert.equal(fields.yearsText(1), '1 yr')
})

test('the list API does not load the deferred education and work history', async () => {
  const server = await readFile(new URL('../../backend/app/api/schemas.py', import.meta.url), 'utf8')
  const list = server.slice(server.indexOf('class CandidateOut(Wire):'), server.indexOf('class CandidateWithFormOut'))
  assert.ok(!/\beducation\b|\bwork_history\b/.test(list.replace(/#.*$/gm, '')))
})

test('documents on file use the shared labels in the shared order, folding the old certificate keys', () => {
  const file = [{}]
  const held = documentsOnFile([
    { key: 'other', label: 'Other', versions: file },
    { key: 'experience', label: 'Experience', versions: file },
    { key: 'photo', label: 'Photograph', versions: file },
    { key: 'passport', label: 'Passport copy', versions: file },
    { key: 'education', label: 'Education', versions: file },
    { key: 'resume', label: 'Resume', versions: [] },
    { key: 'pcc', label: 'PCC', versions: file },
    { key: 'aadhaar', label: 'Aadhaar copy', versions: file },
  ])
  assert.deepEqual(held, ['Passport', 'Aadhaar copy', 'Police clearance certificate (PCC)', 'Photo', 'Education certificate', 'Experience certificate', 'Other documents'])
  assert.deepEqual(documentsOnFile([{ key: 'education_certificate', versions: file }, { key: 'education', versions: file }]), ['Education certificate'])
  assert.deepEqual(documentsOnFile(undefined), [])
  assert.deepEqual(documentsOnFile([]), [])
})

test('the documents column writes the shared labels', () => {
  const held = { ...candidate, documents: [{ key: 'photo', label: 'Photograph', versions: [{}] }, { key: 'passport', label: 'Passport copy', versions: [{}] }] }
  const rows = Object.fromEntries(fields.bankFieldRows(held, context).map((row) => [row.key, row]))
  assert.equal(rows.documents.value, 'Passport, Photo')
})
