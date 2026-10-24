import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import ts from 'typescript'

async function loadSource(path) {
  const source = await readFile(new URL(path, import.meta.url), 'utf8')
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
  })
  return import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`)
}

const view = await loadSource('../src/features/candidates/ocr-view.ts')
const {
  humanizeKey, pageList, confidenceLabel, isoDateLabel, maskAadhaarNumber, maskAadhaarText, aadhaarNumberView,
  documentEntries, extraEntries, pageViews, inspectionView, buildTree, countValues, aadhaarMasker,
  normalizeOcr, primaryDocument, humanDate,
} = view

const flat = (nodes, prefix = '') => nodes.flatMap((n) =>
  n.kind === 'value' ? [`${prefix}${n.label}=${n.text}`] : flat(n.children, `${prefix}${n.label}/`))

test('keys are humanised with acronyms and units kept intact', () => {
  assert.equal(humanizeKey('processing_time_ms'), 'Processing time (ms)')
  assert.equal(humanizeKey('mrz_source'), 'MRZ source')
  assert.equal(humanizeKey('all_check_digits_valid'), 'All check digits valid')
  assert.equal(humanizeKey('source_sha256'), 'Source SHA-256')
  assert.equal(humanizeKey('masked_aadhaar_number'), 'Masked Aadhaar number')
  assert.equal(humanizeKey('date_of_birth'), 'Date of birth')
  assert.equal(humanizeKey('givenName'), 'Given name')
  assert.equal(humanizeKey('vid'), 'VID')
})

test('page lists collapse runs and confidence reads as a percentage', () => {
  assert.equal(pageList([8, 3, 4, 5, 4]), '3–5, 8')
  assert.equal(pageList([2]), '2')
  assert.equal(pageList(undefined), '')
  assert.equal(confidenceLabel(0.923), '92.3%')
  assert.equal(confidenceLabel(1), '100%')
  assert.equal(confidenceLabel(undefined), '')
  assert.equal(isoDateLabel('2030-03-02'), '2 Mar 2030')
  assert.equal(isoDateLabel('March 2030'), 'March 2030')
})

test('Aadhaar numbers are masked for viewers who may not see them, and shown in full to those who may', () => {
  assert.equal(maskAadhaarNumber('1234 5678 9012'), 'XXXX XXXX 9012')
  assert.equal(maskAadhaarNumber('XXXX XXXX 9012'), 'XXXX XXXX 9012')
  assert.equal(maskAadhaarNumber(''), '')
  assert.equal(maskAadhaarText('No: 1234 5678 9012, mob 9876543210'), 'No: XXXX XXXX 9012, mob 9876543210')
  assert.equal(maskAadhaarText('VID 1234 5678 9012 3456'), 'VID XXXX XXXX XXXX 3456')
  assert.equal(maskAadhaarText('123456789012'), 'XXXXXXXX9012')

  const stored = { aadhaar_number: '123456789012', masked_aadhaar_number: 'XXXX XXXX 9012' }
  assert.deepEqual(aadhaarNumberView(stored, false), { masked: 'XXXX XXXX 9012', full: '' })
  assert.deepEqual(aadhaarNumberView(stored, true), { masked: 'XXXX XXXX 9012', full: '123456789012' })
  assert.deepEqual(aadhaarNumberView({ aadhaar_number: '123456789012' }, false), { masked: 'XXXX XXXX 9012', full: '' })
  assert.deepEqual(aadhaarNumberView(null, true), { masked: '', full: '' })
})

test('a hand-entered candidate has no document evidence to render', () => {
  assert.deepEqual(documentEntries({}), [])
  assert.deepEqual(extraEntries({}), [])
  assert.deepEqual(documentEntries(null), [])
  assert.deepEqual(documentEntries({ resume: {}, passport: null }), [])
})

test('primary and additional documents are listed in reading order, unknown data is kept as extras', () => {
  const ocr = {
    aadhaar: { aadhaar: { name: 'A' } },
    resume: { name: 'R' },
    additional_documents: [{ passport: { mrz: { surname: 'S' } } }, { resume: { name: 'Second' } }],
    person_split: { group: 'one' },
    future_field: [1, 2],
  }
  const docs = documentEntries(ocr)
  assert.deepEqual(docs.map((d) => d.id), ['resume', 'aadhaar', 'additional-0-passport', 'additional-1-resume'])
  assert.equal(docs[2].label, 'Passport (additional document 1)')
  const extras = extraEntries(ocr)
  assert.deepEqual(extras.map((e) => e.id), ['person_split', 'future_field'])
  assert.equal(extras[0].label, 'How the documents were split')
})

test('page views keep the original page number and every page text, whitespace included', () => {
  const pages = pageViews({
    pages: [
      { page_number: 4, provider_page_number: 1, text: 'Line one\n  indented\n', language: 'eng', average_confidence: 0.8765 },
      { page_number: 6, provider_page_number: 2, text: 'Second' },
    ],
  })
  assert.equal(pages.length, 2)
  assert.equal(pages[0].page, 4)
  assert.equal(pages[0].sent, 1)
  assert.equal(pages[0].text, 'Line one\n  indented\n')
  assert.equal(pages[0].confidence, '87.7%')
  assert.equal(pages[1].page, 6)
  assert.deepEqual(pageViews({}), [])
})

test('the local classification names the identified, unread and unknown pages', () => {
  const v = inspectionView({
    local_inspection: {
      source_format: 'pdf', page_count: 4, complete: false,
      pages: [
        { page_number: 1, status: 'read', document_type: 'resume', method: 'native_text+tesseract', confidence: 0.9, reason: '' },
        { page_number: 2, status: 'unread', document_type: 'unknown', method: 'failed', confidence: 0, reason: 'Blurred' },
      ],
      resume_pages: [1], passport_pages: [], aadhaar_pages: [3, 4], unread_pages: [2], unknown_pages: [],
      selected_pages: [1], warnings: ['One or more pages could not be read.'],
    },
  })
  assert.equal(v.rows.length, 2)
  assert.equal(v.rows[0].type, 'Resume')
  assert.equal(v.rows[0].method, 'Native text and tesseract')
  assert.equal(v.rows[1].reason, 'Blurred')
  assert.deepEqual(v.groups.map((g) => [g.label, g.pages]), [
    ['Identified as resume', '1'],
    ['Identified as Aadhaar', '3–4'],
    ['Sent to the reader for this document', '1'],
    ['Pages that could not be read', '2'],
  ])
  assert.deepEqual(v.warnings, ['One or more pages could not be read.'])
  assert.equal(inspectionView({ pages: [] }), null)
})

test('the value tree keeps every returned value, nests objects, lists arrays and skips only page markup', () => {
  const payload = {
    request_id: 'req-1',
    mrz: { surname: 'KHAN', all_check_digits_valid: false, individual_check_digits: { composite: false, passport_number: null } },
    warnings: ['one', 'two'],
    fields: [{ label: 'Place of birth', value: 'Delhi', page: 2, confidence: 0.5 }],
    pages: [{ page_number: 2, text: 'A\nB', html: '<p>A</p>', language: 'eng' }],
    empty: [], blank: '', none: null, zero: 0,
  }
  assert.deepEqual(flat(buildTree(payload)), [
    'Request ID=req-1',
    'MRZ/Surname=KHAN',
    'MRZ/All check digits valid=No',
    'MRZ/Individual check digits/Composite=No',
    'Warnings/#1=one',
    'Warnings/#2=two',
    'Fields/#1/Label=Place of birth',
    'Fields/#1/Value=Delhi',
    'Fields/#1/Page=2',
    'Fields/#1/Confidence=0.5',
    'Pages/#1/Page number=2',
    'Pages/#1/Text=A\nB',
    'Pages/#1/Language=eng',
    'Zero=0',
  ])
  const withEmpty = flat(buildTree({ none: null, list: [] }, { includeEmpty: true }))
  assert.deepEqual(withEmpty, ['None=—'])
  assert.equal(countValues(buildTree(payload)), 14)
})

test('html is skipped only inside pages, and binary-like blobs are replaced by a size note', () => {
  const tree = flat(buildTree({ html: 'kept at top level', nested: { pages: [{ html: 'x', text: 't' }] }, blob: 'A'.repeat(5000) }))
  assert.ok(tree.includes('HTML=kept at top level'))
  assert.ok(!tree.some((l) => l.includes('=x')))
  assert.ok(tree.some((l) => l.startsWith('Blob=[binary data omitted · 5,000 characters')))
})

test('the Aadhaar tree mask hides the number everywhere it appears, including page text', () => {
  const payload = {
    aadhaar: { aadhaar_number: '123456789012', masked_aadhaar_number: 'XXXX XXXX 9012', vid: '1234567890123456' },
    pages: [{ page_number: 1, text: 'Your Aadhaar No.: 1234 5678 9012\nVID: 1234 5678 9012 3456' }],
  }
  const text = flat(buildTree(payload, { transform: aadhaarMasker() })).join('\n')
  assert.ok(!text.includes('1234 5678 9012'))
  assert.ok(!text.includes('123456789012'))
  assert.ok(!text.includes('1234567890123456'))
  assert.ok(text.includes('Aadhaar number=XXXX XXXX 9012'))
  assert.ok(text.includes('VID=XXXXXXXXXXXX3456'))
  // Unmasked for a viewer who may see it.
  assert.ok(flat(buildTree(payload)).join('\n').includes('Aadhaar number=123456789012'))
})

const inspection = (selected) => ({
  source_format: 'pdf', page_count: 6, complete: true,
  pages: [{ page_number: 3, status: 'read', document_type: 'resume', method: 'native_text', confidence: 0.9, reason: '' }],
  resume_pages: selected, selected_pages: selected,
})

const currentShape = () => ({
  resume: {
    name: 'Asha Rao', request_id: 'req-r',
    pages: [
      { page_number: 1, text: 'first sent page', language: 'eng', average_confidence: 0.9 },
      { page_number: 2, text: 'second sent page' },
    ],
  },
  passport: { mrz: { surname: 'RAO' }, pages: [{ page_number: 1, text: 'passport page' }] },
  _sources: {
    resume: {
      source_file: { channel: 'email', message_id: 'm-1', attachment_id: 'a-1', file_name: 'bundle.pdf', selected_pages: [3, 5] },
      local_inspection: inspection([3, 5]),
      source_sha256: 'abc123',
      selected_pages: [3, 5],
    },
    passport: { source_file: { channel: 'email', file_name: 'bundle.pdf' }, selected_pages: [6] },
  },
  additional_documents: [{
    resume: { name: 'Second', pages: [{ page_number: 1, text: 'extra' }] },
    _source: { source_file: { channel: 'whatsapp', file_name: 'second.pdf' }, selected_pages: [2], source_sha256: 'def' },
  }],
})

const legacyShape = () => ({
  resume: {
    name: 'Asha Rao', request_id: 'req-r',
    source_file: { channel: 'email', file_name: 'old.pdf' },
    source_sha256: 'old-hash',
    local_inspection: inspection([3, 5]),
    pages: [
      { page_number: 3, provider_page_number: 1, text: 'first sent page' },
      { page_number: 5, provider_page_number: 2, text: 'second sent page' },
    ],
  },
})

test('the current shape maps Veris page numbers back to the original pages using the stored selection', () => {
  const [resume, passport, extra] = normalizeOcr(currentShape())
  assert.deepEqual([resume.id, passport.id, extra.id], ['resume', 'passport', 'additional-0-resume'])
  assert.equal(resume.shape, 'current')
  assert.equal(resume.source.fileName, 'bundle.pdf')
  assert.equal(resume.source.channel, 'email')
  assert.equal(resume.source.messageId, 'm-1')
  assert.equal(resume.source.attachmentId, 'a-1')
  assert.equal(resume.source.sha256, 'abc123')
  assert.deepEqual(resume.source.selectedPages, [3, 5])
  assert.deepEqual(resume.pagesView.map((p) => [p.page, p.sent, p.text]), [[3, 1, 'first sent page'], [5, 2, 'second sent page']])
  assert.equal(resume.inspection.rows.length, 1)
  assert.deepEqual(resume.inspection.groups.map((g) => g.pages), ['3, 5', '3, 5'])
  assert.deepEqual(passport.pagesView.map((p) => p.page), [6])
  // The payload stays the verbatim Veris response, free of provenance keys.
  assert.equal(resume.payload.source_file, undefined)
  assert.equal(resume.payload.local_inspection, undefined)
  // Additional documents carry their own `_source`, and `_source` is not mistaken for a document kind.
  assert.equal(extra.kind, 'resume')
  assert.equal(extra.additional, true)
  assert.equal(extra.source.fileName, 'second.pdf')
  assert.deepEqual(extra.pagesView.map((p) => p.page), [2])
  assert.equal(documentEntries(currentShape()).length, 3)
})

test('the current shape shows provenance in the complete-data tree and is not reported as an unknown key', () => {
  const [resume] = normalizeOcr(currentShape())
  const lines = flat(buildTree(resume.treeValue))
  assert.ok(lines.includes('Source/Source SHA-256=abc123'))
  assert.ok(lines.includes('Source/Source file/File name=bundle.pdf'))
  assert.deepEqual(extraEntries(currentShape()), [])
  // Provenance whose document is missing is still surfaced.
  const orphan = { _sources: { passport: { source_sha256: 'zz' } } }
  assert.deepEqual(extraEntries(orphan).map((e) => e.id), ['source-passport'])
})

test('a legacy row keeps its provenance inside the payload and its page numbers are already original', () => {
  const [resume] = normalizeOcr(legacyShape())
  assert.equal(resume.shape, 'legacy')
  assert.equal(resume.source.fileName, 'old.pdf')
  assert.equal(resume.source.channel, 'email')
  assert.equal(resume.source.sha256, 'old-hash')
  assert.deepEqual(resume.source.selectedPages, [3, 5])
  assert.deepEqual(resume.pagesView.map((p) => [p.page, p.sent]), [[3, 1], [5, 2]])
  assert.equal(resume.inspection.sourceFormat, 'pdf')
  assert.equal(resume.treeValue, resume.payload)
  assert.deepEqual(extraEntries(legacyShape()), [])
  assert.equal(primaryDocument(legacyShape(), 'resume').payload.name, 'Asha Rao')
  assert.equal(primaryDocument(legacyShape(), 'passport'), undefined)
  assert.equal(primaryDocument(currentShape(), 'resume').payload.name, 'Asha Rao')
})

test('a string source_file and a missing selection are tolerated', () => {
  const [doc] = normalizeOcr({ resume: { name: 'X', source_file: 'cv.docx', pages: [{ page_number: 2, text: 't' }] }, _sources: { resume: { source_file: 'cv.docx' } } })
  assert.equal(doc.source.fileName, 'cv.docx')
  // No stored selection: the number is kept as given.
  assert.deepEqual(doc.pagesView.map((p) => [p.page, p.sent]), [[2, undefined]])
  assert.deepEqual(normalizeOcr(null), [])
  assert.deepEqual(normalizeOcr({ _sources: { resume: {} } }), [])
})

test('free-text dates read as "25 May 1976" and anything unclear is shown as written', () => {
  assert.equal(humanDate('25th May 1976'), '25 May 1976')
  assert.equal(humanDate('1 September 1990'), '1 Sep 1990')
  assert.equal(humanDate('May 25, 1976'), '25 May 1976')
  assert.equal(humanDate('25/05/1976'), '25 May 1976')
  assert.equal(humanDate('25-05-1976'), '25 May 1976')
  assert.equal(humanDate('25-Sept-1976'), '25 Sep 1976')
  assert.equal(humanDate('1976-05-25'), '25 May 1976')
  assert.equal(humanDate('1976-05-25T00:00:00Z'), '25 May 1976')
  assert.equal(humanDate('31/13/1976'), '31/13/1976')
  assert.equal(humanDate('Marital 12 2000'), 'Marital 12 2000')
  assert.equal(humanDate('circa 1976'), 'circa 1976')
  assert.equal(humanDate(''), '')
  assert.equal(humanDate(null), '')
})
