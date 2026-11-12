import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import ts from 'typescript'

function moduleUrl(source) {
  const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } })
  return `data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`
}
const read = (path) => readFile(new URL(`../src/${path}`, import.meta.url), 'utf8')

const slotsUrl = moduleUrl(await read('shared/model/document-slots.ts'))
const slots = await import(slotsUrl)
const model = await import(moduleUrl((await read('features/candidates/profile-documents-model.ts')).replace("'@/shared/model/document-slots'", `'${slotsUrl}'`)))

const file = (id, version, uploadedAt, name = `${id}.pdf`) => ({ id, name, version, uploadedAt, sizeKb: 10, url: `/api/files/${id}`, uploadedBy: 'u1' })
const ORDER = ['application_form', 'resume', 'passport', 'aadhaar', 'medical', 'signed_offer', 'pcc', 'visa', 'air_ticket', 'undertaking_letter', 'emigration', 'insurance', 'invoice', 'photo', 'education_certificate', 'experience_certificate', 'google_review', 'visa_stamping', 'other']

const EMPTY_ORDER = ORDER.filter(key => key !== 'application_form')

test('the shared list keeps eleven required documents and optional application/Aadhaar evidence in order', () => {
  assert.deepEqual(slots.DOCUMENT_SLOTS.filter(entry => slots.documentExpected(entry, 11)).map(entry => entry.slot), ['resume', 'passport', 'medical', 'signed_offer', 'pcc', 'visa', 'air_ticket', 'undertaking_letter', 'emigration', 'insurance', 'invoice'])
  assert.deepEqual(slots.DOCUMENT_SLOT_KEYS, ORDER)
  assert.equal(slots.documentLabel('photo'), 'Photo')
  assert.equal(slots.documentLabel('passport'), 'Passport')
  assert.equal(slots.documentLabel('pcc'), 'Police clearance certificate (PCC)')
  assert.equal(slots.documentLabel('other'), 'Other documents')
  assert.ok(slots.DOCUMENT_SLOTS.every((entry) => !/passport-size/i.test(entry.label)))
  // Every passport page is retained alongside the other multi-file documents.
  assert.deepEqual(slots.DOCUMENT_SLOTS.filter((entry) => entry.multiple).map((entry) => entry.slot), ['passport', 'aadhaar', 'other'])
})

test('the expected-by-stage rule follows the contract', () => {
  const signedOffer = slots.documentSlot('signed_offer')
  assert.equal(slots.documentExpected(signedOffer, 4), false)
  assert.equal(slots.documentExpected(signedOffer, 5), true)
  assert.equal(slots.documentExpected(signedOffer, null), false)
  // Always-expected documents count even with no application; optional ones never do without a stage.
  assert.equal(slots.documentExpected(slots.documentSlot('application_form'), null), false)
  assert.equal(slots.documentExpected(slots.documentSlot('photo'), null), false)
  assert.equal(slots.documentExpected(slots.documentSlot('emigration'), 9), false)
  assert.equal(slots.documentExpected(slots.documentSlot('emigration'), 10), true)
  for (const key of ['undertaking_letter', 'insurance', 'invoice']) {
    assert.equal(slots.documentExpected(slots.documentSlot(key), 10), false)
    assert.equal(slots.documentExpected(slots.documentSlot(key), 11), true)
  }
  assert.equal(slots.documentExpected(slots.documentSlot('other'), 11), false)
})

test('an empty candidate still lists every slot, in order, labelled from the shared list', () => {
  const rows = model.profileDocuments([])
  assert.deepEqual(rows.slice(0, EMPTY_ORDER.length).map((row) => row.key), EMPTY_ORDER)
  assert.ok(rows.slice(0, EMPTY_ORDER.length).every((row) => row.versions.length === 0))
  assert.equal(rows.find((row) => row.key === 'other').multiple, true)
  assert.equal(rows.find((row) => row.key === 'photo').multiple, false)
  // Aadhaar is now a shared multi-file slot; the empty optional application form stays hidden.
  assert.deepEqual(rows.slice(EMPTY_ORDER.length).map((row) => row.key), [])
  assert.deepEqual(model.profileDocuments(undefined).map((row) => row.key), rows.map((row) => row.key))
})

test('manually collected documents retain attribution with no synthetic file versions', () => {
  const manualStatus = { uploaded: true, by: { id: 'u1', name: 'Ravi' }, at: '2026-11-12T10:00:00Z', note: 'Received at the office' }
  const rows = model.profileDocuments([{ key: 'medical', label: 'Medical', versions: [], uploaded: true, fileUploaded: false, manualStatus }])
  assert.deepEqual(rows.find(row => row.key === 'medical').manualStatus, manualStatus)
  assert.deepEqual(rows.find(row => row.key === 'medical').versions, [])
  assert.equal(rows.find(row => row.key === 'passport').multiple, true)
})

test('server labels are replaced by the shared ones, files stay with their slot', () => {
  const rows = model.profileDocuments([
    { key: 'resume', label: 'Resume', versions: [file('r1', 1, '2026-10-01T09:00:00Z')] },
    { key: 'passport', label: 'Passport copy', versions: [] },
    { key: 'photo', label: 'Photograph', versions: [file('p1', 1, '2026-10-02T09:00:00Z', 'me.jpg')] },
    { key: 'pcc', label: 'Whatever the server says', versions: [file('c1', 1, '2026-10-03T09:00:00Z')] },
  ])
  const byKey = Object.fromEntries(rows.map((row) => [row.key, row]))
  assert.equal(byKey.passport.label, 'Passport')
  assert.equal(byKey.photo.label, 'Photo')
  assert.equal(byKey.pcc.label, 'Police clearance certificate (PCC)')
  assert.deepEqual(byKey.photo.versions.map((v) => v.id), ['p1'])
  assert.deepEqual(byKey.pcc.versions.map((v) => v.id), ['c1'])
})

test('files saved under the earlier certificate keys show in the matching new row', () => {
  const rows = model.profileDocuments([
    { key: 'education', label: 'Education certificates', versions: [file('old1', 1, '2026-09-01T09:00:00Z'), file('old2', 2, '2026-09-02T09:00:00Z')] },
    { key: 'experience', label: 'Experience certificates', versions: [file('x1', 1, '2026-09-03T09:00:00Z')] },
    { key: 'education_certificate', label: 'Education certificate', versions: [file('new1', 1, '2026-10-05T09:00:00Z')] },
  ])
  assert.equal(rows.some((row) => row.key === 'education' || row.key === 'experience'), false)
  const education = rows.find((row) => row.key === 'education_certificate')
  // Oldest first and renumbered, so the newest upload is the highest version even though the old key counted higher.
  assert.deepEqual(education.versions.map((v) => [v.id, v.version]), [['old1', 1], ['old2', 2], ['new1', 3]])
  assert.deepEqual(rows.find((row) => row.key === 'experience_certificate').versions.map((v) => [v.id, v.version]), [['x1', 1]])
})

test('a single history is passed through untouched', () => {
  const only = [file('a', 4, '2026-10-01T09:00:00Z')]
  assert.equal(model.mergeVersions(only, []), only)
  assert.equal(model.mergeVersions([], only), only)
})

test('other slots the server holds are listed after the shared ones only when they hold a file', () => {
  const rows = model.profileDocuments([
    { key: 'aadhaar', label: 'Aadhaar copy', versions: [file('a1', 1, '2026-10-01T09:00:00Z')] },
    { key: 'source_bundle', label: 'Source document evidence', versions: [] },
  ])
  assert.deepEqual(rows.slice(EMPTY_ORDER.length).map((row) => row.key), [])
  const withEvidence = model.profileDocuments([{ key: 'source_bundle', label: 'Source document evidence', versions: [file('s1', 1, '2026-10-01T09:00:00Z')] }])
  assert.deepEqual(withEvidence.slice(EMPTY_ORDER.length).map((row) => [row.key, row.label]), [['source_bundle', 'Source document evidence']])
})
