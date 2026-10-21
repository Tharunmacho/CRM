import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'

const source = await readFile(new URL('../src/shared/lib/document-files.ts', import.meta.url), 'utf8')
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
})
const { documentFileError, MAX_DOCUMENT_BYTES, newestFileVersions, fileDownloadPath, applicationFileSlot, applicationFileVersions } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`)

test('document validation rejects empty, oversized, and unsupported selections before upload', () => {
  assert.match(documentFileError({ name: 'scan.pdf', size: 0 }), /empty/)
  assert.match(documentFileError({ name: 'scan.pdf', size: MAX_DOCUMENT_BYTES + 1 }), /25 MB/)
  assert.match(documentFileError({ name: 'scan.pdf.exe', size: 100 }), /Choose a PDF/)
  assert.equal(documentFileError({ name: 'RESUME.DOCX', size: MAX_DOCUMENT_BYTES }), undefined)
  for (const extension of ['pdf', 'doc', 'jpg', 'jpeg', 'png', 'webp', 'heic']) {
    assert.equal(documentFileError({ name: `scan.${extension}`, size: 1 }), undefined)
  }
})

test('history selects the newest version independently of API ordering and preserves its input', () => {
  const versions = [
    { id: 'v2', version: 2, uploadedAt: '2026-10-21T09:00:00Z' },
    { id: 'v10', version: 10, uploadedAt: '2026-10-21T10:00:00Z' },
    { id: 'v1', version: 1, uploadedAt: '2026-10-20T09:00:00Z' },
  ]
  assert.deepEqual(newestFileVersions(versions).map((file) => file.id), ['v10', 'v2', 'v1'])
  assert.deepEqual(versions.map((file) => file.id), ['v2', 'v10', 'v1'])
  assert.deepEqual(newestFileVersions([]), [])
})

test('download links opt in to attachment delivery without losing existing query or fragment', () => {
  assert.equal(fileDownloadPath('/api/files/one'), '/api/files/one?download=true')
  assert.equal(fileDownloadPath('/api/files/one?download=false&version=2#page=1'), '/api/files/one?download=true&version=2#page=1')
  assert.equal(fileDownloadPath(fileDownloadPath('/api/files/one')), '/api/files/one?download=true')
})

test('offer and ticket histories stay separate even though both stages call the field file', () => {
  const files = [
    { id: 'old-offer', slot: 'file' },
    { id: 'unattributed-legacy', slot: 'file' },
    { id: 'old-ticket', slot: 'file' },
    { id: 'offer-v2', slot: applicationFileSlot(5, 'file') },
    { id: 'ticket-v2', slot: applicationFileSlot(11, 'file') },
  ]
  const app = { currentStage: 11, draft: { file: { id: 'old-ticket' } }, stages: [{ stage: 5, data: { file: { id: 'old-offer' } } }] }
  assert.deepEqual(applicationFileVersions(files, app, 5, 'file').map((file) => file.id), ['old-offer', 'offer-v2'])
  assert.deepEqual(applicationFileVersions(files, app, 11, 'file').map((file) => file.id), ['old-ticket', 'ticket-v2'])
})
