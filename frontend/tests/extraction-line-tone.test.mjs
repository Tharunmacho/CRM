import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import ts from 'typescript'

/**
 * The owner's rule: a document line in the Documents list never shows a yellow / amber alert. Only blocking states get a box
 * (red failed, blue reading, green filled); everything advisory is the quiet "Notes (n)" line. The tone is decided in ONE place
 * (`lineTone` in form-model.ts); these tests keep it that way. Synthetic records only.
 */
function moduleUrl(source) {
  const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } })
  return `data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`
}
const src = (path) => readFile(new URL(`../src/${path}`, import.meta.url), 'utf8')
const slotsUrl = moduleUrl(await src('shared/model/document-slots.ts'))
const readReportUrl = moduleUrl(await src('features/candidates/read-report.ts'))
const model = await import(moduleUrl((await src('features/application-form/form-model.ts'))
  .replace("'@/shared/model/document-slots'", `'${slotsUrl}'`).replace("'@/features/candidates/read-report'", `'${readReportUrl}'`)))

const PASSPORT_CHECK = 'Passport: please check the date of issue against the passport.'
const PAGE_SIX = 'Page 6 could not be fully read. Check page 6 in the original file.'
const NAME_UNCLEAR = 'The name on the card could not be read clearly (check it against the document).'
const reading = (over = {}) => ({ status: 'done', filled: [], kept: [], kindsFound: [], message: '', updatedAt: '', ...over })

test('every advisory reading is a note, never failed (and never a box)', () => {
  const table = {
    'page not fully read': reading({ status: 'needs_review', coverage: { complete: false, pages: { unresolved: [6] } } }),
    'advisory passport check': reading({ status: 'needs_review', coverage: { complete: false, issues: [PASSPORT_CHECK] } }),
    'name unclear': reading({ status: 'needs_review', coverage: { complete: false, issues: [NAME_UNCLEAR] } }),
    'partial coverage': reading({ coverage: { complete: false, pages: { not_sent: [{ page: 3, blocking: true }] }, issues: ['Review: the reader skipped a page.'] } }),
    'filing info': reading({ coverage: { complete: true, filing: ['Pages 7 and 8 were filed under Other documents.'] } }),
    'kept fields': reading({ kept: ['personal.dateOfBirth', 'personal.mobileNumber'] }),
    'needs review with only the reader message': reading({ status: 'needs_review', message: 'Some pages could not be read.' }),
    'needs review with no reason at all': reading({ status: 'needs_review' }),
    'passport cross-check difference': reading({ coverage: { complete: true, kinds: { passport: { crossCheck: { verified: [], conflicts: ['doi'] } } } } }),
  }
  for (const [name, extraction] of Object.entries(table)) {
    assert.equal(model.lineTone(extraction), 'note', name)
    const line = model.describeExtraction('resume', { uploaded: true, extraction })
    assert.equal(line.tone, 'note', name)
    assert.ok(line.notes.length > 0, `${name}: the information stays reachable as a note`)
  }
  assert.deepEqual(model.describeExtraction('resume', { uploaded: true, extraction: table['page not fully read'] }).notes, [PAGE_SIX])
  assert.deepEqual(model.describeExtraction('aadhaar', { uploaded: true, extraction: table['name unclear'] }).notes, [NAME_UNCLEAR])
  assert.deepEqual(model.describeExtraction('resume', { uploaded: true, extraction: table['needs review with only the reader message'] }).notes, ['Some pages could not be read.'])
  assert.match(model.describeExtraction('resume', { uploaded: true, extraction: table['needs review with no reason at all'] }).notes[0], /^Some pages could not be read/)
  // Fields were filled: the green line stays, the advice goes under it as a note. Still not an error.
  const filledWithNotes = reading({ filled: ['personal.fatherName'], coverage: { complete: false, pages: { unresolved: [6] }, issues: [PASSPORT_CHECK] } })
  assert.equal(model.lineTone(filledWithNotes), 'filled')
  assert.equal(model.describeExtraction('resume', { uploaded: true, extraction: filledWithNotes }).text, 'Filled 1 field on the form')
  // A plain successful reading has no notes at all.
  assert.deepEqual(model.describeExtraction('resume', { uploaded: true, extraction: reading({ filled: ['a', 'b'] }) }).notes, [])
  assert.equal(model.lineTone(reading()), 'filled')
})

test('only blocking states are failed: wrong person, duplicate, reader failure, nothing could be read', () => {
  assert.equal(model.lineTone(reading({ status: 'needs_review', identityMismatch: true })), 'failed')
  assert.equal(model.lineTone(reading({ status: 'done', identityMismatch: true, duplicateOf: { candidateId: 'x', careId: 'CI-1', name: 'A' } })), 'failed')
  assert.equal(model.lineTone(reading({ status: 'failed' })), 'failed')
  assert.equal(model.lineTone(reading({ status: 'unreadable', message: 'No resume pages were found.' })), 'failed')
  assert.equal(model.lineTone(reading({ status: 'needs_review', failure: { errorClass: 'timeout', retriable: true, detail: 'x' } })), 'failed')
  assert.equal(model.lineTone(reading({ status: 'processing' })), 'running')
  assert.equal(model.lineTone(null), 'none')
  assert.equal(model.lineTone(undefined), 'none')
  assert.equal(model.describeExtraction('photo', { uploaded: true, extraction: reading({ filled: ['a'] }) }).tone, 'none')
  // A rejected document and a failed reading carry no notes in their box.
  assert.deepEqual(model.extractionNotes(reading({ identityMismatch: true, coverage: { complete: false, issues: [PASSPORT_CHECK] } })), [])
})

test('the tone model has no amber tone at all', async () => {
  const tones = new Set()
  const samples = [null, reading(), reading({ filled: ['a'] }), reading({ status: 'processing' }), reading({ status: 'failed' }), reading({ status: 'needs_review' }),
    reading({ identityMismatch: true }), reading({ kept: ['personal.email'] })]
  for (const extraction of samples) tones.add(model.lineTone(extraction))
  assert.deepEqual([...tones].sort(), ['failed', 'filled', 'none', 'note', 'running'])
})

test('a note that names another kind shows on that kind line only, once', () => {
  const resume = reading({ status: 'needs_review', coverage: { complete: false, pages: { unresolved: [6] }, issues: [PASSPORT_CHECK], kinds: { passport: { crossCheck: { conflicts: ['doi'] } } } } })
  const passportFile = { attachmentId: 'p1', fileName: 'passport.pdf', extraction: reading({ status: 'needs_review', coverage: { complete: false, issues: [PASSPORT_CHECK] } }) }
  const aadhaarFile = { attachmentId: 'a1', fileName: 'aadhaar.pdf', extraction: reading({ status: 'needs_review', coverage: { complete: false, issues: [NAME_UNCLEAR] } }) }
  const documents = {
    resume: { slot: 'resume', uploaded: true, multiple: false, files: [], extraction: resume },
    passport: { slot: 'passport', uploaded: true, multiple: true, files: [passportFile], extraction: passportFile.extraction },
    aadhaar: { slot: 'aadhaar', uploaded: true, multiple: true, files: [aadhaarFile], extraction: aadhaarFile.extraction },
  }
  const notes = model.routeLineNotes(documents)
  assert.deepEqual(notes.resume, [PAGE_SIX])                                       // nothing about the passport on the resume line
  assert.deepEqual([...notes['passport:p1']].sort(), [PASSPORT_CHECK, 'Passport: date of issue — please check it against the passport.'].sort())   // each once, whichever line it came from
  assert.deepEqual(notes['aadhaar:a1'], [NAME_UNCLEAR])
  // Without a passport line to show it on, the note stays where it was found: nothing is hidden.
  const alone = model.routeLineNotes({ resume: documents.resume })
  assert.ok(alone.resume.includes(PASSPORT_CHECK))
  // The same sentence twice on one line is shown once.
  const twice = reading({ status: 'needs_review', coverage: { complete: false, issues: [NAME_UNCLEAR, NAME_UNCLEAR] }, message: NAME_UNCLEAR })
  assert.deepEqual(model.routeLineNotes({ aadhaar: { slot: 'aadhaar', uploaded: true, multiple: false, files: [], extraction: twice } }).aadhaar, [NAME_UNCLEAR])
})

test('static scan: the extraction line (component and CSS) holds no amber / yellow / warning token', async () => {
  const FORBIDDEN = /warn|amber|yellow|orange|#f59e0b|#fbbf24|#d97706|#fef3c7|#fde68a|#b45309|TriangleAlert|AlertTriangle/i
  const component = await src('features/application-form/components/extraction-line.tsx')
  assert.doesNotMatch(component, FORBIDDEN)
  assert.doesNotMatch(await src('styles/application-form-extract.css'), FORBIDDEN)
  // Any other stylesheet rule that styles the extraction line or its notes is held to the same rule.
  for (const file of ['application-form.css', 'application-form-v2.css', 'application-form-fill.css', 'application-form-aadhaar.css']) {
    const lines = (await src(`styles/${file}`)).split('\n').filter((line) => /\.af-(extract|xnotes)/.test(line.split('{')[0]))
    for (const line of lines) assert.doesNotMatch(line, FORBIDDEN, `${file}: ${line.trim()}`)
  }
  // The component asks the model for the tone and never works one out itself.
  assert.match(component, /describeExtraction\(/)
  assert.doesNotMatch(component, /needs_review|\.status\b|incompleteReadingText|'review'|'reading'|'done'/)
  const model_ = await src('features/application-form/form-model.ts')
  assert.match(model_, /const tone = lineTone\(extraction\)/)
  // Tone words only ever come out of lineTone: the union has no amber member.
  assert.match(model_, /export type LineTone = 'failed' \| 'running' \| 'filled' \| 'note' \| 'none'/)
})
