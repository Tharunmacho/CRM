import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import ts from 'typescript'
import { withRoleLabels } from './fixtures/role-labels-url.mjs'

function moduleUrl(source) {
  const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } })
  return `data:text/javascript;base64,${Buffer.from(withRoleLabels(outputText)).toString('base64')}`
}
const formSource = (path) => readFile(new URL(`../src/features/application-form/${path}`, import.meta.url), 'utf8')

const slotsUrl = moduleUrl(await readFile(new URL('../src/shared/model/document-slots.ts', import.meta.url), 'utf8'))
const readReportUrl = moduleUrl(await readFile(new URL('../src/features/candidates/read-report.ts', import.meta.url), 'utf8'))
const modelUrl = moduleUrl((await formSource('form-model.ts')).replace("'@/shared/model/document-slots'", `'${slotsUrl}'`).replace("'@/features/candidates/read-report'", `'${readReportUrl}'`))
const model = await import(modelUrl)

const reading = (status, extra = {}) => ({ status, filled: [], kept: [], kindsFound: [], message: '', updatedAt: '', ...extra })
const stored = (id, extra = {}) => ({ attachmentId: id, fileName: `${id}.pdf`, url: `/api/files/${id}`, ...extra })
const form = (documents) => model.normalizeForm({
  candidate: { id: 'c1', ref: 'CND-1', name: 'Asha Rao', canEdit: true },
  personal: { candidateName: 'Asha Rao' }, passport: {}, education: [], experience: { history: [] },
  recruitment: {}, tracking: { summary: {}, rows: [] }, documents,
})

test('a stored file that was never read is the one the form starts reading by itself; read, failed and rejected files are not', () => {
  const resume = form({ resume: { uploaded: true, current: stored('r1', { extractionPending: true }), extraction: null } }).documents.resume
  assert.deepEqual(model.slotReadState(resume), { unread: ['r1'], failed: [] })
  const passport = form({ passport: { uploaded: true, multiple: true, current: stored('p1'), files: [
    stored('p1', { extractionPending: true }),                                        // never read
    stored('p2', { extractionPending: true, extraction: reading('failed') }),          // failed: Read again, never an automatic retry
    stored('p3', { extractionPending: false, extraction: reading('done') }),           // read
    stored('p4', { extractionPending: true, identityMismatch: true }),                 // another person's: needs a different document
    { attachmentId: 'p5', fileName: 'no-url.pdf', url: '', extractionPending: true },  // nothing stored to read
  ] } }).documents.passport
  assert.deepEqual(model.slotReadState(passport), { unread: ['p1'], failed: ['p2'] })
  const failed = form({ resume: { uploaded: true, current: stored('r2', { extractionPending: true }), extraction: reading('failed') } }).documents.resume
  assert.deepEqual(model.slotReadState(failed), { unread: [], failed: ['r2'] })
  const running = form({ resume: { uploaded: true, current: stored('r3', { extractionPending: true }), extraction: reading('processing') } }).documents.resume
  assert.deepEqual(model.slotReadState(running), { unread: [], failed: [] })
  assert.deepEqual(model.slotReadState(undefined), { unread: [], failed: [] })
})

test('unreadFileIds lists each unread file of the form once, across slots, at most 100', () => {
  const mixed = form({
    resume: { uploaded: true, current: stored('r1', { extractionPending: true }) },
    passport: { uploaded: true, multiple: true, current: stored('p1'), files: [stored('p1', { extractionPending: true }), stored('p2', { extractionPending: true })] },
    aadhaar: { uploaded: true, multiple: true, current: stored('a1'), files: [stored('a1', { extractionPending: true, extraction: reading('processing') })] },
    education_certificate: { uploaded: true, current: stored('e1', { extractionPending: true }), extraction: reading('failed') },
  })
  assert.deepEqual(model.unreadFileIds(mixed).sort(), ['p1', 'p2', 'r1'])
  const many = form({ passport: { uploaded: true, multiple: true, current: stored('f0'), files: Array.from({ length: 130 }, (_, index) => stored(`f${index}`, { extractionPending: true })) } })
  assert.equal(model.unreadFileIds(many).length, 100, 'one request takes at most 100 files')
  assert.deepEqual(model.unreadFileIds(form({})), [])
})

test('a failed reading says what happens next, calmly, and keeps the reason for Details', () => {
  const failure = (extra) => ({ errorClass: 'timeout', retriable: true, detail: 'The reader took too long.', ...extra })
  const text = (extra) => model.describeExtraction('resume', { uploaded: true, extraction: reading('failed', extra) }).text
  assert.equal(text({ failure: failure({ retriable: false }) }), 'We could not read this file. Upload a clearer copy, or fill the details here.')
  assert.equal(text({ failure: failure() }), 'Reading did not finish. Choose Read again, or fill the details here.')
  assert.equal(text({ failure: failure({ retryAt: '2026-11-18T10:00:00Z' }) }), 'Reading did not finish. We will try again by ourselves, or you can fill the details here.')
  assert.equal(text({ message: 'Upstream said 500' }), 'Reading did not finish. Choose Read again, or fill the details here.')
  const unreadable = model.describeExtraction('resume', { uploaded: true, extraction: reading('unreadable', { message: 'No pages' }) })
  assert.equal(unreadable.text, 'We could not read this file. Upload a clearer copy, or fill the details here.')
  assert.doesNotMatch(text({ failure: failure({ detail: 'secret detail', httpStatus: 500 }) }), /secret detail|500|timeout/)
  assert.deepEqual(model.failureDetails({ message: 'Upstream said 500' }), ['Upstream said 500'])
  assert.deepEqual(model.failureDetails({ message: '' }), [])
  assert.match(model.failureDetails({ failure: failure(), message: 'ignored' }).join(' '), /Reason: timeout/)
})

/* The wiring of "nobody presses Extract details", read from the source because the hook and the components need React. */

test('the form has no Extract details button, no "Add all pages, then extract" note and no "a aadhaar"', async () => {
  for (const path of ['components/documents-section.tsx', 'components/slot-reading.tsx', 'components/extraction-line.tsx', 'fill/passport-upload.tsx', 'fill/fill-view.tsx', 'form-model.ts']) {
    assert.doesNotMatch(await formSource(path), /Extract details|extract details|then extract|before extracting|DocumentExtractionAction|\ba aadhaar/i, path)
  }
  assert.doesNotMatch(await readFile(new URL('../src/shared/model/document-slots.ts', import.meta.url), 'utf8'), /extract the details/)
})

test('both document panels say "Reading documents…" for an unread file and offer Read again only after a failure', async () => {
  const note = await formSource('components/slot-reading.tsx')
  assert.match(note, /Reading documents…/)
  assert.match(note, /slotReadState\(slot\)\.unread/)
  const row = await formSource('components/documents-section.tsx')
  assert.match(row, /<SlotReadingNote slot=\{slot\} enabled=\{canUpload && Boolean\(onExtract\)\}/)
  assert.match(row, /failedReads\.includes\(attachmentId\)/, 'Read again only for a file whose reading failed')
  assert.match(await formSource('fill/passport-upload.tsx'), /<SlotReadingNote slot=\{slot\} enabled=\{canWork && Boolean\(onExtract\)\}/)
  assert.match(await formSource('components/extraction-line.tsx'), /!wrongDocument && line\.tone === 'failed' && onReadAgain/)
})

test('the form asks for a reading once per file: after uploads settle, only when it may edit, and never again after a failure', async () => {
  const hook = await formSource('use-application-form.ts')
  assert.match(hook, /unreadFileIds\(form\)/)
  assert.match(hook, /canReadByItself = Boolean\(form\?\.candidate\.canEdit && form\.capabilities\?\.deferredDocumentReading\)/)
  assert.match(hook, /requestedReads = useRef\(new Set<string>\(\)\)/)
  assert.match(hook, /if \(!canReadByItself \|\| uploading\) return/, 'waits while files are being uploaded')
  assert.match(hook, /\.filter\(\(id\) => !requestedReads\.current\.has\(id\)\)/, 'a file already asked for is not asked for again')
  assert.match(hook, /ids\.forEach\(\(id\) => requestedReads\.current\.add\(id\)\)\s*\n\s*void extractDocuments\(ids\)\.catch\(/, 'marked before the request, so a failure does not loop')
  assert.match(hook, /return \(\) => window\.clearTimeout\(timer\)/, 'a repeated effect (StrictMode) cancels the pending timer instead of sending twice')
  assert.match(hook, /const reading = form \? isReading\(form\) \|\| waitingToRead : false/, 'unread files count as reading, so the page polls until the fields appear')
})

test('opening the form asks the server once to read what was never read and file the other pages of a bundle', async () => {
  const hook = await formSource('use-application-form.ts')
  const api = await formSource('form-api.ts')
  assert.match(api, /\/read-pending/)
  assert.doesNotMatch(api, /defer_extraction/, 'uploads are read at once; nothing is held back for a button')
  assert.match(hook, /visited\.current === candidateId/, 'once per visit')
  assert.match(hook, /answer\.more/, 'a capped answer is asked again')
})
