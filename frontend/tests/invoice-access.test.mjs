import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import ts from 'typescript'

/**
 * Invoices, the agreed service fee, CGST/SGST and receipts are the admin's alone (capability `view_invoices`).
 * The server leaves them out for a recruiter or manager; these tests check the screens stay in step with that.
 */
function moduleUrl(source) {
  const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } })
  return `data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`
}
const read = (path) => readFile(new URL(`../src/${path}`, import.meta.url), 'utf8')

const slotsUrl = moduleUrl(await read('shared/model/document-slots.ts'))
const { DOCUMENT_SLOT_KEYS } = await import(slotsUrl)
const modelUrl = moduleUrl((await read('features/application-form/form-model.ts')).replace("'@/shared/model/document-slots'", `'${slotsUrl}'`))
const model = await import(modelUrl)
const stages = await import(moduleUrl((await read('features/application-form/process-stages.ts')).replace("'./form-model'", `'${modelUrl}'`).replace("'@/shared/model/document-slots'", `'${slotsUrl}'`)))
const profile = await import(moduleUrl((await read('features/candidates/profile-documents-model.ts')).replace("'@/shared/model/document-slots'", `'${slotsUrl}'`)))
const tabs = await import(moduleUrl(await read('features/reports/report-tabs.ts')))

test('only the admin holds the view_invoices capability', async () => {
  const store = await read('shared/state/crm-store.tsx')
  assert.match(store, /\| 'view_invoices'/)
  assert.match(store, /view_invoices: \['admin'\],/)
})

test('the application form renders the Invoice card (fee, CGST/SGST, payments, PDF) only behind view_invoices', async () => {
  const form = await read('screens/candidates/application-form.tsx')
  assert.match(form, /store\.can\('view_invoices'\) && <InvoiceCard /)
  assert.equal([...form.matchAll(/<InvoiceCard/g)].length, 1)
})

test('profile and stage documents render through a list that hides the Invoice row without the capability', async () => {
  const list = await read('features/candidates/components/profile-documents.tsx')
  assert.match(list, /can\('view_invoices'\)/)
  assert.match(list, /profileDocuments\(documents, \{ invoices \}\)/)
})

test('Fee collections is not offered, and not opened by a link, unless the viewer may see invoices', async () => {
  const page = await read('screens/reports/reports.tsx')
  assert.match(page, /const invoices = store\.can\('view_invoices'\)/)
  assert.match(page, /reportTabFromParams\(params, supervisor, invoices\)/)
  assert.match(page, /REPORTS\.filter\(item => invoices \|\| item\.key !== 'collections'\)/)
  // A manager is a supervisor but still no admin: no tab, no mention in the page description.
  assert.doesNotMatch(page, /supervisor \? 'Recruitment progress, team activity and candidate fee collections/)
  const query = (text) => new URLSearchParams(text)
  assert.equal(tabs.reportTabFromParams(query('tab=collections'), true, true), 'collections')
  for (const [supervisor, invoices] of [[true, false], [false, false]]) {
    assert.equal(tabs.reportTabFromParams(query('tab=collections'), supervisor, invoices), 'funnel')
  }
  assert.equal(tabs.reportTabFromParams(query('tab=collections'), true), 'funnel')
})

test('the profile Documents card has no Invoice row for anyone but the admin, and no gap where it was', () => {
  const asAdmin = profile.profileDocuments([]).map((row) => row.key)
  assert.ok(asAdmin.includes('invoice'))
  const others = profile.profileDocuments([], { invoices: false }).map((row) => row.key)
  assert.deepEqual(others, asAdmin.filter((key) => key !== 'invoice'))
  // Even a stored invoice file (the server never sends it to them) is not listed as an extra row.
  const stray = profile.profileDocuments([{ key: 'invoice', label: 'Invoice', versions: [{ id: 'i1', name: 'Invoice.pdf', version: 1, uploadedAt: '2026-10-01T09:00:00Z', sizeKb: 1, url: '/api/files/i1', uploadedBy: 'u1' }] }], { invoices: false })
  assert.equal(stray.some((row) => row.key === 'invoice'), false)
  assert.equal(profile.profileDocuments(undefined, { invoices: true }).some((row) => row.key === 'invoice'), true)
})

const slot = (key) => ({ slot: key, uploaded: false, current: null, files: [] })
const payload = (withInvoice) => {
  const keys = DOCUMENT_SLOT_KEYS.filter((key) => withInvoice || key !== 'invoice')
  return {
    candidate: { id: 'c1', ref: 'CND-1', name: 'Asha Rao', canEdit: true },
    application: { id: 'a1', orderId: 'o1', orderRef: 'ORD-1', status: 'active', currentStage: 11, position: 'Welder', country: 'UAE' },
    personal: { candidateName: 'Asha Rao' }, passport: {}, education: [], experience: { history: [] }, recruitment: {},
    tracking: { summary: {}, rows: [] },
    documentOrder: keys,
    documents: Object.fromEntries(keys.map((key) => [key, { ...slot(key), required: key === 'resume', requiredFromStage: key === 'invoice' ? 11 : null }])),
  }
}

test('a form payload without the invoice slot yields no Invoice document, checklist item or stage-11 row', () => {
  const form = model.normalizeForm(payload(false))
  assert.equal('invoice' in form.documents, false)
  assert.equal(form.documentOrder.includes('invoice'), false)
  const later = stages.stageRows({ ...form, tracking: { summary: { currentStage: 11 }, rows: [] } }, true)[10].documents.map((item) => item.slot)
  assert.deepEqual(later, ['air_ticket', 'undertaking_letter', 'insurance'])
  const missing = model.completenessOf(model.toDraft(form), form.documents).missing.map((item) => item.key)
  assert.equal(missing.includes('documents.invoice'), false)
})

test('the admin still gets the Invoice document and its place at stage 11', () => {
  const form = model.normalizeForm(payload(true))
  assert.equal('invoice' in form.documents, true)
  const later = stages.stageRows({ ...form, tracking: { summary: { currentStage: 11 }, rows: [] } }, true)[10].documents.map((item) => item.slot)
  assert.deepEqual(later, ['air_ticket', 'undertaking_letter', 'insurance', 'invoice'])
})
