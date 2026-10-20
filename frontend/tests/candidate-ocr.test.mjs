import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { afterEach, test } from 'node:test'
import ts from 'typescript'

async function loadSource(path) {
  const source = await readFile(new URL(path, import.meta.url), 'utf8')
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
  })
  return import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`)
}

const { mergeOcrForm, hasOcrExtraction, inspectedPageCount, scanWarnings } = await loadSource('../src/features/candidates/ocr-form.ts')
const { api, ApiError } = await loadSource('../src/shared/api/client.ts')
const defaults = { name: '', phone: '', dob: '', nationality: 'Indian', skills: '', totalExperience: '' }
const scan = (fields, extra = {}) => ({
  fields, warnings: [], raw: { text: 'Synthetic document' }, extraction_status: 'complete', ...extra,
})

test('a late scan keeps human edits, including intentionally cleared fields', async () => {
  const edited = new Set()
  const form = { ...defaults, name: 'Suggested name', phone: 'Suggested number' }
  let finish
  const response = new Promise((resolve) => { finish = resolve })
  const pending = response.then((result) => mergeOcrForm(form, defaults, { resume: result }, edited))
  // Simulate edits after the request was sent, before the response arrives.
  form.name = 'Corrected name'
  form.phone = ''
  edited.add('name')
  edited.add('phone')
  finish(scan({ name: 'Different OCR name', phone: 'Different number', skills: ['Welding', 'Assembly'] }))
  const merged = await pending
  assert.equal(merged.name, 'Corrected name')
  assert.equal(merged.phone, '')
  assert.equal(merged.skills, 'Welding, Assembly')
  assert.equal(form.skills, '') // The helper does not mutate current state.
})

test('passport wins for unedited fields and empty values do not erase useful resume values', () => {
  const merged = mergeOcrForm(defaults, defaults, {
    resume: scan({ name: 'Resume name', phone: 'Resume phone', dob: '1990-01-01', skills: ['Welding'], totalExperience: 0 }),
    passport: scan({ name: 'Passport name', phone: '', dob: '1991-02-03', skills: [] }),
  }, new Set())
  assert.equal(merged.name, 'Passport name')
  assert.equal(merged.phone, 'Resume phone')
  assert.equal(merged.dob, '1991-02-03')
  assert.equal(merged.skills, 'Welding')
  assert.equal(merged.totalExperience, '0')
})

test('replacing a document clears only its stale automatic values and retains other document and human values', () => {
  const current = { ...defaults, name: 'Passport name', phone: 'Manual phone', skills: 'Old resume skills' }
  const merged = mergeOcrForm(current, defaults, {
    passport: scan({ name: 'Passport name' }),
  }, new Set(['phone']))
  assert.equal(merged.name, 'Passport name')
  assert.equal(merged.phone, 'Manual phone')
  assert.equal(merged.skills, '')
})

test('review-required or no-resume responses never apply or persist partial extracted fields', () => {
  for (const extraction_status of ['needs_review', 'no_resume']) {
    const result = scan({ name: 'Partial name' }, { extraction_status })
    assert.equal(hasOcrExtraction(result), false)
    assert.deepEqual(mergeOcrForm(defaults, defaults, { resume: result }, new Set()), defaults)
  }
})

test('empty raw data and an inspection-only record are not valid scans', () => {
  for (const raw of [{}, { local_inspection: { page_count: 1 } }]) {
    const result = scan({ name: 'Not an extracted name' }, { raw })
    assert.equal(hasOcrExtraction(result), false)
    assert.deepEqual(mergeOcrForm(defaults, defaults, { resume: result }, new Set()), defaults)
  }
  assert.equal(hasOcrExtraction(scan({ name: 'Older response' }, { extraction_status: undefined })), true)
})

test('warnings remain available when inspection prevents extraction, without duplicate messages', () => {
  const result = scan({}, {
    raw: {},
    extraction_status: 'needs_review',
    warnings: ['Page 2 is unread.', 'Nothing was sent for extraction.'],
    inspection: { warnings: ['Page 2 is unread.', 'Page 3 has uncertain type.'] },
  })
  assert.deepEqual(scanWarnings(result), [
    'Page 2 is unread.', 'Nothing was sent for extraction.', 'Page 3 has uncertain type.',
  ])
})

test('pages skipped after an inspection deadline or limit are not counted as inspected', () => {
  assert.equal(inspectedPageCount({
    page_count: 4,
    pages: [
      { page_number: 1, status: 'read', method: 'native_text' },
      { page_number: 2, status: 'unread', method: 'failed' },
      { page_number: 3, status: 'blank', method: 'rendered_blank' },
      { page_number: 4, status: 'unread', method: 'not_processed' },
    ],
  }), 3)
})

const realFetch = globalThis.fetch
const realSetTimeout = globalThis.setTimeout
const realClearTimeout = globalThis.clearTimeout
afterEach(() => {
  globalThis.fetch = realFetch
  globalThis.setTimeout = realSetTimeout
  globalThis.clearTimeout = realClearTimeout
})

test('OCR preserves inspection-only responses and uses the 20-minute deadline with cookies', async () => {
  const result = {
    fields: {}, raw: {}, warnings: ['No resume was found.'], extraction_status: 'no_resume',
    inspection: { page_count: 1, resume_pages: [], unread_pages: [], unknown_pages: [], pages: [], warnings: [] },
  }
  let cleared = false
  globalThis.setTimeout = (_callback, delay) => {
    assert.equal(delay, 20 * 60_000)
    return 17
  }
  globalThis.clearTimeout = (handle) => { assert.equal(handle, 17); cleared = true }
  globalThis.fetch = async (url, options) => {
    assert.equal(url, '/api/ocr/resume')
    assert.equal(options.credentials, 'include')
    assert.equal(options.method, 'POST')
    assert.equal(options.body.get('file').name, 'synthetic.txt')
    assert.ok(options.signal instanceof AbortSignal)
    return Response.json(result)
  }
  assert.deepEqual(await api.ocr('resume', new File(['test'], 'synthetic.txt')), result)
  assert.equal(cleared, true)
})

test('OCR deadline also aborts a stalled response body and explains manual entry', async () => {
  let expire
  let cleared = false
  globalThis.setTimeout = (callback, delay) => {
    assert.equal(delay, 1_200_000)
    expire = callback
    return 23
  }
  globalThis.clearTimeout = (handle) => { assert.equal(handle, 23); cleared = true }
  globalThis.fetch = async (_url, { signal }) => ({
    ok: true,
    status: 200,
    text: () => new Promise((_resolve, reject) => {
      signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')))
    }),
  })
  const response = api.ocr('resume', new File(['test'], 'synthetic.txt'))
  await Promise.resolve()
  expire()
  await assert.rejects(response, (error) => {
    assert.ok(error instanceof ApiError)
    assert.match(error.message, /exceeded 20 minutes/)
    assert.match(error.message, /manually/)
    return true
  })
  assert.equal(cleared, true)
})

test('leaving or cancelling OCR aborts its request without a misleading network error', async () => {
  const controller = new AbortController()
  globalThis.fetch = (_url, { signal }) => new Promise((_resolve, reject) => {
    signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')))
  })
  const response = api.ocr('passport', new File(['test'], 'synthetic.txt'), { signal: controller.signal })
  controller.abort()
  await assert.rejects(response, { name: 'AbortError' })
})

test('a normal attachment upload does not acquire the OCR deadline', async () => {
  globalThis.setTimeout = () => assert.fail('The OCR deadline must not change ordinary file uploads')
  globalThis.fetch = async (url) => {
    assert.equal(url, '/api/files')
    return Response.json({ id: 'synthetic-attachment' })
  }
  assert.deepEqual(await api.upload('candidate', 'synthetic-id', 'resume', new File(['test'], 'synthetic.txt')), {
    id: 'synthetic-attachment',
  })
})
