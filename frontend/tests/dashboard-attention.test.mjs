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

const { attentionDetail, attentionReason, dataBankAttention, hiddenCount, openPath, retryPath } =
  await loadSource('../src/features/dashboard/attention.ts')
const { api } = await loadSource('../src/shared/api/client.ts')

const realFetch = globalThis.fetch
afterEach(() => { globalThis.fetch = realFetch })

test('each source retries through its own existing route, with the id encoded', () => {
  assert.equal(retryPath({ source: 'email', jobId: 'job-1' }), '/inbox/jobs/job-1/retry')
  assert.equal(retryPath({ source: 'whatsapp', jobId: 'wa 1' }), '/whatsapp/messages/wa%201/retry')
  assert.equal(retryPath({ source: 'manual', jobId: 'doc/1' }), '/ocr/jobs/doc%2F1/retry')
})

test('open links prefer the candidate, then the source message, and never invent a private draft link', () => {
  assert.equal(openPath({ source: 'email', messageId: 'm1', candidateId: 'c1' }), '/candidates/c1')
  assert.equal(openPath({ source: 'email', messageId: 'm 1', candidateId: null }), '/inbox?message=m%201')
  assert.equal(openPath({ source: 'whatsapp', messageId: 'w1', candidateId: null }), '/whatsapp')
  assert.equal(openPath({ source: 'manual', messageId: null, candidateId: null }), null)
  assert.equal(openPath({ source: 'manual', messageId: null, candidateId: 'c9' }), '/candidates/c9')
})

test('reasons read as one short line and never repeat the status', () => {
  assert.equal(attentionReason({ status: 'failed', reason: 'Document processing failed.' }), 'Failed · Document processing failed.')
  assert.equal(attentionReason({ status: 'needs_review', reason: 'Pages need review.' }), 'Needs review · Pages need review.')
  assert.equal(attentionReason({ status: 'failed', reason: 'Processing failed' }), 'Failed')
  assert.equal(attentionReason({ status: 'needs_review', reason: 'Needs review' }), 'Needs review')
  assert.equal(attentionReason({ status: 'failed', reason: '  ' }), 'Failed')
})

test('detail lists files, sender and channel, shortening long attachment lists', () => {
  assert.equal(attentionDetail({ files: ['cv.pdf'], sender: 'Asha', channel: 'jobs@example.test' }), 'cv.pdf · Asha · jobs@example.test')
  assert.equal(attentionDetail({ files: [], sender: 'Ravi', channel: '' }), 'Ravi')
  assert.equal(attentionDetail({ files: ['a.pdf', 'b.pdf', 'c.pdf', 'd.pdf'], sender: '', channel: 'WhatsApp' }), 'a.pdf, b.pdf +2 more · WhatsApp')
})

test('hidden rows are only reported when the capped list is shorter than the true total', () => {
  assert.equal(hiddenCount(20, 36), 16)
  assert.equal(hiddenCount(3, 3), 0)
  assert.equal(hiddenCount(5, 2), 0)
})

test('the Data Bank badge counts review-flagged and unassigned candidates once each', () => {
  assert.equal(dataBankAttention([]), 0)
  assert.equal(dataBankAttention([
    { status: 'new', ownerId: 'u1' },
    { status: 'needs_review', ownerId: 'u1' },
    { status: 'new', needsReview: true, ownerId: 'u1' },
    { status: 'new', ownerId: null },
    { status: 'needs_review', needsReview: true, ownerId: null },
    { status: 'deployed', ownerId: 'u2' },
  ]), 4)
})

test('the unread badge and attention list use their dedicated lightweight endpoints', async () => {
  const urls = []
  globalThis.fetch = async (url, options) => {
    urls.push([url, options.method])
    return Response.json({ total: 3, accounts: { a: 3 }, items: [], totals: { all: 0 }, limit: 20, canRetry: false })
  }
  assert.deepEqual(await api.inbox.unreadCount(), { total: 3, accounts: { a: 3 }, items: [], totals: { all: 0 }, limit: 20, canRetry: false })
  await api.inbox.attention()
  assert.deepEqual(urls, [['/api/inbox/unread-count', 'GET'], ['/api/inbox/attention', 'GET']])
})
