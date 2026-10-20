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
const { api, ApiError } = await loadSource('../src/shared/api/client.ts')
const { workerHealth, canRetryJob, isJobPending } = await loadSource('../src/features/inbox/inbox-status.ts')
const realFetch = globalThis.fetch
afterEach(() => { globalThis.fetch = realFetch })

test('configured processing is never presented as healthy without a recent heartbeat', () => {
  const now = Date.parse('2026-10-20T12:00:00Z')
  assert.equal(workerHealth({ enabled: false, lastHeartbeat: new Date(now).toISOString() }, now).healthy, false)
  assert.equal(workerHealth({ enabled: true, lastHeartbeat: null }, now).healthy, false)
  assert.equal(workerHealth({ enabled: true, lastHeartbeat: 'invalid' }, now).healthy, false)
  assert.equal(workerHealth({ enabled: true, lastHeartbeat: '2026-10-20T11:44:59Z' }, now).healthy, false)
  assert.equal(workerHealth({ enabled: true, lastHeartbeat: '2026-10-20T11:59:00Z' }, now).healthy, true)
})

test('manual retry follows the backend state machine without resubmitting active work', () => {
  for (const status of ['pending', 'processing', 'retry', 'completed']) assert.equal(canRetryJob({ status }), false)
  for (const status of ['failed', 'needs_review', 'ignored']) assert.equal(canRetryJob({ status }), true)
  for (const status of ['pending', 'processing', 'retry']) assert.equal(isJobPending({ status }), true)
  for (const status of ['completed', 'failed', 'needs_review', 'ignored']) assert.equal(isJobPending({ status }), false)
})

test('Inbox gets a dedicated snapshot with cookies and preserves attachment names and inspection summaries', async () => {
  const snapshot = {
    accounts: [], availableAccounts: ['mailbox@example.test'],
    messages: [{ id: 'message', attachments: [{ id: 'attachment', fileName: 'synthetic.pdf', sizeKb: 1, kind: 'pdf' }] }],
    jobs: [{ id: 'job', inspection: [{ resumePages: [2], passportPages: [4], aadhaarPages: [5] }] }],
    worker: { enabled: true, lastHeartbeat: null },
  }
  globalThis.fetch = async (url, options) => {
    assert.equal(url, '/api/inbox')
    assert.equal(options.method, 'GET')
    assert.equal(options.credentials, 'include')
    return Response.json(snapshot)
  }
  assert.deepEqual(await api.inbox.get(), snapshot)
})

test('connecting sends a configuration reference and toggle, never mailbox credentials', async () => {
  globalThis.fetch = async (url, options) => {
    assert.equal(url, '/api/inbox/connect')
    assert.equal(options.method, 'POST')
    assert.deepEqual(JSON.parse(options.body), { auto_ingest: false, address: 'mailbox@example.test' })
    return Response.json({ id: 'account' })
  }
  assert.deepEqual(await api.inbox.connect(false, 'mailbox@example.test'), { id: 'account' })
})

test('manual attachment ingestion, retry and auto-ingest use the agreed resource routes', async () => {
  const requests = []
  globalThis.fetch = async (url, options) => {
    requests.push({ url, method: options.method, body: options.body && JSON.parse(options.body) })
    return Response.json({ status: 'queued' }, { status: 202 })
  }
  await api.inbox.ingest('message/id')
  await api.inbox.retry('job/id')
  await api.inbox.setAutoIngest('account/id', true)
  assert.deepEqual(requests, [
    { url: '/api/inbox/messages/message%2Fid/ingest', method: 'POST', body: undefined },
    { url: '/api/inbox/jobs/job%2Fid/retry', method: 'POST', body: undefined },
    { url: '/api/inbox/accounts/account%2Fid', method: 'PATCH', body: { auto_ingest: true } },
  ])
})

test('missing mailbox configuration is exposed as an actionable error, not success', async () => {
  const message = 'Set IMAP_SERVER and IMAP_USERNAME in backend environment settings.'
  globalThis.fetch = async () => Response.json({ detail: message }, { status: 503 })
  await assert.rejects(api.inbox.connect(true), (error) => {
    assert.ok(error instanceof ApiError)
    assert.equal(error.status, 503)
    assert.equal(error.message, message)
    return true
  })
})

test('dashboard source health uses the small health endpoint', async () => {
  globalThis.fetch = async (url, options) => {
    assert.equal(url, '/api/inbox/health')
    assert.equal(options.method, 'GET')
    return Response.json({ accounts: [], counts: {}, worker: { enabled: false, lastHeartbeat: null } })
  }
  assert.equal((await api.inbox.health()).worker.enabled, false)
})
